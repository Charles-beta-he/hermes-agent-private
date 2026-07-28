"""Prepare explicit, independently admitted compensation runs.

Compensation never mutates or appends to the source execution ledger. A source
failed run with an observed write effect is converted into a new canonical
workflow definition and authority-neutral execution plan. Applying that plan
still requires a fresh LocalExecutionGrant, RuntimeExecutionSession, policy-
bound approval digest, event ledger, runner bindings, and verification.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, NoReturn, Sequence

from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_execution_event_ledger import (
    WorkflowExecutionEventLedgerError,
    read_workflow_execution_events,
    replay_workflow_execution_events_v1,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_ALLOWED_CAPABILITIES = {"read_file", "terminal", "write_file"}


class WorkflowCompensationError(ValueError):
    """Stable fail-closed compensation preparation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowCompensationError(code, message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("compensation_input_not_json", f"input is not deterministic JSON: {type(exc).__name__}")


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _detach_plain_json(value: Any, *, path: str) -> Any:
    """Copy exact built-in JSON values once; reject behavioral containers."""

    if type(value) is dict:
        result: dict[str, Any] = {}
        try:
            items = list(value.items())
        except RuntimeError as exc:
            _fail("compensation_input_invalid", f"{path} changed during snapshot: {type(exc).__name__}")
        for key, item in items:
            if type(key) is not str:
                _fail("compensation_input_invalid", f"{path} contains a non-string key")
            result[key] = _detach_plain_json(item, path=f"{path}.{key}")
        return result
    if type(value) is list:
        try:
            items = list(value)
        except RuntimeError as exc:
            _fail("compensation_input_invalid", f"{path} changed during snapshot: {type(exc).__name__}")
        return [
            _detach_plain_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(items)
        ]
    if value is None or type(value) in {bool, int, float, str}:
        # Canonical serialization below rejects NaN and infinities.
        return value
    _fail("compensation_input_invalid", f"{path} is not exact plain JSON")


def _string_list(name: str, values: Sequence[str], *, allowed: set[str] | None = None) -> list[str]:
    if type(values) not in {list, tuple} or not values:
        _fail("compensation_input_invalid", f"{name} must be a non-empty list or tuple")
    result = list(values)
    if any(type(value) is not str or not value for value in result):
        _fail("compensation_input_invalid", f"{name} contains an invalid string")
    if len(set(result)) != len(result):
        _fail("compensation_input_invalid", f"{name} contains duplicates")
    if allowed is not None and any(value not in allowed for value in result):
        _fail("compensation_input_invalid", f"{name} exceeds the local compensation boundary")
    return sorted(result)


def prepare_workflow_compensation_v1(
    source_receipt: dict[str, Any],
    source_ledger_path: Path,
    *,
    private_root: Path,
    effect_id: str,
    compensation_kind: str,
    capabilities: Sequence[str],
    writes: Sequence[str],
) -> dict[str, Any]:
    """Build a new authority-neutral graph package for one observed failed effect.

    Uncertain effects are deliberately rejected: they require reconciliation,
    not blind compensation. The returned package is not executable authority.
    """

    if type(source_receipt) is not dict:
        _fail("compensation_input_invalid", "source receipt must be an exact dictionary")
    receipt = _detach_plain_json(source_receipt, path="source_receipt")
    try:
        source_events = read_workflow_execution_events(
            source_ledger_path,
            private_root=private_root,
        )
        snapshot = replay_workflow_execution_events_v1(source_events)
    except (OSError, WorkflowExecutionEventLedgerError) as exc:
        _fail(
            "compensation_source_invalid",
            f"durable source ledger is invalid: {type(exc).__name__}",
        )
    try:
        core = receipt["core"]
        core_digest = receipt["core_digest"]
        snapshot_identity = snapshot["identity"]
        effects = snapshot["effects"]
        run_id = snapshot["run_id"]
    except (KeyError, TypeError) as exc:
        _fail("compensation_source_invalid", f"source evidence is incomplete: {type(exc).__name__}")
    if type(core) is not dict or _digest(core) != core_digest:
        _fail("compensation_source_digest_mismatch", "source receipt core digest does not match")
    terminal_event = source_events[-1] if source_events else None
    if (
        type(terminal_event) is not dict
        or terminal_event.get("kind") != "run_failed"
        or terminal_event.get("evidence_digest") != core_digest
    ):
        _fail(
            "compensation_source_terminal_mismatch",
            "failed receipt is not bound to the durable terminal event",
        )
    if core.get("status") != "failed" or snapshot.get("status") != "failed":
        _fail("compensation_source_not_failed", "compensation requires a failed source run")
    if type(effect_id) is not str or re.fullmatch(r"wef_[0-9a-f]{64}", effect_id) is None:
        _fail("compensation_effect_invalid", "effect id is not canonical")
    effect = effects.get(effect_id) if type(effects) is dict else None
    if type(effect) is not dict:
        _fail("compensation_effect_missing", "effect is absent from source evidence")
    if effect.get("status") != "observed":
        _fail("compensation_effect_uncertain", "only an observed effect may be compensated")
    source_task_id = effect.get("task_id")
    nodes = core.get("nodes")
    workflow_ref = core.get("workflow_ref")
    if (
        type(source_task_id) is not str
        or type(nodes) is not dict
        or type(nodes.get(source_task_id)) is not dict
        or nodes[source_task_id].get("status") != "failed"
    ):
        _fail("compensation_task_not_failed", "source effect task is not failed")
    if (
        type(snapshot_identity) is not dict
        or type(workflow_ref) is not dict
        or snapshot_identity.get("workflow_id") != workflow_ref.get("workflow_id")
        or snapshot_identity.get("definition_digest") != core.get("definition_digest")
        or snapshot_identity.get("plan_digest") != core.get("plan_digest")
        or snapshot_identity.get("binding_digest") != core.get("binding_digest")
    ):
        _fail("compensation_source_identity_mismatch", "source receipt and ledger identity differ")
    if type(run_id) is not str or _ID.fullmatch(run_id) is None:
        _fail("compensation_source_invalid", "source run id is invalid")
    if type(compensation_kind) is not str or _ID.fullmatch(compensation_kind) is None:
        _fail("compensation_input_invalid", "compensation kind is invalid")

    selected_capabilities = _string_list(
        "capabilities", capabilities, allowed=_ALLOWED_CAPABILITIES
    )
    if "read_file" not in selected_capabilities or not {
        "terminal",
        "write_file",
    }.intersection(selected_capabilities):
        _fail(
            "compensation_input_invalid",
            "compensation requires readback and a declared local-write capability",
        )
    selected_writes = _string_list("writes", writes)
    package_body = {
        "schema_version": "workflow_compensation_package_v1",
        "source_run_id": run_id,
        "source_core_digest": core_digest,
        "source_terminal_event_id": terminal_event["event_id"],
        "source_effect_id": effect_id,
        "source_task_id": source_task_id,
        "compensation_kind": compensation_kind,
        "capabilities": selected_capabilities,
        "writes": selected_writes,
    }
    policy_digest = _digest(package_body)
    workflow_id = f"comp-{hashlib.sha256(_canonical_bytes(package_body)).hexdigest()[:24]}"
    definition = {
        "schema_version": "workflow_definition_v1",
        "workflow_id": workflow_id,
        "version": 1,
        "title": "Explicit local effect compensation",
        "goal": "Compensate one observed effect from a failed workflow run.",
        "non_goals": ["Do not mutate the source ledger.", "Do not infer authority from evidence."],
        "constraints": [
            "A fresh exact local execution grant is required.",
            "Apply mode requires policy-bound approval evidence.",
        ],
        "authority": {
            "max_side_effect": "local_write",
            "allowed_capabilities": selected_capabilities,
        },
        "tasks": [
            {
                "id": "compensate",
                "title": "Apply bounded compensation",
                "role": "execute",
                "owner": "compensation-executor",
                "depends_on": [],
                "inputs": ["validated source effect evidence"],
                "outputs": ["compensation readback"],
                "writes": selected_writes,
                "authority": {
                    "side_effect": "local_write",
                    "capabilities": selected_capabilities,
                },
                "acceptance": [
                    {
                        "id": "compensation-applied",
                        "claim": "The compensation effect has bounded readback evidence.",
                        "evidence_required": ["readback"],
                    }
                ],
                "verifies": [],
            },
            {
                "id": "verify-compensation",
                "title": "Verify bounded compensation",
                "role": "verify",
                "owner": "compensation-verifier",
                "depends_on": ["compensate"],
                "inputs": ["compensation readback"],
                "outputs": ["verification verdict"],
                "writes": [],
                "authority": {
                    "side_effect": "local_read",
                    "capabilities": [
                        capability
                        for capability in selected_capabilities
                        if capability in {"read_file", "terminal"}
                    ]
                    or ["read_file"],
                },
                "acceptance": [
                    {
                        "id": "compensation-verified",
                        "claim": "Fresh readback verifies the compensation outcome.",
                        "evidence_required": ["readback"],
                    }
                ],
                "verifies": ["compensate"],
            },
        ],
        "loop_policy": {
            "max_iterations": 1,
            "repeated_failure_limit": 1,
            "on_repeated_failure": "terminal_failure",
        },
        "stop_conditions": [
            "success",
            "blocked",
            "human_gate",
            "terminal_failure",
            "cancelled",
            "stale",
        ],
        "handoff": {
            "required_fields": [
                "goal",
                "task_states",
                "artifacts",
                "evidence",
                "blockers",
                "next_step",
                "authority_state",
            ]
        },
    }
    plan = compile_workflow_definition_v1(definition)
    return {
        **package_body,
        "policy_digest": policy_digest,
        "definition": definition,
        "plan": plan,
        "authority_granted": False,
        "executable": False,
    }


__all__ = [
    "WorkflowCompensationError",
    "prepare_workflow_compensation_v1",
]
