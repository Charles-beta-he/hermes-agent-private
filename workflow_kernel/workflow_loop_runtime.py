"""Default-off local-shadow registration for the authority-neutral loop runtime."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from workflow_kernel.workflow_loop_event_ledger import (
    append_workflow_loop_event,
    read_workflow_loop_events,
    replay_workflow_loop_events_v1,
)


_REQUIRED_DENIED = {
    "adapter_write",
    "cron_mutation",
    "external_dispatch",
    "model_call",
    "publish_deploy_push",
    "task_execution",
}

_REGISTRY: dict[str, Any] = {
    "schema_version": "workflow_loop_runtime_registry_v1",
    "contract": "workflow-loop-v1",
    "maturity": "registered",
    "mode": "local_shadow",
    "default_enabled": False,
    "authority_granted": False,
    "executable": False,
    "external_side_effects_enabled": False,
    "model_calls_enabled": False,
    "adapter_writes_enabled": False,
    "allowed_operations": [
        "compile_definition",
        "project_signal",
        "append_private_local_ledger",
        "replay_private_local_ledger",
        "emit_local_observation_report",
    ],
    "denied_operations": sorted(_REQUIRED_DENIED),
}


def registry_snapshot() -> dict[str, Any]:
    return deepcopy(_REGISTRY)


def validate_registry(registry: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    exact_false = (
        ("default_enabled", "default_enabled_must_be_false"),
        ("authority_granted", "authority_granted_must_be_false"),
        ("executable", "executable_must_be_false"),
        ("external_side_effects_enabled", "external_side_effects_must_be_false"),
        ("model_calls_enabled", "model_calls_must_be_false"),
        ("adapter_writes_enabled", "adapter_writes_must_be_false"),
    )
    if registry.get("schema_version") != "workflow_loop_runtime_registry_v1":
        errors.append("schema_version_invalid")
    if registry.get("contract") != "workflow-loop-v1":
        errors.append("contract_invalid")
    if registry.get("maturity") != "registered":
        errors.append("maturity_invalid")
    if registry.get("mode") != "local_shadow":
        errors.append("mode_invalid")
    for field, error in exact_false:
        if registry.get(field) is not False:
            errors.append(error)
    denied = registry.get("denied_operations")
    if not isinstance(denied, list) or not _REQUIRED_DENIED.issubset(set(denied)):
        errors.append("denied_operations_incomplete")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors}


def _event_chain_digest(events: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [event["event_id"] for event in events],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def observe_workflow_loop_shadow_v1(
    plan: Mapping[str, Any],
    events: list[dict[str, Any]],
    *,
    private_root: Path,
    ledger_path: Path,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append and replay observed local events without executing workflow tasks."""

    selected_registry = registry_snapshot() if registry is None else deepcopy(registry)
    validation = validate_registry(selected_registry)
    if validation["status"] != "PASS":
        raise ValueError(f"registry validation failed: {validation['errors']}")
    if plan.get("authority_granted") is not False or plan.get("executable") is not False:
        raise ValueError("execution plan must remain authority-neutral and non-executable")

    append_statuses: list[str] = []
    for event in events:
        result = append_workflow_loop_event(
            ledger_path,
            plan,
            event,
            private_root=private_root,
        )
        append_statuses.append(result["status"])

    readback = read_workflow_loop_events(ledger_path, private_root=private_root)
    snapshot = replay_workflow_loop_events_v1(plan, readback)
    replay_again = replay_workflow_loop_events_v1(plan, readback)
    readback_verified = snapshot == replay_again and snapshot["sequence"] == len(readback)
    return {
        "schema_version": "workflow_loop_shadow_observation_v1",
        "status": "OBSERVED_LOCAL_SHADOW" if readback_verified else "OBSERVATION_FAILED",
        "contract": selected_registry["contract"],
        "registered_maturity": selected_registry["maturity"],
        "mode": selected_registry["mode"],
        "event_count": len(readback),
        "append_statuses": append_statuses,
        "ledger_tip_event_id": readback[-1]["event_id"] if readback else None,
        "event_chain_digest": _event_chain_digest(readback),
        "definition_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "final_sequence": snapshot["sequence"],
        "final_workflow_state": snapshot["workflow_state"],
        "final_stop_reason": snapshot["stop_reason"],
        "readback_verified": readback_verified,
        "authority_granted": False,
        "executable": False,
        "external_side_effects_enabled": False,
        "model_calls_enabled": False,
        "adapter_writes_enabled": False,
    }
