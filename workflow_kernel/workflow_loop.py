"""Pure authority-neutral WorkflowLoopV1 lifecycle projection.

The projector consumes a compiled WorkflowExecutionPlanV1 and observed local
signals.  It never starts tasks, invokes tools, writes ledgers, grants authority,
or performs adapter calls.  Persistence and execution remain separate control
plane concerns.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Mapping, NoReturn

import jsonschema


_TASK_SIGNAL_KINDS = {
    "task_started_observed",
    "task_passed_observed",
    "task_failed_observed",
}
_GLOBAL_STOP_BY_SIGNAL = {
    "blocked_observed": "blocked",
    "human_gate_observed": "human_gate",
    "cancelled_observed": "cancelled",
    "context_exhausted_observed": "context_exhausted",
    "stale_observed": "stale",
}


@dataclass(frozen=True, slots=True)
class WorkflowLoopProjectionError(ValueError):
    """Stable fail-closed error raised by the loop projector."""

    code: str
    message: str
    path: str = "$"

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"


def _fail(code: str, message: str, path: str = "$") -> NoReturn:
    raise WorkflowLoopProjectionError(code=code, message=message, path=path)


@lru_cache(maxsize=None)
def _schema_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    schema_text = (
        resources.files("workflow_kernel.schemas")
        .joinpath(schema_name)
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _validate_schema(value: Any, schema_name: str) -> None:
    errors = sorted(
        _schema_validator(schema_name).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    _fail("schema_invalid", error.message, path)


def _validate_plan_digest(plan: Mapping[str, Any]) -> None:
    body = {key: value for key, value in plan.items() if key != "plan_digest"}
    payload = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if plan["plan_digest"] != expected:
        _fail(
            "plan_digest_mismatch",
            "execution plan content does not match plan_digest",
            "$.plan_digest",
        )


def _task_bindings(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    bindings: dict[str, Mapping[str, Any]] = {}
    for index, binding in enumerate(plan["task_bindings"]):
        task_id = binding["task_id"]
        if task_id in bindings:
            _fail(
                "duplicate_task_binding",
                f"duplicate task binding {task_id!r}",
                f"$.task_bindings[{index}].task_id",
            )
        bindings[task_id] = binding
    return bindings


def _task_state_map(snapshot: Mapping[str, Any]) -> dict[str, str]:
    states: dict[str, str] = {}
    for index, item in enumerate(snapshot["task_states"]):
        task_id = item["task_id"]
        if task_id in states:
            _fail(
                "duplicate_task_state",
                f"duplicate task state {task_id!r}",
                f"$.task_states[{index}].task_id",
            )
        states[task_id] = item["state"]
    return states


def _validate_snapshot_against_plan(
    plan: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    if snapshot["workflow_ref"] != plan["workflow_ref"]:
        _fail(
            "workflow_ref_mismatch",
            "snapshot workflow_ref does not match execution plan",
            "$.workflow_ref",
        )
    if snapshot["definition_digest"] != plan["definition_digest"]:
        _fail(
            "definition_digest_mismatch",
            "snapshot definition_digest does not match execution plan",
            "$.definition_digest",
        )
    if snapshot["plan_digest"] != plan["plan_digest"]:
        _fail(
            "plan_digest_mismatch",
            "snapshot plan_digest does not match execution plan",
            "$.plan_digest",
        )

    bindings = _task_bindings(plan)
    states = _task_state_map(snapshot)
    if set(states) != set(bindings):
        missing = sorted(set(bindings) - set(states))
        extra = sorted(set(states) - set(bindings))
        _fail(
            "task_state_set_mismatch",
            f"snapshot task states differ from plan; missing={missing}, extra={extra}",
            "$.task_states",
        )
    return bindings, states


def create_workflow_loop_snapshot_v1(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a deterministic non-executable initial projection for a plan."""

    _validate_schema(plan, "workflow-execution-plan-v1.schema.json")
    _validate_plan_digest(plan)
    bindings = _task_bindings(plan)
    snapshot: dict[str, Any] = {
        "schema_version": "workflow_loop_snapshot_v1",
        "workflow_ref": copy.deepcopy(plan["workflow_ref"]),
        "definition_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "sequence": 0,
        "last_signal_id": None,
        "iteration": 1,
        "consecutive_failures": 0,
        "task_states": [
            {
                "task_id": task_id,
                "state": "ready" if not binding["depends_on"] else "pending",
            }
            for task_id, binding in sorted(bindings.items())
        ],
        "workflow_state": "ready",
        "terminal": False,
        "stop_reason": None,
        "continuation_action": "continue",
        "authority_granted": False,
        "executable": False,
    }
    _validate_schema(snapshot, "workflow-loop-snapshot-v1.schema.json")
    return snapshot


def _derive_nonterminal_workflow_state(states: Mapping[str, str]) -> str:
    if "verifying" in states.values():
        return "verifying"
    if "running" in states.values():
        return "running"
    return "ready"


def _unlock_ready_tasks(
    states: dict[str, str], bindings: Mapping[str, Mapping[str, Any]]
) -> None:
    for task_id, binding in sorted(bindings.items()):
        if states[task_id] != "pending":
            continue
        if all(states[dependency] == "passed" for dependency in binding["depends_on"]):
            states[task_id] = "ready"


def _serialize_task_states(states: Mapping[str, str]) -> list[dict[str, str]]:
    return [
        {"task_id": task_id, "state": state}
        for task_id, state in sorted(states.items())
    ]


def apply_workflow_loop_signal_v1(
    plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one observed signal with sequence-CAS and bounded loop semantics."""

    _validate_schema(plan, "workflow-execution-plan-v1.schema.json")
    _validate_plan_digest(plan)
    _validate_schema(snapshot, "workflow-loop-snapshot-v1.schema.json")
    _validate_schema(signal, "workflow-loop-signal-v1.schema.json")
    bindings, states = _validate_snapshot_against_plan(plan, snapshot)

    if signal["expected_sequence"] != snapshot["sequence"]:
        _fail(
            "sequence_conflict",
            f"expected sequence {signal['expected_sequence']} does not match current sequence {snapshot['sequence']}",
            "$.expected_sequence",
        )
    if snapshot["terminal"]:
        _fail("workflow_terminal", "terminal workflow snapshot cannot advance")

    kind = signal["kind"]
    task_id = signal["task_id"]
    if kind in _TASK_SIGNAL_KINDS:
        if task_id is None:
            _fail("task_id_required", f"signal kind {kind!r} requires task_id", "$.task_id")
        if task_id not in bindings:
            _fail("unknown_task_id", f"unknown task id {task_id!r}", "$.task_id")
    elif task_id is not None:
        _fail("task_id_forbidden", f"signal kind {kind!r} forbids task_id", "$.task_id")

    result = copy.deepcopy(dict(snapshot))
    result["sequence"] = snapshot["sequence"] + 1
    result["last_signal_id"] = signal["signal_id"]
    result["continuation_action"] = "continue"
    result["authority_granted"] = False
    result["executable"] = False

    if kind == "task_started_observed":
        assert task_id is not None
        if states[task_id] != "ready":
            _fail(
                "task_not_ready",
                f"task {task_id!r} is {states[task_id]!r}, not ready",
                "$.task_id",
            )
        states[task_id] = (
            "verifying" if bindings[task_id]["role"] == "verify" else "running"
        )
        result["workflow_state"] = _derive_nonterminal_workflow_state(states)

    elif kind == "task_passed_observed":
        assert task_id is not None
        if states[task_id] not in {"running", "verifying"}:
            _fail(
                "task_not_active",
                f"task {task_id!r} cannot pass from state {states[task_id]!r}",
                "$.task_id",
            )
        states[task_id] = "passed"
        result["consecutive_failures"] = 0
        _unlock_ready_tasks(states, bindings)
        if all(state == "passed" for state in states.values()):
            result["workflow_state"] = "passed"
            result["terminal"] = True
            result["stop_reason"] = "success"
            result["continuation_action"] = "stop"
        else:
            result["workflow_state"] = _derive_nonterminal_workflow_state(states)

    elif kind == "task_failed_observed":
        assert task_id is not None
        if states[task_id] not in {"running", "verifying"}:
            _fail(
                "task_not_active",
                f"task {task_id!r} cannot fail from state {states[task_id]!r}",
                "$.task_id",
            )
        failures = snapshot["consecutive_failures"] + 1
        policy = plan["loop_policy"]
        result["consecutive_failures"] = failures
        may_retry = (
            failures < policy["repeated_failure_limit"]
            and snapshot["iteration"] < policy["max_iterations"]
        )
        if may_retry:
            states[task_id] = "ready"
            result["iteration"] = snapshot["iteration"] + 1
            result["workflow_state"] = _derive_nonterminal_workflow_state(states)
            result["continuation_action"] = "retry"
        else:
            states[task_id] = "failed"
            failure_policy = policy["on_repeated_failure"]
            if failure_policy == "switch_path":
                result["workflow_state"] = "blocked"
                result["stop_reason"] = "blocked"
                result["continuation_action"] = "switch_path"
            elif failure_policy == "human_gate":
                result["workflow_state"] = "human_gate"
                result["stop_reason"] = "human_gate"
                result["continuation_action"] = "human_gate"
            else:
                result["workflow_state"] = "terminal_failure"
                result["stop_reason"] = "terminal_failure"
                result["continuation_action"] = "stop"
            result["terminal"] = True

    else:
        stop_reason = _GLOBAL_STOP_BY_SIGNAL[kind]
        if stop_reason not in plan["stop_conditions"]:
            _fail(
                "stop_condition_not_declared",
                f"stop condition {stop_reason!r} is not declared by the execution plan",
                "$.kind",
            )
        result["workflow_state"] = stop_reason
        result["terminal"] = True
        result["stop_reason"] = stop_reason
        result["continuation_action"] = (
            "human_gate" if stop_reason == "human_gate" else "stop"
        )

    result["task_states"] = _serialize_task_states(states)
    _validate_schema(result, "workflow-loop-snapshot-v1.schema.json")
    return result
