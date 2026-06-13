"""Shared kernel helpers for opt-in cron smoke wrappers.

Phase 31 boundary: this module centralizes read-only provider-health gate
consumption for the two already opted-in wrappers. It does not mutate cron job
state, call models, or send messages.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_GATE_GLOB = str(
    Path.home()
    / ".hermes"
    / "hermes-agent"
    / "docs"
    / "private"
    / "workflow-kernel-cron-provider-health-gate"
    / "cron_provider_health_gate_*.json"
)
DEFAULT_SKIP_LEDGER = Path.home() / ".hermes" / "state" / "cron-provider-health-gate" / "provider_health_skips.jsonl"
DEFAULT_RUN_RECEIPT_LEDGER = Path.home() / ".hermes" / "state" / "cron-provider-health-gate" / "wrapper_run_receipts.jsonl"

DAILY_FULL_JOB_ID = "42baeebb646c"
DAILY_FULL_JOB_NAME = "multi-model-smoke-daily-full"
EVERY4H_JOB_ID = "2c14ca2a9bd2"
EVERY4H_JOB_NAME = "multi-model-smoke-every-4h"

EVERY4H_MODEL_PROVIDER = {
    "gpt-5.5": "openai",
    "mimo-v2.5-pro": "xiaomi",
    "deepseek-v4-pro": "deepseek",
}
EVERY4H_BASE_ARGS = ["--skip-claude", "--timeout", "45"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_gate_path(pattern: str = DEFAULT_GATE_GLOB) -> Path | None:
    override = os.environ.get("HERMES_CRON_HEALTH_GATE_REPORT")
    if override:
        p = Path(override)
        return p if p.exists() else None
    matches = [Path(p) for p in glob.glob(pattern)]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def load_gate_decision(job_id: str, pattern: str = DEFAULT_GATE_GLOB) -> dict[str, Any] | None:
    path = latest_gate_path(pattern)
    if not path:
        return None
    report = json.loads(path.read_text())
    for decision in report.get("decisions", []):
        if decision.get("job_id") == job_id:
            return {**decision, "gate_report": str(path), "gate_report_id": report.get("report_id")}
    return None


def daily_skip_payload(decision: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "status": "provider_health_degraded_skip",
        "job_id": DAILY_FULL_JOB_ID,
        "job_name": DAILY_FULL_JOB_NAME,
        "created_at": utc_now(),
        "decision": decision,
        "external_side_effects": False,
        "model_generation_attempted": False,
        "cron_state_mutated": False,
        "kernel_shim": True,
    }
    payload["idempotency_key"] = hashlib.sha256(
        json.dumps(
            {
                "job_id": DAILY_FULL_JOB_ID,
                "gate_report_id": decision.get("gate_report_id"),
                "decision": decision.get("decision"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    return payload


def append_skip(payload: dict[str, Any], ledger: Path | None = None) -> dict[str, Any]:
    target = ledger or Path(os.environ.get("HERMES_CRON_HEALTH_SKIP_LEDGER", str(DEFAULT_SKIP_LEDGER)))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        for line in target.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("idempotency_key") == payload.get("idempotency_key"):
                return {**row, "ledger": str(target), "deduped": True, "kernel_shim": True}
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return {**payload, "ledger": str(target), "deduped": False}


def daily_should_skip_for_provider_health() -> dict[str, Any] | None:
    decision = load_gate_decision(DAILY_FULL_JOB_ID)
    if decision and decision.get("decision") == "skip_provider_health_degraded":
        return append_skip(daily_skip_payload(decision))
    return None


def every4h_script_args_for_decision(decision: dict[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    args = list(EVERY4H_BASE_ARGS)
    degraded: list[str] = []
    if decision and decision.get("decision") == "run_with_degraded_provider_exclusions":
        degraded = [str(x) for x in decision.get("degraded_endpoints") or []]
        allowed_models = [model for model, provider in EVERY4H_MODEL_PROVIDER.items() if provider not in set(degraded)]
        for model in allowed_models:
            args.extend(["--model", model])
    return args, {
        "gate_decision": decision,
        "degraded_endpoints_excluded": degraded,
        "selected_models": [args[i + 1] for i, item in enumerate(args[:-1]) if item == "--model"],
        "kernel_shim": True,
    }


def every4h_gate_decision() -> dict[str, Any] | None:
    return load_gate_decision(EVERY4H_JOB_ID)


def wrapper_run_receipt(
    *,
    job_id: str,
    job_name: str,
    payload: dict[str, Any],
    gate_decision: dict[str, Any] | None,
    runtime_mode: str,
) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload, dict) else None
    receipt = {
        "status": "wrapper_run_receipt_recorded",
        "created_at": utc_now(),
        "job_id": job_id,
        "job_name": job_name,
        "runtime_mode": runtime_mode,
        "kernel_shim": True,
        "gate_report": gate_decision.get("gate_report") if gate_decision else None,
        "gate_report_id": gate_decision.get("gate_report_id") if gate_decision else None,
        "gate_decision": gate_decision.get("decision") if gate_decision else None,
        "payload_status": payload.get("status"),
        "payload_ok": payload.get("ok"),
        "payload_returncode": payload.get("returncode"),
        "model_generation_attempted": bool(payload.get("model_generation_attempted", True)),
        "cron_state_mutated": False,
        "external_side_effects": False,
        "summary": summary if isinstance(summary, dict) else None,
    }
    receipt["idempotency_key"] = hashlib.sha256(
        json.dumps(
            {
                "job_id": job_id,
                "runtime_mode": runtime_mode,
                "gate_report_id": receipt["gate_report_id"],
                "payload_status": receipt["payload_status"],
                "summary": receipt["summary"],
                "created_at": receipt["created_at"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    return receipt


def append_run_receipt(receipt: dict[str, Any], ledger: Path | None = None) -> dict[str, Any]:
    target = ledger or Path(os.environ.get("HERMES_CRON_WRAPPER_RECEIPT_LEDGER", str(DEFAULT_RUN_RECEIPT_LEDGER)))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return {**receipt, "ledger": str(target)}


def record_wrapper_run_receipt(
    *,
    job_id: str,
    job_name: str,
    payload: dict[str, Any],
    gate_decision: dict[str, Any] | None,
    runtime_mode: str,
) -> dict[str, Any]:
    return append_run_receipt(
        wrapper_run_receipt(
            job_id=job_id,
            job_name=job_name,
            payload=payload,
            gate_decision=gate_decision,
            runtime_mode=runtime_mode,
        )
    )
