"""Human-gated local runtime dry-run compiler for workflow-kernel.

This module is the next boundary after the phase-1 static registry: it can turn
an approved registry into a deterministic local dry-run execution plan, but it
still refuses model calls, adapter writes, write-through, and external side
effects.  The only enabled runtime mode here is local/no-op planning with an
explicit human-gate packet.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from workflow_kernel.contracts import contract_packet, digest_payload, utc_now
from workflow_kernel.static_registry import MODULE_NAMES, registry_snapshot

RUNTIME_GATE_SCHEMA = "workflow_kernel_runtime_gate_v1"
LOCAL_DRY_RUN_SCOPE = "workflow_kernel:local_dry_run_runtime"
LOCAL_DRY_RUN_AUTHORITY = "budget-handoff"
ALLOWED_AUTHORITIES = {LOCAL_DRY_RUN_AUTHORITY, "autonomous-execution"}
FORBIDDEN_RUNTIME_FLAGS = (
    "write_through_enabled",
    "external_side_effects_enabled",
    "model_calls_enabled",
    "adapter_writes_enabled",
)


def _default_expiry(hours: int = 24) -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def build_human_gate_packet(
    *,
    decision_id: str,
    scope: str = LOCAL_DRY_RUN_SCOPE,
    requested_authority: str = LOCAL_DRY_RUN_AUTHORITY,
    operator: str = "human",
    evidence_refs: list[str] | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic local runtime-gate packet.

    The digest intentionally excludes the digest field itself.  The packet is a
    local authorization record only; it is not a credential and does not grant
    external side effects.
    """
    body: dict[str, Any] = {
        "schema": RUNTIME_GATE_SCHEMA,
        "decision_id": decision_id,
        "scope": scope,
        "requested_authority": requested_authority,
        "operator": operator,
        "expires_at": expires_at or _default_expiry(),
        "evidence_refs": evidence_refs or [],
    }
    body["digest"] = digest_payload(body)
    return body


def validate_human_gate_packet(packet: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a local dry-run runtime authorization packet fail-closed."""
    registry = registry or registry_snapshot()
    errors: list[str] = []
    warnings: list[str] = []

    required = ("decision_id", "scope", "requested_authority", "digest", "expires_at", "operator")
    for field in required:
        if not str(packet.get(field) or "").strip():
            errors.append(f"missing human gate field: {field}")

    if packet.get("schema") != RUNTIME_GATE_SCHEMA:
        errors.append("runtime gate schema mismatch")
    if packet.get("scope") != LOCAL_DRY_RUN_SCOPE:
        errors.append(f"scope must be {LOCAL_DRY_RUN_SCOPE}")
    if packet.get("requested_authority") not in ALLOWED_AUTHORITIES:
        errors.append(f"authority not allowed for local dry-run runtime: {packet.get('requested_authority')}")

    digest_source = {k: deepcopy(v) for k, v in packet.items() if k != "digest"}
    expected_digest = digest_payload(digest_source)
    if packet.get("digest") != expected_digest:
        errors.append("human gate digest mismatch")

    flags = registry.get("safety_flags") or {}
    # Local dry-run runtime may be authorized by packet, but every side-effect
    # flag must remain false until a later, narrower gate exists.
    for flag in FORBIDDEN_RUNTIME_FLAGS:
        if flags.get(flag) is not False:
            errors.append(f"forbidden runtime flag enabled: {flag}")
    if flags.get("runtime_enabled") is not False:
        warnings.append("static registry runtime_enabled is false; local dry-run runtime is packet-scoped only")

    return {
        "schema": "workflow_kernel_runtime_gate_validation_v1",
        "status": "PASS" if not errors else "FAIL",
        "scope": packet.get("scope"),
        "requested_authority": packet.get("requested_authority"),
        "errors": errors,
        "warnings": warnings,
    }


def compile_local_dry_run_plan(packet: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compile a side-effect-free runtime plan from the static registry."""
    registry = registry or registry_snapshot()
    gate = validate_human_gate_packet(packet, registry)
    modules = registry.get("modules") or []
    actions = [
        {
            "module": module.get("name"),
            "route_alias": module.get("route_alias"),
            "executor": "local_noop_dry_run",
            "model_call": False,
            "adapter_write": False,
            "external_side_effect": False,
        }
        for module in modules
    ]
    missing_modules = [name for name in MODULE_NAMES if name not in {action.get("module") for action in actions}]
    errors = list(gate.get("errors") or [])
    for name in missing_modules:
        errors.append(f"missing module action: {name}")

    status = "READY_LOCAL_DRY_RUN" if not errors else "BLOCKED"
    payload = {
        "schema": "workflow_kernel_local_dry_run_plan_v1",
        "status": status,
        "created_at": utc_now(),
        "gate": gate,
        "executor_mode": "local_noop_dry_run",
        "runtime_enabled": status == "READY_LOCAL_DRY_RUN",
        "write_through_enabled": False,
        "external_side_effects_enabled": False,
        "model_calls_enabled": False,
        "adapter_writes_enabled": False,
        "actions": actions,
        "errors": errors,
    }
    payload["contract_packet"] = contract_packet(
        packet_type="workflow_kernel_local_dry_run_runtime_plan",
        payload=payload,
        evidence_refs=packet.get("evidence_refs") or [],
    )
    return payload


__all__ = [
    "ALLOWED_AUTHORITIES",
    "LOCAL_DRY_RUN_AUTHORITY",
    "LOCAL_DRY_RUN_SCOPE",
    "RUNTIME_GATE_SCHEMA",
    "build_human_gate_packet",
    "compile_local_dry_run_plan",
    "validate_human_gate_packet",
]
