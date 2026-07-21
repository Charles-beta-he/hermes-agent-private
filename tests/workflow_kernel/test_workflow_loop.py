from __future__ import annotations

import copy
import json
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_loop import (
    WorkflowLoopProjectionError,
    apply_workflow_loop_signal_v1,
    create_workflow_loop_snapshot_v1,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-definition-v1"
    / "cases.json"
)


def _definition() -> dict[str, Any]:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(cases[0]["value"])


def _plan(definition: dict[str, Any] | None = None) -> dict[str, Any]:
    return compile_workflow_definition_v1(definition or _definition())


def _signal(
    sequence: int,
    kind: str,
    *,
    task_id: str | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "workflow_loop_signal_v1",
        "signal_id": signal_id or f"signal-{sequence + 1}",
        "kind": kind,
        "expected_sequence": sequence,
        "task_id": task_id,
        "reason": f"Observed {kind}.",
    }


def _apply(
    plan: dict[str, Any],
    snapshot: dict[str, Any],
    kind: str,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    return apply_workflow_loop_signal_v1(
        plan,
        snapshot,
        _signal(snapshot["sequence"], kind, task_id=task_id),
    )


def _task_state(snapshot: dict[str, Any], task_id: str) -> str:
    return next(
        item["state"] for item in snapshot["task_states"] if item["task_id"] == task_id
    )


def test_create_initial_authority_neutral_snapshot() -> None:
    plan = _plan()

    snapshot = create_workflow_loop_snapshot_v1(plan)

    assert snapshot["plan_digest"] == plan["plan_digest"]

    assert snapshot["workflow_state"] == "ready"
    assert snapshot["sequence"] == 0
    assert snapshot["last_signal_id"] is None
    assert snapshot["iteration"] == 1
    assert snapshot["consecutive_failures"] == 0
    assert snapshot["terminal"] is False
    assert snapshot["stop_reason"] is None
    assert snapshot["authority_granted"] is False
    assert snapshot["executable"] is False
    assert _task_state(snapshot, "inspect") == "ready"
    assert _task_state(snapshot, "plan") == "pending"


def test_successful_observed_sequence_unlocks_dependencies_and_closes() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)

    for task_id in ("inspect", "plan", "implement", "verify"):
        snapshot = _apply(plan, snapshot, "task_started_observed", task_id=task_id)
        expected_running = "verifying" if task_id == "verify" else "running"
        assert _task_state(snapshot, task_id) == expected_running
        snapshot = _apply(plan, snapshot, "task_passed_observed", task_id=task_id)

    assert snapshot["workflow_state"] == "passed"
    assert snapshot["terminal"] is True
    assert snapshot["stop_reason"] == "success"
    assert snapshot["continuation_action"] == "stop"
    assert snapshot["sequence"] == 8
    assert snapshot["last_signal_id"] == "signal-8"
    assert all(item["state"] == "passed" for item in snapshot["task_states"])


def test_rejects_start_before_dependencies_are_passed() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        _apply(plan, snapshot, "task_started_observed", task_id="implement")

    assert exc_info.value.code == "task_not_ready"


def test_rejects_stale_sequence_and_duplicate_signal_application() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)
    signal = _signal(0, "task_started_observed", task_id="inspect")
    advanced = apply_workflow_loop_signal_v1(plan, snapshot, signal)

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        apply_workflow_loop_signal_v1(plan, advanced, signal)

    assert exc_info.value.code == "sequence_conflict"


def test_failure_retries_then_stops_at_switch_path_boundary() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)

    snapshot = _apply(plan, snapshot, "task_started_observed", task_id="inspect")
    snapshot = _apply(plan, snapshot, "task_failed_observed", task_id="inspect")
    assert snapshot["iteration"] == 2
    assert snapshot["consecutive_failures"] == 1
    assert snapshot["workflow_state"] == "ready"
    assert snapshot["continuation_action"] == "retry"
    assert _task_state(snapshot, "inspect") == "ready"

    snapshot = _apply(plan, snapshot, "task_started_observed", task_id="inspect")
    snapshot = _apply(plan, snapshot, "task_failed_observed", task_id="inspect")
    assert snapshot["workflow_state"] == "blocked"
    assert snapshot["terminal"] is True
    assert snapshot["stop_reason"] == "blocked"
    assert snapshot["continuation_action"] == "switch_path"
    assert _task_state(snapshot, "inspect") == "failed"


def test_retry_preserves_running_state_of_parallel_lane() -> None:
    definition = _definition()
    tasks = definition["tasks"]
    parallel = copy.deepcopy(next(task for task in tasks if task["id"] == "implement"))
    parallel["id"] = "implement-parallel"
    parallel["owner"] = "executor-parallel"
    parallel["writes"] = ["workflow_kernel/compiler-parallel"]
    parallel["acceptance"][0]["id"] = "parallel-tests"
    tasks.append(parallel)
    verifier = next(task for task in tasks if task["id"] == "verify")
    verifier["depends_on"].append("implement-parallel")
    verifier["verifies"].append("implement-parallel")
    plan = _plan(definition)
    snapshot = create_workflow_loop_snapshot_v1(plan)

    for task_id in ("inspect", "plan"):
        snapshot = _apply(plan, snapshot, "task_started_observed", task_id=task_id)
        snapshot = _apply(plan, snapshot, "task_passed_observed", task_id=task_id)
    snapshot = _apply(
        plan, snapshot, "task_started_observed", task_id="implement-parallel"
    )
    snapshot = _apply(plan, snapshot, "task_started_observed", task_id="implement")

    snapshot = _apply(plan, snapshot, "task_failed_observed", task_id="implement")

    assert _task_state(snapshot, "implement") == "ready"
    assert _task_state(snapshot, "implement-parallel") == "running"
    assert snapshot["workflow_state"] == "running"
    assert snapshot["continuation_action"] == "retry"


@pytest.mark.parametrize(
    ("policy", "expected_state", "expected_reason", "expected_action"),
    [
        ("human_gate", "human_gate", "human_gate", "human_gate"),
        ("terminal_failure", "terminal_failure", "terminal_failure", "stop"),
    ],
)
def test_failure_boundary_honors_declared_policy(
    policy: str,
    expected_state: str,
    expected_reason: str,
    expected_action: str,
) -> None:
    definition = _definition()
    definition["loop_policy"] = {
        "max_iterations": 1,
        "repeated_failure_limit": 1,
        "on_repeated_failure": policy,
    }
    plan = _plan(definition)
    snapshot = create_workflow_loop_snapshot_v1(plan)
    snapshot = _apply(plan, snapshot, "task_started_observed", task_id="inspect")

    snapshot = _apply(plan, snapshot, "task_failed_observed", task_id="inspect")

    assert snapshot["workflow_state"] == expected_state
    assert snapshot["stop_reason"] == expected_reason
    assert snapshot["continuation_action"] == expected_action
    assert snapshot["terminal"] is True


@pytest.mark.parametrize(
    ("kind", "expected_state", "expected_reason"),
    [
        ("blocked_observed", "blocked", "blocked"),
        ("human_gate_observed", "human_gate", "human_gate"),
        ("cancelled_observed", "cancelled", "cancelled"),
        ("context_exhausted_observed", "context_exhausted", "context_exhausted"),
        ("stale_observed", "stale", "stale"),
    ],
)
def test_global_observed_stops_are_legal_terminal_states(
    kind: str, expected_state: str, expected_reason: str
) -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)

    snapshot = _apply(plan, snapshot, kind)

    assert snapshot["workflow_state"] == expected_state
    assert snapshot["stop_reason"] == expected_reason
    assert snapshot["terminal"] is True


def test_rejects_stop_not_declared_by_plan() -> None:
    definition = _definition()
    definition["loop_policy"]["on_repeated_failure"] = "terminal_failure"
    definition["stop_conditions"] = ["success", "terminal_failure"]
    plan = _plan(definition)
    snapshot = create_workflow_loop_snapshot_v1(plan)

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        _apply(plan, snapshot, "cancelled_observed")

    assert exc_info.value.code == "stop_condition_not_declared"


def test_rejects_task_signal_without_task_and_global_signal_with_task() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)

    with pytest.raises(WorkflowLoopProjectionError) as task_exc:
        _apply(plan, snapshot, "task_started_observed")
    assert task_exc.value.code == "task_id_required"

    with pytest.raises(WorkflowLoopProjectionError) as global_exc:
        _apply(plan, snapshot, "blocked_observed", task_id="inspect")
    assert global_exc.value.code == "task_id_forbidden"


def test_rejects_signals_after_terminal_snapshot() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)
    snapshot = _apply(plan, snapshot, "cancelled_observed")

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        _apply(plan, snapshot, "stale_observed")

    assert exc_info.value.code == "workflow_terminal"


def test_projection_does_not_mutate_plan_snapshot_or_signal() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)
    signal = _signal(0, "task_started_observed", task_id="inspect")
    before = (copy.deepcopy(plan), copy.deepcopy(snapshot), copy.deepcopy(signal))

    apply_workflow_loop_signal_v1(plan, snapshot, signal)

    assert (plan, snapshot, signal) == before


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda snapshot: snapshot.__setitem__(
                "definition_digest", "sha256:" + "b" * 64
            ),
            "definition_digest_mismatch",
        ),
        (
            lambda snapshot: snapshot.__setitem__(
                "plan_digest", "sha256:" + "b" * 64
            ),
            "plan_digest_mismatch",
        ),
        (
            lambda snapshot: snapshot["workflow_ref"].__setitem__(
                "workflow_id", "other-workflow"
            ),
            "workflow_ref_mismatch",
        ),
        (
            lambda snapshot: snapshot["task_states"].pop(),
            "task_state_set_mismatch",
        ),
    ],
)
def test_rejects_snapshot_that_does_not_belong_to_plan(
    mutation: Any, expected_code: str
) -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)
    mutation(snapshot)

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        _apply(plan, snapshot, "task_started_observed", task_id="inspect")

    assert exc_info.value.code == expected_code


def test_rejects_tampered_plan_with_stale_digest() -> None:
    plan = _plan()
    next(
        binding
        for binding in plan["task_bindings"]
        if binding["task_id"] == "implement"
    )["depends_on"] = []

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        create_workflow_loop_snapshot_v1(plan)

    assert exc_info.value.code == "plan_digest_mismatch"


def test_rejects_authority_injection_in_snapshot() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)
    snapshot["authority_granted"] = True

    with pytest.raises(WorkflowLoopProjectionError) as exc_info:
        _apply(plan, snapshot, "task_started_observed", task_id="inspect")

    assert exc_info.value.code == "schema_invalid"


def test_snapshot_and_signal_match_packaged_schemas() -> None:
    plan = _plan()
    snapshot = create_workflow_loop_snapshot_v1(plan)
    signal = _signal(0, "task_started_observed", task_id="inspect")

    for name, value in (
        ("workflow-loop-snapshot-v1.schema.json", snapshot),
        ("workflow-loop-signal-v1.schema.json", signal),
    ):
        schema = json.loads(
            resources.files("workflow_kernel.schemas")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(value)
