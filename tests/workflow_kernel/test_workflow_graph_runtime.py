from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from itertools import count
from pathlib import Path
from typing import Any, Callable

import pytest

import workflow_kernel.workflow_execution_event_ledger as execution_ledger
import workflow_kernel.workflow_graph_runtime as graph_runtime
from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_graph_runtime import (
    LocalExecutionGrant,
    RuntimeExecutionSession,
    RuntimeTaskBinding,
    WorkflowGraphRuntimeError,
    derive_runtime_apply_approval_digest_v1,
    derive_runtime_binding_digest_v1,
    execute_workflow_plan_v1,
)
from workflow_kernel.workflow_execution_event_ledger import (
    WorkflowExecutionLedgerSink,
    read_workflow_execution_events,
    replay_workflow_execution_events_v1,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-definition-v1"
    / "cases.json"
)
_GRANT_IDS = count()


def _base_definition() -> dict[str, Any]:
    return copy.deepcopy(json.loads(FIXTURE_PATH.read_text())[0]["value"])


def _task(
    task_id: str,
    role: str,
    deps: list[str],
    *,
    owner: str | None = None,
    verifies: list[str] | None = None,
    side_effect: str = "local_read",
    capabilities: list[str] | None = None,
    writes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": task_id.replace("_", " ").title(),
        "role": role,
        "owner": owner or f"owner-{task_id}",
        "depends_on": deps,
        "inputs": [f"input-{task_id}"],
        "outputs": [f"output-{task_id}"],
        "writes": writes or [],
        "authority": {
            "side_effect": side_effect,
            "capabilities": capabilities or ["read_file"],
        },
        "acceptance": [
            {
                "id": f"accept-{task_id}",
                "claim": f"{task_id} completed with evidence.",
                "evidence_required": ["file"],
            }
        ],
        "verifies": verifies or [],
    }


def _definition(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    definition = _base_definition()
    definition["tasks"] = tasks
    return definition


def _bindings(
    definition: dict[str, Any],
    **overrides: dict[str, Any],
) -> list[RuntimeTaskBinding]:
    result = []
    for task in definition["tasks"]:
        values = {
            "task_id": task["id"],
            "runner_key": task["id"],
            "condition_key": None,
            "reducer": "mapping",
            "join_policy": "require_success",
            "model_tier": "none",
            "isolation": "in_process",
        }
        values.update(overrides.get(task["id"], {}))
        result.append(RuntimeTaskBinding(**values))
    return result


def _grant(plan: dict[str, Any], *, max_workers: int = 4) -> LocalExecutionGrant:
    return LocalExecutionGrant(
        grant_id=f"grant-test-{next(_GRANT_IDS)}",
        plan_digest=plan["plan_digest"],
        definition_digest=plan["definition_digest"],
        allowed_capabilities=frozenset({"read_file", "terminal", "write_file"}),
        max_side_effect="local_write",
        max_workers=max_workers,
    )


def _run(
    definition: dict[str, Any],
    runners: dict[str, Callable[..., Any]],
    *,
    bindings: list[RuntimeTaskBinding] | None = None,
    conditions: dict[str, Callable[..., bool]] | None = None,
    grant: LocalExecutionGrant | None = None,
) -> dict[str, Any]:
    plan = compile_workflow_definition_v1(definition)
    return execute_workflow_plan_v1(
        plan,
        definition,
        bindings or _bindings(definition),
        runners,
        conditions=conditions or {},
        grant=grant or _grant(plan),
    )


def test_public_kernel_exports_unified_graph_runtime() -> None:
    from workflow_kernel import CAPABILITIES, execute_workflow_plan_v1 as public_execute

    assert public_execute is execute_workflow_plan_v1
    assert "workflow_execution_plan_local_graph_runtime" in CAPABILITIES
    assert "dependency_driven_scheduler" in CAPABILITIES


def test_dependency_driven_scheduler_releases_downstream_without_global_wave_barrier() -> None:
    definition = _definition(
        [
            _task("slow", "explore", []),
            _task("fast", "explore", []),
            _task("synth", "plan", ["fast"]),
            _task("implement", "execute", ["synth"], writes=["artifact"]),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    slow_started = threading.Event()
    release_slow = threading.Event()
    synth_started = threading.Event()
    synth_visible_outputs: list[set[str]] = []

    def slow(_ctx: Any) -> str:
        slow_started.set()
        assert release_slow.wait(5)
        return "slow"

    def synth(ctx: Any) -> str:
        synth_visible_outputs.append(set(ctx.outputs))
        synth_started.set()
        return f"synth:{ctx.reduced_input['fast']}"

    runners = {
        "slow": slow,
        "fast": lambda _ctx: "fast",
        "synth": synth,
        "implement": lambda ctx: f"built:{ctx.reduced_input['synth']}",
        "verify": lambda ctx: ctx.reduced_input["implement"].startswith("built:"),
    }
    with ThreadPoolExecutor(max_workers=1) as outer:
        future = outer.submit(_run, definition, runners)
        assert slow_started.wait(5)
        assert synth_started.wait(5), "synth must not wait for unrelated slow branch"
        release_slow.set()
        receipt = future.result(timeout=5)

    assert receipt["core"]["status"] == "success"
    assert synth_visible_outputs == [{"fast"}]
    assert receipt["core"]["nodes"]["implement"]["accepted"] is True
    assert receipt["timing"]["max_in_flight"] >= 2


def test_deterministic_reduce_and_receipt_core_ignore_completion_order() -> None:
    definition = _definition(
        [
            _task("left", "explore", []),
            _task("right", "explore", []),
            _task("join", "plan", ["left", "right"]),
            _task("implement", "execute", ["join"], writes=["artifact"]),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )

    def one_run(first: str) -> tuple[dict[str, Any], Any]:
        started = {name: threading.Event() for name in ("left", "right")}
        release = {name: threading.Event() for name in ("left", "right")}
        finished = {name: threading.Event() for name in ("left", "right")}
        seen: list[Any] = []

        def branch(name: str) -> Callable[..., str]:
            def runner(_ctx: Any) -> str:
                started[name].set()
                assert release[name].wait(5)
                finished[name].set()
                return name.upper()

            return runner

        def join(ctx: Any) -> Any:
            seen.append(ctx.reduced_input)
            return ctx.reduced_input

        runners = {
            "left": branch("left"),
            "right": branch("right"),
            "join": join,
            "implement": lambda ctx: ctx.reduced_input[0] + ctx.reduced_input[1],
            "verify": lambda _ctx: True,
        }
        bindings = _bindings(
            definition,
            join={"reducer": "list"},
            implement={"reducer": "first"},
        )
        with ThreadPoolExecutor(max_workers=1) as outer:
            future = outer.submit(_run, definition, runners, bindings=bindings)
            assert started["left"].wait(5) and started["right"].wait(5)
            second = "right" if first == "left" else "left"
            release[first].set()
            assert finished[first].wait(5), "force the first completion order"
            release[second].set()
            receipt = future.result(timeout=5)
        return receipt, seen[0]

    first_receipt, first_input = one_run("left")
    second_receipt, second_input = one_run("right")
    assert first_input == second_input == ["LEFT", "RIGHT"]
    first_core = copy.deepcopy(first_receipt["core"])
    second_core = copy.deepcopy(second_receipt["core"])
    first_core.pop("grant_id")
    second_core.pop("grant_id")
    assert first_core == second_core


def test_conditional_skip_skips_verifier_and_allow_skipped_join_runs() -> None:
    definition = _definition(
        [
            _task("required", "explore", []),
            _task("optional", "execute", [], writes=["optional-artifact"]),
            _task("verify_optional", "verify", ["optional"], verifies=["optional"]),
            _task("join", "plan", ["required", "optional"]),
            _task("implement", "execute", ["join"], writes=["artifact"]),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    forbidden_calls: list[str] = []
    runners = {
        "required": lambda _ctx: "required-value",
        "optional": lambda _ctx: forbidden_calls.append("optional"),
        "verify_optional": lambda _ctx: forbidden_calls.append("verify_optional"),
        "join": lambda ctx: dict(ctx.reduced_input),
        "implement": lambda ctx: ctx.reduced_input["join"],
        "verify": lambda _ctx: True,
    }
    bindings = _bindings(
        definition,
        optional={"condition_key": "take_optional"},
        join={"join_policy": "allow_skipped"},
    )
    receipt = _run(
        definition,
        runners,
        bindings=bindings,
        conditions={"take_optional": lambda _ctx: False},
    )

    assert forbidden_calls == []
    nodes = receipt["core"]["nodes"]
    assert nodes["optional"]["status"] == "skipped"
    assert nodes["verify_optional"]["status"] == "skipped"
    assert nodes["join"]["output"] == {"required": "required-value"}
    assert nodes["implement"]["accepted"] is True


def test_require_success_propagates_skip_without_invoking_dependants() -> None:
    definition = _definition(
        [
            _task("choice", "explore", []),
            _task("join", "plan", ["choice"]),
            _task("implement", "execute", ["join"], writes=["artifact"]),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    called: list[str] = []
    runners = {task["id"]: (lambda _ctx, task_id=task["id"]: called.append(task_id)) for task in definition["tasks"]}
    bindings = _bindings(definition, choice={"condition_key": "selected"})
    receipt = _run(
        definition,
        runners,
        bindings=bindings,
        conditions={"selected": lambda _ctx: False},
    )

    assert called == []
    assert {node["status"] for node in receipt["core"]["nodes"].values()} == {"skipped"}
    assert receipt["core"]["status"] == "failed"


def test_verifier_failure_keeps_output_unaccepted_blocks_dependants_and_continues_independent_chain() -> None:
    definition = _definition(
        [
            _task("independent", "explore", []),
            _task("implement", "execute", [], writes=["artifact"]),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
            _task("publish_candidate", "plan", ["implement"]),
        ]
    )
    independent_done = threading.Event()
    downstream_calls: list[str] = []
    runners = {
        "independent": lambda _ctx: independent_done.set() or "independent",
        "implement": lambda _ctx: "candidate",
        "verify": lambda _ctx: False,
        "publish_candidate": lambda _ctx: downstream_calls.append("publish"),
    }
    receipt = _run(definition, runners)

    nodes = receipt["core"]["nodes"]
    assert independent_done.is_set()
    assert downstream_calls == []
    assert nodes["independent"]["status"] == "achieved"
    assert nodes["implement"]["status"] == "failed"
    assert nodes["implement"]["output"] == "candidate"
    assert nodes["implement"]["accepted"] is False
    assert nodes["verify"]["status"] == "failed"
    assert nodes["publish_candidate"]["status"] == "blocked"
    assert receipt["core"]["status"] == "failed"


def test_admission_rejects_digest_mutation_grant_replay_and_missing_runner_before_execution() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    calls: list[str] = []
    runners = {task["id"]: (lambda _ctx, task_id=task["id"]: calls.append(task_id) or True) for task in definition["tasks"]}
    bindings = _bindings(definition)

    mutated = copy.deepcopy(plan)
    mutated["waves"][0]["task_ids"].append("plan")
    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(mutated, definition, bindings, runners, grant=_grant(plan))
    assert exc.value.code == "plan_digest_mismatch"
    assert calls == []

    grant = _grant(plan)
    with pytest.raises(FrozenInstanceError):
        grant.max_workers = 8
    assert copy.copy(grant) is grant
    assert copy.deepcopy(grant) is grant
    execute_workflow_plan_v1(plan, definition, bindings, runners, grant=grant)
    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(plan, definition, bindings, runners, grant=grant)
    assert exc.value.code == "grant_already_consumed"

    missing = dict(runners)
    missing.pop("verify")
    calls_before_missing_runner = list(calls)
    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(plan, definition, bindings, missing, grant=_grant(plan))
    assert exc.value.code == "runner_missing"
    assert calls == calls_before_missing_runner


def test_admission_rejects_external_side_effect_and_unsupported_runtime_policy() -> None:
    definition = _base_definition()
    definition["authority"]["max_side_effect"] = "external_write"
    definition["tasks"][2]["authority"]["side_effect"] = "external_write"
    plan = compile_workflow_definition_v1(definition)
    calls: list[str] = []
    runners = {task["id"]: (lambda _ctx, task_id=task["id"]: calls.append(task_id) or True) for task in definition["tasks"]}
    elevated_grant = LocalExecutionGrant(
        grant_id=f"external-{next(_GRANT_IDS)}",
        plan_digest=plan["plan_digest"],
        definition_digest=plan["definition_digest"],
        allowed_capabilities=frozenset({"read_file", "terminal", "write_file"}),
        max_side_effect="external_write",
    )
    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            _bindings(definition),
            runners,
            grant=elevated_grant,
        )
    assert exc.value.code == "invalid_grant"
    assert calls == []
    assert elevated_grant.consumed is False

    local_definition = _base_definition()
    local_plan = compile_workflow_definition_v1(local_definition)
    bad_bindings = _bindings(local_definition, implement={"isolation": "worktree"})
    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            local_plan,
            local_definition,
            bad_bindings,
            runners,
            grant=_grant(local_plan),
        )
    assert exc.value.code == "isolation_unsupported"
    assert calls == []


def test_shared_verifier_ignores_skipped_target_but_accepts_active_target() -> None:
    definition = _definition(
        [
            _task("optional", "execute", [], writes=["optional-artifact"]),
            _task("active", "execute", [], writes=["active-artifact"]),
            _task(
                "verify_both",
                "verify",
                ["optional", "active"],
                verifies=["optional", "active"],
            ),
        ]
    )
    calls: list[str] = []
    runners = {
        "optional": lambda _ctx: calls.append("optional"),
        "active": lambda _ctx: "active-candidate",
        "verify_both": lambda ctx: ctx.reduced_input == {"active": "active-candidate"},
    }
    receipt = _run(
        definition,
        runners,
        bindings=_bindings(
            definition,
            optional={"condition_key": "take_optional"},
        ),
        conditions={"take_optional": lambda _ctx: False},
    )

    assert calls == []
    nodes = receipt["core"]["nodes"]
    assert nodes["optional"]["status"] == "skipped"
    assert nodes["active"]["status"] == "achieved"
    assert nodes["active"]["accepted"] is True
    assert nodes["verify_both"]["status"] == "achieved"


def test_shared_verifier_failure_fails_other_pending_targets_without_deadlock() -> None:
    definition = _definition(
        [
            _task("broken", "execute", [], writes=["broken-artifact"]),
            _task("candidate", "execute", [], writes=["candidate-artifact"]),
            _task(
                "verify_both",
                "verify",
                ["broken", "candidate"],
                verifies=["broken", "candidate"],
            ),
        ]
    )
    verifier_calls: list[str] = []

    def fail_runner(_ctx: Any) -> Any:
        raise RuntimeError("build failed")

    receipt = _run(
        definition,
        {
            "broken": fail_runner,
            "candidate": lambda _ctx: "candidate",
            "verify_both": lambda _ctx: verifier_calls.append("verify"),
        },
    )

    assert verifier_calls == []
    nodes = receipt["core"]["nodes"]
    assert nodes["broken"]["status"] == "failed"
    assert nodes["candidate"]["status"] == "failed"
    assert nodes["candidate"]["accepted"] is False
    assert nodes["verify_both"]["status"] == "blocked"
    assert receipt["core"]["status"] == "failed"


def test_blocked_verifier_prerequisite_fails_target_instead_of_deadlocking() -> None:
    definition = _definition(
        [
            _task("prerequisite", "explore", []),
            _task("implement", "execute", [], writes=["artifact"]),
            _task(
                "verify",
                "verify",
                ["implement", "prerequisite"],
                verifies=["implement"],
            ),
        ]
    )
    verifier_calls: list[str] = []

    def fail_prerequisite(_ctx: Any) -> Any:
        raise RuntimeError("missing evidence")

    receipt = _run(
        definition,
        {
            "prerequisite": fail_prerequisite,
            "implement": lambda _ctx: "candidate",
            "verify": lambda _ctx: verifier_calls.append("verify"),
        },
    )

    assert verifier_calls == []
    nodes = receipt["core"]["nodes"]
    assert nodes["prerequisite"]["status"] == "failed"
    assert nodes["verify"]["status"] == "blocked"
    assert nodes["implement"]["status"] == "failed"
    assert nodes["implement"]["accepted"] is False
    assert receipt["core"]["status"] == "failed"


def test_grant_replay_is_rejected_while_first_execution_is_in_flight() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    grant = _grant(plan)
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []

    def inspect(_ctx: Any) -> str:
        calls.append("inspect")
        first_started.set()
        assert release_first.wait(5)
        return "inventory"

    runners = {
        "inspect": inspect,
        "plan": lambda _ctx: "plan",
        "implement": lambda _ctx: "candidate",
        "verify": lambda _ctx: True,
    }
    bindings = _bindings(definition)
    with ThreadPoolExecutor(max_workers=2) as outer:
        first = outer.submit(
            execute_workflow_plan_v1,
            plan,
            definition,
            bindings,
            runners,
            grant=grant,
        )
        assert first_started.wait(5)
        second = outer.submit(
            execute_workflow_plan_v1,
            plan,
            definition,
            bindings,
            runners,
            grant=grant,
        )
        with pytest.raises(WorkflowGraphRuntimeError) as exc:
            second.result(timeout=5)
        assert exc.value.code == "grant_already_consumed"
        release_first.set()
        assert first.result(timeout=5)["core"]["status"] == "success"

    assert calls == ["inspect"]


def test_runner_inputs_and_outputs_are_json_snapshots_not_shared_mutable_state() -> None:
    definition = _base_definition()
    retained: dict[str, Any] = {"items": ["original"]}

    def plan(ctx: Any) -> str:
        ctx.reduced_input["inspect"]["items"].append("mutated-by-plan")
        return "plan"

    receipt = _run(
        definition,
        {
            "inspect": lambda _ctx: retained,
            "plan": plan,
            "implement": lambda _ctx: "candidate",
            "verify": lambda _ctx: True,
        },
    )
    retained["items"].append("mutated-after-return")

    assert receipt["core"]["nodes"]["inspect"]["output"] == {
        "items": ["original"]
    }


def test_non_finite_runner_output_fails_closed_as_non_json() -> None:
    definition = _base_definition()
    receipt = _run(
        definition,
        {
            "inspect": lambda _ctx: float("nan"),
            "plan": lambda _ctx: "plan",
            "implement": lambda _ctx: "candidate",
            "verify": lambda _ctx: True,
        },
    )

    inspect = receipt["core"]["nodes"]["inspect"]
    assert inspect["status"] == "failed"
    assert "runner_output_not_json" in inspect["failure"]
    assert "output" not in inspect
    assert receipt["core"]["status"] == "failed"


def test_output_snapshot_toctou_is_converted_to_structured_node_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = _base_definition()
    shared: dict[str, Any] = {"items": ["stable"]}
    validated = threading.Event()
    mutated = threading.Event()
    original_ensure = graph_runtime._ensure_json_value

    def ensure_then_allow_mutation(value: Any) -> None:
        original_ensure(value)
        validated.set()
        assert mutated.wait(timeout=2)

    monkeypatch.setattr(graph_runtime, "_ensure_json_value", ensure_then_allow_mutation)

    def mutate_after_validation() -> None:
        assert validated.wait(timeout=2)
        shared["not_json"] = object()
        mutated.set()

    thread = threading.Thread(target=mutate_after_validation)
    thread.start()
    receipt = _run(
        definition,
        {
            "inspect": lambda _ctx: shared,
            "plan": lambda ctx: ctx.reduced_input,
            "implement": lambda ctx: ctx.reduced_input,
            "verify": lambda _ctx: {"accepted": True},
        },
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert receipt["core"]["status"] == "failed"
    inspect = receipt["core"]["nodes"]["inspect"]
    assert inspect["status"] == "failed"
    assert "runner_output_not_json" in inspect["failure"]


def test_duplicate_logical_grant_id_is_atomically_rejected_process_locally() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    calls: list[str] = []
    lock = threading.Lock()

    def runner(_ctx: Any) -> bool:
        with lock:
            calls.append("called")
        return True

    def duplicate() -> LocalExecutionGrant:
        return LocalExecutionGrant(
            grant_id="duplicate-logical-grant",
            plan_digest=plan["plan_digest"],
            definition_digest=plan["definition_digest"],
            allowed_capabilities=frozenset({"read_file", "terminal", "write_file"}),
            max_side_effect="local_write",
            max_workers=4,
        )

    def execute(grant: LocalExecutionGrant) -> str:
        try:
            execute_workflow_plan_v1(
                plan,
                definition,
                _bindings(definition),
                {task["id"]: runner for task in definition["tasks"]},
                grant=grant,
            )
            return "success"
        except WorkflowGraphRuntimeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [duplicate(), duplicate()]))

    assert sorted(results) == ["grant_already_consumed", "success"]
    assert len(calls) == len(definition["tasks"])


def test_plan_and_runner_registries_are_snapshotted_before_execution() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    original_workflow_ref = copy.deepcopy(plan["workflow_ref"])
    calls: list[str] = []
    plan_finished = threading.Event()
    runners: dict[str, Callable[..., Any]] = {}
    bindings = _bindings(definition)
    plan_binding = next(binding for binding in bindings if binding.task_id == "plan")
    grant = _grant(compile_workflow_definition_v1(definition), max_workers=1)
    original_grant_id = grant.grant_id

    def inspect(_ctx: Any) -> dict[str, bool]:
        next(
            binding
            for binding in plan["task_bindings"]
            if binding["task_id"] == "implement"
        )["depends_on"].clear()
        plan["workflow_ref"]["workflow_id"] = "forged-after-admission"
        runners["plan"] = lambda _ctx: calls.append("swapped") or "swapped"
        plan_binding.__dict__["runner_key"] = "implement"
        grant.__dict__["grant_id"] = "forged-after-admission"
        return {"inspected": True}

    def original_plan(ctx: Any) -> Any:
        calls.append("original-plan")
        plan_finished.set()
        return ctx.reduced_input

    def implement(ctx: Any) -> Any:
        assert plan_finished.is_set(), "execute must retain the admitted dependency"
        calls.append("implement")
        return ctx.reduced_input

    runners.update(
        {
            "inspect": inspect,
            "plan": original_plan,
            "implement": implement,
            "verify": lambda _ctx: {"accepted": True},
        }
    )
    receipt = execute_workflow_plan_v1(
        plan,
        definition,
        bindings,
        runners,
        grant=grant,
    )

    assert calls == ["original-plan", "implement"]
    assert receipt["core"]["workflow_ref"] == original_workflow_ref
    assert receipt["core"]["grant_id"] == original_grant_id
    assert receipt["core"]["status"] == "success"


def test_verifier_acceptance_is_derived_from_saved_plain_json_snapshot() -> None:
    class FalseVerdict(dict[str, bool]):
        def get(self, key: str, default: Any = None) -> Any:
            if key == "accepted":
                return True
            return super().get(key, default)

    definition = _base_definition()
    receipt = _run(
        definition,
        {
            "inspect": lambda _ctx: "facts",
            "plan": lambda ctx: ctx.reduced_input,
            "implement": lambda ctx: ctx.reduced_input,
            "verify": lambda _ctx: FalseVerdict(accepted=False),
        },
    )

    assert receipt["core"]["status"] == "failed"
    assert receipt["core"]["nodes"]["verify"]["accepted"] is False
    assert receipt["core"]["nodes"]["implement"]["accepted"] is False
    output = receipt["core"]["nodes"]["verify"].get("output")
    assert output is None or output == {"accepted": False}


def test_malformed_grant_is_rejected_before_any_runner_and_receipt_work() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    calls: list[str] = []
    grant = LocalExecutionGrant(
        grant_id=object(),  # type: ignore[arg-type]
        plan_digest=plan["plan_digest"],
        definition_digest=plan["definition_digest"],
        allowed_capabilities=frozenset({"read_file", "terminal", "write_file"}),
    )

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            _bindings(definition),
            {
                task["id"]: (lambda _ctx: calls.append("called") or True)
                for task in definition["tasks"]
            },
            grant=grant,
        )

    assert exc.value.code == "invalid_grant"
    assert calls == []
    assert grant.consumed is False


def test_grant_subclass_cannot_swap_identity_between_validation_and_snapshot() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    calls: list[str] = []

    class AlternatingGrant(LocalExecutionGrant):
        reads = 0

        def __getattribute__(self, name: str) -> Any:
            if name == "grant_id":
                reads = object.__getattribute__(self, "reads") + 1
                object.__setattr__(self, "reads", reads)
                if reads >= 4:
                    return "forged receipt id with spaces"
            return super().__getattribute__(name)

    grant = AlternatingGrant(
        grant_id=f"subclass-{next(_GRANT_IDS)}",
        plan_digest=plan["plan_digest"],
        definition_digest=plan["definition_digest"],
        allowed_capabilities=frozenset({"read_file", "terminal", "write_file"}),
    )

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            _bindings(definition),
            {
                task["id"]: (lambda _ctx: calls.append("called") or True)
                for task in definition["tasks"]
            },
            grant=grant,
        )

    assert exc.value.code == "invalid_grant"
    assert calls == []
    assert grant.consumed is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"grant_id": ""},
        {"grant_id": "contains space"},
        {"grant_id": "x" * 129},
        {"plan_digest": "sha256:nope"},
        {"definition_digest": "sha256:" + "A" * 64},
        {"allowed_capabilities": {"read_file"}},
        {"allowed_capabilities": frozenset({""})},
        {"max_side_effect": "payment"},
        {"max_workers": True},
        {"max_workers": 0},
        {"max_workers": 33},
    ],
)
def test_complete_grant_shape_fails_closed_before_consumption(overrides: dict[str, Any]) -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    values: dict[str, Any] = {
        "grant_id": f"shape-{id(overrides)}",
        "plan_digest": plan["plan_digest"],
        "definition_digest": plan["definition_digest"],
        "allowed_capabilities": frozenset({"read_file", "terminal", "write_file"}),
        "max_side_effect": "local_write",
        "max_workers": 1,
    }
    values.update(overrides)
    grant = LocalExecutionGrant(**values)

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            _bindings(definition),
            {task["id"]: (lambda _ctx: True) for task in definition["tasks"]},
            grant=grant,
        )

    assert exc.value.code == "invalid_grant"
    assert grant.consumed is False


def test_non_string_json_object_key_fails_closed_without_coercion() -> None:
    definition = _base_definition()
    receipt = _run(
        definition,
        {
            "inspect": lambda _ctx: {1: "numeric-key"},
            "plan": lambda ctx: ctx.reduced_input,
            "implement": lambda ctx: ctx.reduced_input,
            "verify": lambda _ctx: {"accepted": True},
        },
    )

    inspect = receipt["core"]["nodes"]["inspect"]
    assert receipt["core"]["status"] == "failed"
    assert inspect["status"] == "failed"
    assert "runner_output_not_json" in inspect["failure"]
    assert "output" not in inspect


def test_receipt_core_is_json_roundtrippable_and_preserves_authority_neutral_plan() -> None:
    definition = _base_definition()
    runners = {
        "inspect": lambda _ctx: {"inventory": [1, 2]},
        "plan": lambda ctx: {"plan": ctx.reduced_input},
        "implement": lambda ctx: {"artifact": ctx.reduced_input},
        "verify": lambda _ctx: True,
    }
    receipt = _run(definition, runners)
    encoded = json.dumps(receipt["core"], sort_keys=True, separators=(",", ":"))

    assert json.loads(encoded) == receipt["core"]
    assert receipt["core"]["plan_authority_granted"] is False
    assert receipt["core"]["plan_executable"] is False
    assert receipt["core_digest"].startswith("sha256:")
    assert "started_at" not in receipt["core"]
    assert "events" in receipt["timing"]


def _execution_session(
    definition: dict[str, Any],
    plan: dict[str, Any],
    bindings: list[RuntimeTaskBinding],
    *,
    run_id: str,
    execution_mode: str = "apply",
    policy_digest: str | None = None,
    approval_digest: str | None = None,
) -> RuntimeExecutionSession:
    binding_digest = derive_runtime_binding_digest_v1(bindings)
    selected_policy_digest = policy_digest
    selected_approval_digest = approval_digest
    if execution_mode == "apply" and policy_digest is None and approval_digest is None:
        selected_policy_digest = "sha256:" + "e" * 64
        selected_approval_digest = derive_runtime_apply_approval_digest_v1(
            run_id=run_id,
            workflow_id=definition["workflow_id"],
            plan_digest=plan["plan_digest"],
            binding_digest=binding_digest,
            policy_digest=selected_policy_digest,
        )
    return RuntimeExecutionSession(
        run_id=run_id,
        session_owner="worker-one",
        lease_id=f"lease-{run_id}",
        lease_expires_at="2026-07-28T01:00:00Z",
        fencing_token=7,
        workflow_id=definition["workflow_id"],
        definition_digest=plan["definition_digest"],
        plan_digest=plan["plan_digest"],
        binding_digest=binding_digest,
        execution_mode=execution_mode,
        policy_digest=selected_policy_digest,
        approval_digest=selected_approval_digest,
    )


def test_durable_execution_persists_exact_identity_and_effect_order(tmp_path: Path) -> None:
    definition = _definition(
        [
            _task(
                "implement",
                "execute",
                [],
                side_effect="local_write",
                capabilities=["write_file"],
                writes=["artifact"],
            ),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(
        definition, plan, bindings, run_id="durable-success"
    )
    contexts: list[Any] = []
    ledger_path = tmp_path / "durable-success.jsonl"
    sink = WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path)

    def implement(ctx: Any) -> dict[str, bool]:
        contexts.append(ctx)
        session.__dict__["fencing_token"] = 99
        return {"written": True}

    receipt = execute_workflow_plan_v1(
        plan,
        definition,
        bindings,
        {"implement": implement, "verify": lambda _ctx: True},
        grant=_grant(plan),
        execution_session=session,
        event_sink=sink,
        execution_clock=lambda: "2026-07-28T00:30:00Z",
    )
    events = read_workflow_execution_events(ledger_path, private_root=tmp_path)
    replay = replay_workflow_execution_events_v1(events)

    assert receipt["core"]["status"] == "success"
    assert [event["kind"] for event in events] == [
        "run_admitted",
        "task_started",
        "effect_intent",
        "effect_observed",
        "task_started",
        "task_succeeded",
        "task_succeeded",
        "run_succeeded",
    ]
    assert replay["status"] == "succeeded"
    assert replay["uncertain_effect_ids"] == []
    assert contexts[0].run_id == "durable-success"
    assert contexts[0].effect_id is not None
    assert contexts[0].fencing_token == 7
    assert {event["identity"]["fencing_token"] for event in events} == {7}


def test_durable_sink_failure_before_effect_prevents_runner_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = _definition(
        [
            _task(
                "implement",
                "execute",
                [],
                side_effect="local_write",
                capabilities=["write_file"],
                writes=["artifact"],
            ),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(definition, plan, bindings, run_id="sink-failure")
    observed: list[str] = []
    runner_calls: list[str] = []
    ledger_path = tmp_path / "sink-failure.jsonl"
    sink = WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path)
    real_append = execution_ledger.append_workflow_execution_event

    def rejecting_append(
        path: Path, event: dict[str, Any], *, private_root: Path
    ) -> dict[str, Any]:
        observed.append(event["kind"])
        if event["kind"] == "effect_intent":
            raise OSError("disk unavailable")
        return real_append(path, event, private_root=private_root)

    monkeypatch.setattr(execution_ledger, "append_workflow_execution_event", rejecting_append)

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {
                "implement": lambda _ctx: runner_calls.append("called"),
                "verify": lambda _ctx: True,
            },
            grant=_grant(plan),
            execution_session=session,
            event_sink=sink,
            execution_clock=lambda: "2026-07-28T00:30:00Z",
        )

    assert exc.value.code == "durable_evidence_failed"
    assert observed == ["run_admitted", "task_started", "effect_intent"]
    assert runner_calls == []


def test_noncanonical_execution_sink_fails_before_grant_consumption() -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(definition, plan, bindings, run_id="memory-sink")
    grant = _grant(plan)

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {task["id"]: (lambda _ctx: True) for task in definition["tasks"]},
            grant=grant,
            execution_session=session,
            event_sink=lambda _event: None,
        )

    assert exc.value.code == "invalid_execution_session"
    assert grant.consumed is False


def test_execution_session_mismatch_fails_before_grant_consumption(tmp_path: Path) -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(definition, plan, bindings, run_id="wrong-binding")
    session.__dict__["binding_digest"] = "sha256:" + "0" * 64
    assert session.policy_digest is not None
    session.__dict__["approval_digest"] = derive_runtime_apply_approval_digest_v1(
        run_id=session.run_id,
        workflow_id=session.workflow_id,
        plan_digest=session.plan_digest,
        binding_digest=session.binding_digest,
        policy_digest=session.policy_digest,
    )
    grant = _grant(plan)

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {task["id"]: (lambda _ctx: True) for task in definition["tasks"]},
            grant=grant,
            execution_session=session,
            event_sink=WorkflowExecutionLedgerSink(
                tmp_path / "mismatch.jsonl", private_root=tmp_path
            ),
        )

    assert exc.value.code == "execution_session_mismatch"
    assert grant.consumed is False


def test_failed_effectful_run_preserves_uncertain_effect_evidence(tmp_path: Path) -> None:
    definition = _definition(
        [
            _task(
                "implement",
                "execute",
                [],
                side_effect="local_write",
                capabilities=["write_file"],
                writes=["artifact"],
            ),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(definition, plan, bindings, run_id="durable-failure")
    ledger_path = tmp_path / "durable-failure.jsonl"
    sink = WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path)

    def fail_after_possible_effect(_ctx: Any) -> None:
        raise RuntimeError("uncertain write")

    receipt = execute_workflow_plan_v1(
        plan,
        definition,
        bindings,
        {"implement": fail_after_possible_effect, "verify": lambda _ctx: True},
        grant=_grant(plan),
        execution_session=session,
        event_sink=sink,
        execution_clock=lambda: "2026-07-28T00:30:00Z",
    )
    events = read_workflow_execution_events(ledger_path, private_root=tmp_path)
    replay = replay_workflow_execution_events_v1(events)

    assert receipt["core"]["status"] == "failed"
    assert replay["status"] == "failed"
    assert len(replay["uncertain_effect_ids"]) == 1
    assert events[-1]["kind"] == "run_failed"
    assert events[-1]["evidence_digest"] == receipt["core_digest"]


def test_expired_execution_lease_fails_before_grant_consumption(tmp_path: Path) -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(definition, plan, bindings, run_id="already-stale")
    grant = _grant(plan)
    ledger_path = tmp_path / "already-stale.jsonl"
    sink = WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path)

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {task["id"]: (lambda _ctx: True) for task in definition["tasks"]},
            grant=grant,
            execution_session=session,
            event_sink=sink,
            execution_clock=lambda: "2026-07-28T01:00:00Z",
        )

    assert exc.value.code == "execution_lease_expired"
    assert grant.consumed is False
    assert not ledger_path.exists()


def test_lease_expiry_after_effect_intent_records_stale_uncertain_effect(
    tmp_path: Path,
) -> None:
    definition = _definition(
        [
            _task(
                "implement",
                "execute",
                [],
                side_effect="local_write",
                capabilities=["write_file"],
                writes=["artifact"],
            ),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = _execution_session(definition, plan, bindings, run_id="expires-mid-effect")
    ledger_path = tmp_path / "expires-mid-effect.jsonl"
    sink = WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path)
    clock_reads = count()

    def crossing_clock() -> str:
        if next(clock_reads) < 5:
            return "2026-07-28T00:30:00Z"
        return "2026-07-28T01:00:00Z"

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {"implement": lambda _ctx: {"possibly_written": True}, "verify": lambda _ctx: True},
            grant=_grant(plan),
            execution_session=session,
            event_sink=sink,
            execution_clock=crossing_clock,
        )

    events = read_workflow_execution_events(ledger_path, private_root=tmp_path)
    replay = replay_workflow_execution_events_v1(events)
    assert exc.value.code == "execution_lease_expired"
    assert [event["kind"] for event in events] == [
        "run_admitted",
        "task_started",
        "effect_intent",
        "stale_observed",
    ]
    assert replay["status"] == "stale"
    assert len(replay["uncertain_effect_ids"]) == 1


def _single_write_definition() -> dict[str, Any]:
    return _definition(
        [
            _task(
                "implement",
                "execute",
                [],
                side_effect="local_write",
                capabilities=["write_file"],
                writes=["artifact"],
            ),
            _task("verify", "verify", ["implement"], verifies=["implement"]),
        ]
    )


def test_dry_run_plans_write_without_invoking_effect_or_verifier(tmp_path: Path) -> None:
    definition = _single_write_definition()
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    policy_digest = "sha256:" + "d" * 64
    session = _execution_session(
        definition,
        plan,
        bindings,
        run_id="dry-run-one",
        execution_mode="dry_run",
        policy_digest=policy_digest,
    )
    calls: list[str] = []
    ledger_path = tmp_path / "dry-run-one.jsonl"

    receipt = execute_workflow_plan_v1(
        plan,
        definition,
        bindings,
        {
            "implement": lambda _ctx: calls.append("effect"),
            "verify": lambda _ctx: calls.append("verify") or True,
        },
        grant=_grant(plan),
        execution_session=session,
        event_sink=WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path),
        execution_clock=lambda: "2026-07-28T00:30:00Z",
    )
    events = read_workflow_execution_events(ledger_path, private_root=tmp_path)
    replay = replay_workflow_execution_events_v1(events)

    assert calls == []
    assert receipt["core"]["status"] == "dry_run"
    assert receipt["core"]["nodes"]["implement"]["status"] == "planned"
    assert receipt["core"]["nodes"]["verify"]["status"] == "skipped"
    assert [event["kind"] for event in events] == [
        "run_admitted",
        "effect_planned",
        "task_skipped",
        "run_dry_run_completed",
    ]
    assert replay["status"] == "dry_run"
    assert all(effect["status"] == "planned" for effect in replay["effects"].values())
    assert events[0]["identity"]["policy_digest"] == policy_digest
    assert events[0]["identity"]["approval_digest"] is None


def test_apply_session_without_policy_bound_approval_fails_before_grant_consumption(
    tmp_path: Path,
) -> None:
    definition = _base_definition()
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    session = replace(
        _execution_session(definition, plan, bindings, run_id="policy-apply-missing"),
        policy_digest=None,
        approval_digest=None,
    )
    grant = _grant(plan)

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {task["id"]: (lambda _ctx: True) for task in definition["tasks"]},
            grant=grant,
            execution_session=session,
            event_sink=WorkflowExecutionLedgerSink(
                tmp_path / "policy-apply-missing.jsonl", private_root=tmp_path
            ),
        )

    assert exc.value.code == "invalid_execution_session"
    assert grant.consumed is False


def test_apply_policy_requires_exact_bound_approval_before_grant_consumption(
    tmp_path: Path,
) -> None:
    definition = _single_write_definition()
    plan = compile_workflow_definition_v1(definition)
    bindings = _bindings(definition)
    policy_digest = "sha256:" + "d" * 64
    grant = _grant(plan)
    rejected_session = _execution_session(
        definition,
        plan,
        bindings,
        run_id="policy-apply-rejected",
        policy_digest=policy_digest,
        approval_digest="sha256:" + "f" * 64,
    )

    with pytest.raises(WorkflowGraphRuntimeError) as exc:
        execute_workflow_plan_v1(
            plan,
            definition,
            bindings,
            {"implement": lambda _ctx: True, "verify": lambda _ctx: True},
            grant=grant,
            execution_session=rejected_session,
            event_sink=WorkflowExecutionLedgerSink(
                tmp_path / "policy-apply-rejected.jsonl", private_root=tmp_path
            ),
            execution_clock=lambda: "2026-07-28T00:30:00Z",
        )

    assert exc.value.code == "invalid_execution_session"
    assert grant.consumed is False

    run_id = "policy-apply-accepted"
    binding_digest = derive_runtime_binding_digest_v1(bindings)
    approval_digest = derive_runtime_apply_approval_digest_v1(
        run_id=run_id,
        workflow_id=definition["workflow_id"],
        plan_digest=plan["plan_digest"],
        binding_digest=binding_digest,
        policy_digest=policy_digest,
    )
    accepted_session = _execution_session(
        definition,
        plan,
        bindings,
        run_id=run_id,
        policy_digest=policy_digest,
        approval_digest=approval_digest,
    )
    ledger_path = tmp_path / "policy-apply-accepted.jsonl"
    receipt = execute_workflow_plan_v1(
        plan,
        definition,
        bindings,
        {"implement": lambda _ctx: {"written": True}, "verify": lambda _ctx: True},
        grant=_grant(plan),
        execution_session=accepted_session,
        event_sink=WorkflowExecutionLedgerSink(ledger_path, private_root=tmp_path),
        execution_clock=lambda: "2026-07-28T00:30:00Z",
    )

    events = read_workflow_execution_events(ledger_path, private_root=tmp_path)
    assert receipt["core"]["status"] == "success"
    assert receipt["core"]["approval_digest"] == approval_digest
    assert events[0]["identity"]["approval_digest"] == approval_digest
