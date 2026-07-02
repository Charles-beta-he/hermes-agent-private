"""Read-only/local receipt helpers for the complex-task harness.

This module exposes a CLI-friendly projection over
``agent.conductor_runtime.ComplexTaskHarnessLedger``.  It never marks work done,
never dispatches workers, and never performs external side effects.
"""
from __future__ import annotations

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
