"""Workflow-kernel open gate for one-target limited unattended dispatch.

The gate turns prior readiness artifacts into an auditable local open manifest.
Opening the manifest is not itself a send: external delivery is still performed
only by a separate guarded runner with idempotency, rate limits, and a kill
switch.  This keeps the authority boundary explicit and reversible.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_kernel.contracts import contract_packet
from workflow_kernel.io import canonical_json_bytes, read_json_artifact

OPEN_SCHEMA = "workflow_kernel_open_gate_v1"
OPEN_CONFIRMATION = "WORKFLOW_KERNEL_OPEN_ONE_TARGET_LIMITED_UNATTENDED_SENDS"
DEFAULT_TARGET = "telegram"
DEFAULT_PURPOSE = "workflow_kernel_status_smoke"
DEFAULT_DAILY_LIMIT = 1
DEFAULT_PAYLOAD = "Workflow Kernel opened limited unattended dispatch: governed one-target smoke."


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest_obj(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def artifact_paths(root: str | Path) -> dict[str, Path]:
    base = Path(root)
    return {
        "static_registry_validation": base / "docs/private/workflow-kernel-static-registry-validator/workflow_kernel_static_registry_validation_20260614.json",
        "local_runtime_plan": base / "docs/private/workflow-kernel-local-runtime-gate/workflow_kernel_local_runtime_dry_run_plan_20260614.json",
        "policy": base / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json",
        "dry_run_scheduler": base / "docs/private/workflow-kernel-unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json",
        "guarded_one_shot": base / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json",
        "enablement_packet": base / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json",
    }


def load_readiness(root: str | Path = ".") -> dict[str, Any]:
    paths = artifact_paths(root)
    artifacts = {name: read_json_artifact(path, missing_ok=True) for name, path in paths.items()}
    checks = {
        "static_registry_pass": artifacts["static_registry_validation"].get("status") == "PASS",
        "local_runtime_plan_ready": artifacts["local_runtime_plan"].get("status") == "READY_LOCAL_DRY_RUN",
        "local_runtime_no_external_side_effects": artifacts["local_runtime_plan"].get("external_side_effects_enabled") is False,
        "local_runtime_no_model_calls": artifacts["local_runtime_plan"].get("model_calls_enabled") is False,
        "local_runtime_no_adapter_writes": artifacts["local_runtime_plan"].get("adapter_writes_enabled") is False,
        "policy_gate_ready": artifacts["policy"].get("status") == "UNATTENDED_DISPATCH_POLICY_GATE_READY",
        "dry_run_scheduler_ready": artifacts["dry_run_scheduler"].get("status") == "UNATTENDED_DRY_RUN_SCHEDULER_READY",
        "one_shot_guarded_send_verified": artifacts["guarded_one_shot"].get("status") == "ONE_SHOT_GUARDED_SEND_VERIFIED",
        "enablement_packet_ready": artifacts["enablement_packet"].get("status") == "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY",
    }
    blockers = [name for name, ok in checks.items() if not ok]
    return {
        "schema": "workflow_kernel_open_readiness_v1",
        "status": "OPEN_READY" if not blockers else "OPEN_BLOCKED",
        "created_at": utc_now(),
        "checks": checks,
        "blockers": blockers,
        "artifact_paths": {name: str(path) for name, path in paths.items()},
        "artifact_digests": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            for name, path in paths.items()
        },
    }


def build_open_manifest(
    root: str | Path = ".",
    *,
    opened: bool = False,
    confirmation: str | None = None,
    operator: str = "human",
    target: str = DEFAULT_TARGET,
    purpose: str = DEFAULT_PURPOSE,
    payload: str = DEFAULT_PAYLOAD,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
) -> dict[str, Any]:
    readiness = load_readiness(root)
    errors: list[str] = []
    if readiness["status"] != "OPEN_READY":
        errors.extend(readiness["blockers"])
    if opened and confirmation != OPEN_CONFIRMATION:
        errors.append("open_confirmation_mismatch")
    if target != DEFAULT_TARGET:
        errors.append("target_not_allowlisted")
    if daily_limit < 1 or daily_limit > DEFAULT_DAILY_LIMIT:
        errors.append("daily_limit_out_of_bounds")
    payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    opened_effective = opened and not errors
    manifest = {
        "schema": OPEN_SCHEMA,
        "status": "OPENED_LIMITED_UNATTENDED" if opened_effective else "OPEN_READY_NOT_OPENED" if not errors else "OPEN_BLOCKED",
        "created_at": utc_now(),
        "operator": operator,
        "opened": opened_effective,
        "target": target,
        "purpose": purpose,
        "payload_template": payload,
        "payload_digest": payload_digest,
        "daily_limit": daily_limit,
        "kill_switch_enabled": False if opened_effective else True,
        "requires_idempotency_key": True,
        "requires_receipt_ledger": True,
        "requires_compensation_path": True,
        "external_side_effects_allowed_by_manifest": opened_effective,
        "readiness": readiness,
        "errors": errors,
    }
    manifest["manifest_digest"] = digest_obj({k: v for k, v in manifest.items() if k != "manifest_digest"})
    manifest["contract_packet"] = contract_packet(
        packet_type="workflow_kernel_open_gate_manifest",
        payload=manifest,
        evidence_refs=list(readiness["artifact_paths"].values()),
    )
    return manifest


def validate_open_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if manifest.get("schema") != OPEN_SCHEMA:
        errors.append("schema_mismatch")
    expected = digest_obj({k: v for k, v in manifest.items() if k not in {"manifest_digest", "contract_packet"}})
    # Older manifests include contract_packet after the digest; compare without it.
    if manifest.get("manifest_digest") != expected:
        errors.append("manifest_digest_mismatch")
    if manifest.get("opened") is True:
        for key in ("requires_idempotency_key", "requires_receipt_ledger", "requires_compensation_path"):
            if manifest.get(key) is not True:
                errors.append(f"missing_required_control:{key}")
        if manifest.get("kill_switch_enabled") is not False:
            errors.append("kill_switch_not_open")
        if manifest.get("daily_limit") != DEFAULT_DAILY_LIMIT:
            errors.append("daily_limit_not_minimal")
    return {"schema": "workflow_kernel_open_manifest_validation_v1", "status": "PASS" if not errors else "FAIL", "errors": errors}


__all__ = [
    "OPEN_CONFIRMATION",
    "build_open_manifest",
    "load_readiness",
    "validate_open_manifest",
]
