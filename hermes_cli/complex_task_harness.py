"""Read-only/local receipt helpers for the complex-task harness.

This module exposes a CLI-friendly projection over
``agent.conductor_runtime.ComplexTaskHarnessLedger``.  It never marks work done,
never dispatches workers, and never performs external side effects.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from agent.conductor_runtime import ComplexTaskHarnessLedger, conductor_defaults_enabled, default_conductor_config


def _merged_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_conductor_config()
    if not isinstance(config, dict):
        return merged
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            section = merged[key]
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, dict) and isinstance(section.get(sub_key), dict):
                    section[sub_key].update(sub_value)
                else:
                    section[sub_key] = sub_value
        else:
            merged[key] = value
    return merged


def config_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = _merged_config(config)
    conductor = merged.get("conductor") if isinstance(merged, dict) else {}
    conductor = conductor if isinstance(conductor, dict) else {}
    harness = conductor.get("complex_task_harness") if isinstance(conductor, dict) else {}
    harness = harness if isinstance(harness, dict) else {}
    router = conductor.get("intent_router") if isinstance(conductor, dict) else {}
    router = router if isinstance(router, dict) else {}
    return {
        "ok": True,
        "schema": "hermes_complex_task_harness_config_status_v1",
        "mode": "config_readback_only",
        "runtime_defaults_enabled": conductor_defaults_enabled(merged),
        "intent_router_enabled": bool(router.get("enabled")),
        "complex_task_harness": {
            "enabled": bool(harness.get("enabled")),
            "ledger_enabled": bool(harness.get("ledger_enabled")),
            "ledger_path": str(harness.get("ledger_path") or ""),
            "runtime_mutation_authorized": bool(harness.get("runtime_mutation_authorized")),
            "output_schema": harness.get("output_schema") or "",
        },
        "external_side_effects_enabled": False,
        "allowed_to_mark_done": False,
    }


def _ledger(path: str | Path | None = None) -> ComplexTaskHarnessLedger:
    return ComplexTaskHarnessLedger(path) if path else ComplexTaskHarnessLedger()


def status(*, ledger_path: str | Path | None = None, scan_id: str = "", limit: int = 20) -> dict[str, Any]:
    ledger = _ledger(ledger_path)
    events = ledger.read_events(scan_id=scan_id)
    limited = events[-max(1, min(int(limit or 20), 100)):]
    latest_scan_id = ""
    for event in reversed(events):
        if event.get("scan_id"):
            latest_scan_id = str(event["scan_id"])
            break
    return {
        "ok": True,
        "schema": "hermes_complex_task_harness_status_v1",
        "mode": "local_readback_only",
        "ledger_path": str(ledger.path),
        "scan_id_filter": scan_id,
        "event_count": len(events),
        "latest_scan_id": latest_scan_id,
        "events": limited,
        "external_side_effects_enabled": False,
        "allowed_to_mark_done": False,
    }


def verifier_packets(*, ledger_path: str | Path | None = None, scan_id: str = "", limit: int = 20) -> dict[str, Any]:
    ledger = _ledger(ledger_path)
    events = [event for event in ledger.read_events(scan_id=scan_id) if event.get("event_type") == "verifier_packet"]
    limited = events[-max(1, min(int(limit or 20), 100)):]
    latest_scan_id = ""
    for event in reversed(events):
        if event.get("scan_id"):
            latest_scan_id = str(event["scan_id"])
            break
    return {
        "ok": True,
        "schema": "hermes_complex_task_harness_verifier_packets_v1",
        "mode": "verifier_packet_readback_only",
        "ledger_path": str(ledger.path),
        "scan_id_filter": scan_id,
        "event_count": len(events),
        "latest_scan_id": latest_scan_id,
        "events": limited,
        "external_side_effects_enabled": False,
        "allowed_to_mark_done": False,
    }


EVIDENCE_PREFIXES = ("proof:", "artifact:", "command:", "file:", "url:")
TASKPOOL_STATUSES = {"proposed", "pending", "claimed", "running", "retry", "review", "certified", "blocked", "done", "archived", "expired"}
EXTERNAL_PLATFORMS = {"local_harness", "telegram", "lark", "feishu", "discord", "slack", "sms", "email"}


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject(schema: str, event_type: str, scan_id: str, reason: str, *, mode: str) -> dict[str, Any]:
    return {
        "ok": False,
        "schema": schema,
        "mode": mode,
        "receipt": {
            "schema": "hermes_complex_task_harness_receipt_v1",
            "event_type": event_type,
            "scan_id": scan_id,
            "status": "rejected",
            "reason": reason,
            "external_side_effects_enabled": False,
            "allowed_to_mark_done": False,
        },
        "external_side_effects_enabled": False,
        "allowed_to_mark_done": False,
    }


def _has_secret_shape(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "secret", "password", "api_key", "apikey", "authorization")):
                return True
            if _has_secret_shape(child):
                return True
    elif isinstance(value, list):
        return any(_has_secret_shape(item) for item in value)
    elif isinstance(value, str):
        lowered = value.lower()
        if "bearer " in lowered or "api_key" in lowered or "password=" in lowered:
            return True
    return False


def taskpool_lifecycle(
    *,
    scan_id: str,
    task_id: str,
    from_status: str,
    to_status: str,
    evidence_ref: str,
    reason: str,
    readback_ref: str,
    execute: bool = False,
    ledger_path: str | Path | None = None,
    actor: str = "complex_task_harness",
    authority: str = "autonomous-execution",
) -> dict[str, Any]:
    mode = "taskpool_lifecycle_adapter_receipt_only" if not execute else "taskpool_lifecycle_adapter_exact_write"
    if not scan_id or not task_id or not from_status or not to_status:
        return _reject("hermes_complex_task_harness_taskpool_lifecycle_v1", "taskpool_lifecycle_adapter", scan_id, "missing_exact_target", mode=mode)
    if from_status not in TASKPOOL_STATUSES or to_status not in TASKPOOL_STATUSES:
        return _reject("hermes_complex_task_harness_taskpool_lifecycle_v1", "taskpool_lifecycle_adapter", scan_id, "invalid_status", mode=mode)
    if not evidence_ref.startswith(EVIDENCE_PREFIXES):
        return _reject("hermes_complex_task_harness_taskpool_lifecycle_v1", "taskpool_lifecycle_adapter", scan_id, "invalid_evidence_ref", mode=mode)
    if not readback_ref.startswith(("command:", "artifact:", "file:")):
        return _reject("hermes_complex_task_harness_taskpool_lifecycle_v1", "taskpool_lifecycle_adapter", scan_id, "invalid_readback_ref", mode=mode)

    command = [
        str(Path.home() / ".hermes" / "scripts" / "brain_task_orchestrator.py"),
        "transition",
        task_id,
        "--to",
        to_status,
        "--actor",
        actor,
        "--authority",
        authority,
        "--reason",
        reason,
        "--evidence",
        evidence_ref,
        "--json",
    ]
    command_result: dict[str, Any] | None = None
    if execute:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=120, check=False)
        command_result = {
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "readback_ref": readback_ref,
        }
        if completed.returncode != 0:
            return _reject("hermes_complex_task_harness_taskpool_lifecycle_v1", "taskpool_lifecycle_adapter", scan_id, "transition_command_failed", mode=mode)

    adapter = {
        "task_id": task_id,
        "from_status": from_status,
        "to_status": to_status,
        "evidence_ref": evidence_ref,
        "reason": reason,
        "readback_ref": readback_ref,
        "actor": actor,
        "authority": authority,
        "execute_requested": bool(execute),
        "fixed_command": command,
        "command_result": command_result,
    }
    ledger = _ledger(ledger_path)
    receipt = ledger.append_taskpool_lifecycle_adapter(scan_id, adapter)
    return {
        "ok": receipt.get("status") == "recorded",
        "schema": "hermes_complex_task_harness_taskpool_lifecycle_v1",
        "mode": mode,
        "ledger_path": str(ledger.path),
        "receipt": receipt,
        "external_side_effects_enabled": False,
        "allowed_to_mark_done": bool(receipt.get("allowed_to_mark_done")),
    }


def external_dispatch(
    *,
    scan_id: str,
    platform: str,
    target: str,
    payload: str,
    payload_sha256: str,
    readback_ref: str,
    execute: bool = False,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    mode = "external_dispatch_adapter_outbox_only" if not execute else "external_dispatch_adapter_exact_send"
    if not scan_id or not platform or not target or not payload_sha256:
        return _reject("hermes_complex_task_harness_external_dispatch_v1", "external_dispatch_adapter", scan_id, "missing_exact_target", mode=mode)
    if platform not in EXTERNAL_PLATFORMS:
        return _reject("hermes_complex_task_harness_external_dispatch_v1", "external_dispatch_adapter", scan_id, "platform_not_allowlisted", mode=mode)
    if _has_secret_shape(payload):
        return _reject("hermes_complex_task_harness_external_dispatch_v1", "external_dispatch_adapter", scan_id, "secret_like_payload_rejected", mode=mode)
    actual_hash = _sha256_text(payload)
    if actual_hash != payload_sha256:
        return _reject("hermes_complex_task_harness_external_dispatch_v1", "external_dispatch_adapter", scan_id, "payload_hash_mismatch", mode=mode)
    if not readback_ref.startswith(("command:", "artifact:", "file:", "platform-readback:")):
        return _reject("hermes_complex_task_harness_external_dispatch_v1", "external_dispatch_adapter", scan_id, "invalid_readback_ref", mode=mode)
    send_executed = bool(execute and platform == "local_harness")
    if execute and platform != "local_harness":
        return _reject("hermes_complex_task_harness_external_dispatch_v1", "external_dispatch_adapter", scan_id, "real_platform_sender_not_bound", mode=mode)
    adapter = {
        "platform": platform,
        "target": target,
        "payload_sha256": payload_sha256,
        "payload_bytes": len(payload.encode("utf-8")),
        "payload_preview": payload[:80],
        "payload_stored": False,
        "readback_ref": readback_ref,
        "execute_requested": bool(execute),
        "send_executed": send_executed,
        "platform_message_id": "local-harness-dispatch-" + payload_sha256.split(":", 1)[-1][:16] if send_executed else "",
    }
    ledger = _ledger(ledger_path)
    receipt = ledger.append_external_dispatch_adapter(scan_id, adapter)
    return {
        "ok": receipt.get("status") == "recorded",
        "schema": "hermes_complex_task_harness_external_dispatch_v1",
        "mode": mode,
        "ledger_path": str(ledger.path),
        "receipt": receipt,
        "external_side_effects_enabled": bool(receipt.get("external_side_effects_enabled")),
        "allowed_to_mark_done": False,
    }


def checkpoint(
    *,
    scan_id: str,
    objective: str,
    completed_evidence: list[str] | None = None,
    pending_actions: list[str] | None = None,
    rollback: str = "",
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    ledger = _ledger(ledger_path)
    receipt = ledger.append_checkpoint(
        scan_id,
        objective=objective,
        completed_evidence=completed_evidence or [],
        pending_actions=pending_actions or [],
        rollback=rollback,
    )
    return {
        "ok": receipt.get("status") == "recorded",
        "schema": "hermes_complex_task_harness_checkpoint_v1",
        "mode": "local_receipt_only",
        "ledger_path": str(ledger.path),
        "receipt": receipt,
        "external_side_effects_enabled": False,
        "allowed_to_mark_done": False,
    }
