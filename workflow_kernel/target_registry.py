"""Enterprise target registry for workflow-kernel unattended dispatch.

The registry is intentionally local and explicit.  It opens enterprise-style
multi-target plumbing without discovering or broadcasting to arbitrary chats.
Only targets marked enabled participate in routine dispatch.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from workflow_kernel.io import canonical_json_bytes

TARGET_REGISTRY_SCHEMA = "workflow_kernel_target_registry_v1"
DEFAULT_TARGET_ID = "telegram_home"

_TARGETS: dict[str, dict[str, Any]] = {
    "telegram_home": {
        "target_id": "telegram_home",
        "adapter": "telegram",
        "send_target": "telegram",
        "target_type": "home_channel",
        "owner": "he-cheng",
        "enabled": True,
        "purpose_allowlist": ["workflow_kernel_status_smoke", "workflow_kernel_enterprise_heartbeat"],
        "daily_limit": 1,
        "hourly_limit": 1,
        "burst_limit": 1,
        "quiet_hours_local": [],
        "payload_policy": "workflow_kernel_enterprise_payload_guard_v1",
        "kill_switch_enabled": False,
        "compensation_path": "Send correction only after explicit operator approval.",
    }
}


def registry_snapshot() -> dict[str, Any]:
    targets = [deepcopy(v) for v in _TARGETS.values()]
    payload = {
        "schema": TARGET_REGISTRY_SCHEMA,
        "default_target_id": DEFAULT_TARGET_ID,
        "multi_target_enabled": True,
        "cross_adapter_enabled": False,
        "arbitrary_target_discovery_enabled": False,
        "targets": targets,
        "global_controls": {
            "requires_enabled_target": True,
            "requires_purpose_allowlist": True,
            "requires_payload_guard": True,
            "requires_idempotency_key": True,
            "requires_receipt_ledger": True,
            "requires_per_target_rate_limit": True,
            "requires_compensation_path": True,
        },
    }
    payload["registry_digest"] = hashlib.sha256(canonical_json_bytes({k: v for k, v in payload.items() if k != "registry_digest"})).hexdigest()
    return payload


def enabled_targets(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    registry = registry or registry_snapshot()
    return [target for target in registry.get("targets", []) if target.get("enabled") is True and target.get("kill_switch_enabled") is False]


def find_target(target_id: str, registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    registry = registry or registry_snapshot()
    for target in registry.get("targets", []):
        if target.get("target_id") == target_id:
            return target
    return None


def validate_registry(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or registry_snapshot()
    errors: list[str] = []
    if registry.get("schema") != TARGET_REGISTRY_SCHEMA:
        errors.append("schema_mismatch")
    if registry.get("arbitrary_target_discovery_enabled") is not False:
        errors.append("arbitrary_target_discovery_must_stay_false")
    for target in registry.get("targets", []):
        tid = target.get("target_id")
        if not tid:
            errors.append("target_id_missing")
        if target.get("adapter") != "telegram":
            errors.append(f"unsupported_adapter:{tid}:{target.get('adapter')}")
        if int(target.get("daily_limit") or 0) < 1:
            errors.append(f"daily_limit_missing:{tid}")
        if not target.get("purpose_allowlist"):
            errors.append(f"purpose_allowlist_missing:{tid}")
        if not target.get("compensation_path"):
            errors.append(f"compensation_path_missing:{tid}")
    if not enabled_targets(registry):
        errors.append("no_enabled_targets")
    return {"schema": "workflow_kernel_target_registry_validation_v1", "status": "PASS" if not errors else "FAIL", "errors": errors}


__all__ = ["DEFAULT_TARGET_ID", "TARGET_REGISTRY_SCHEMA", "enabled_targets", "find_target", "registry_snapshot", "validate_registry"]
