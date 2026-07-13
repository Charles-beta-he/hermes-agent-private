#!/usr/bin/env python3
"""Repository-owned wrapper for the daily full multi-model smoke check."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workflow_kernel import cron_wrappers as kernel_cron  # noqa: E402

SAFE = SCRIPT_DIR / "multi-model-smoke-safe.py"
JOB_ID = kernel_cron.DAILY_FULL_JOB_ID
JOB_NAME = kernel_cron.DAILY_FULL_JOB_NAME
DEFAULT_GATE_GLOB = kernel_cron.DEFAULT_GATE_GLOB
DEFAULT_SKIP_LEDGER = kernel_cron.DEFAULT_SKIP_LEDGER
utc_now = kernel_cron.utc_now
latest_gate_path = kernel_cron.latest_gate_path
append_skip = kernel_cron.append_skip


def load_gate_decision(job_id: str = JOB_ID) -> dict | None:
    return kernel_cron.load_gate_decision(job_id)


def skip_payload(decision: dict) -> dict:
    return kernel_cron.daily_skip_payload(decision)


def should_skip_for_provider_health() -> dict | None:
    return kernel_cron.daily_should_skip_for_provider_health()


def load_safe():
    spec = importlib.util.spec_from_file_location("multi_model_smoke_safe", SAFE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load smoke safety wrapper: {SAFE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_original() -> dict:
    mod = load_safe()
    return mod.run_smoke(
        script=str(SCRIPT_DIR / "multi-model-smoke.py"),
        script_args=["--timeout", "60"],
        timeout_s=300,
        log_file=str(Path.home() / ".hermes" / "logs" / "multi-model-smoke-daily-full-wrapper.jsonl"),
    )


def main() -> int:
    skip = should_skip_for_provider_health()
    if skip:
        payload = skip
        gate_decision = skip.get("decision") if isinstance(skip.get("decision"), dict) else load_gate_decision()
        runtime_mode = "provider_health_degraded_skip"
    else:
        gate_decision = load_gate_decision()
        payload = run_original()
        runtime_mode = "original_run"
    payload["wrapper_run_receipt"] = kernel_cron.record_wrapper_run_receipt(
        job_id=JOB_ID,
        job_name=JOB_NAME,
        payload=payload,
        gate_decision=gate_decision,
        runtime_mode=runtime_mode,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
