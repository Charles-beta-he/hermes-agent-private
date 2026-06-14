#!/usr/bin/env python3
"""Phase 65: manual-only closure monitoring projection.

Read-only invariant monitor for the Phase64 safe-default manual-only closure.
It checks local artifacts and cron metadata but does not mutate cron, enable
unattended dispatch, or send messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-manual-only-monitoring")
REPORT_JSON = REPORT_DIR / "workflow_kernel_manual_only_monitoring_20260613_phase65.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_manual_only_monitoring_20260613_phase65.md"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def find_job(jobs: Any, job_id: str) -> dict[str, Any] | None:
    if isinstance(jobs, dict):
        raw = jobs.get("jobs", jobs)
        if isinstance(raw, dict):
            item = raw.get(job_id)
            if isinstance(item, dict):
                return item
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("job_id", item.get("id")) == job_id:
                    return item
    if isinstance(jobs, list):
        for item in jobs:
            if isinstance(item, dict) and item.get("job_id", item.get("id")) == job_id:
                return item
    return None


def build_monitor(root: Path, hermes_home: Path | None = None) -> dict[str, Any]:
    hermes_home = hermes_home or Path.home() / ".hermes"
    closure = read_json(root / "docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json")
    decision = read_json(root / "docs/private/workflow-kernel-unattended-decision-record/workflow_kernel_unattended_decision_record_20260613_phase63.json")
    one_shot = read_json(root / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json")
    dry_run_cron = read_json(root / "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json")
    cron_jobs = read_json(hermes_home / "cron/jobs.json")
    job = find_job(cron_jobs, "wk57dryrun01") or {}
    job_script = str(job.get("script") or job.get("command") or "")
    checks = {
        "closure_ready": closure.get("status") == "MANUAL_ONLY_CLOSURE_READY",
        "closure_unattended_disabled": closure.get("unattended_dispatch_enabled") is False,
        "closure_external_false": closure.get("external_side_effects_executed") is False,
        "decision_keep_manual_only": decision.get("decision") == "keep_manual_only",
        "one_shot_history_present": one_shot.get("status") == "ONE_SHOT_GUARDED_SEND_VERIFIED",
        "dry_run_cron_artifact_no_send": dry_run_cron.get("would_send") is False,
        "dry_run_job_present": bool(job),
        "dry_run_job_no_agent": job.get("no_agent") is True if job else False,
        "dry_run_job_local_delivery": job.get("deliver") == "local" if job else False,
        "dry_run_job_script_is_runner": "workflow-kernel-unattended-dry-run-runner.py" in job_script if job else False,
    }
    drift = [k for k, ok in checks.items() if not ok]
    monitor = {
        "schema": "workflow_kernel_manual_only_monitoring_v1",
        "phase": 65,
        "status": "MANUAL_ONLY_MONITORING_CLEAR" if not drift else "MANUAL_ONLY_MONITORING_DRIFT_DETECTED",
        "created_at": iso_now(),
        "checks": checks,
        "drift": drift,
        "observed_job": {
            "job_id": "wk57dryrun01",
            "present": bool(job),
            "no_agent": job.get("no_agent") if job else None,
            "deliver": job.get("deliver") if job else None,
            "script": job_script or None,
        },
        "closure_digest": closure.get("closure_digest"),
        "last_known_one_shot_message_id": ((one_shot.get("send_result") or {}).get("message_id")),
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "message_sent": False,
        "read_only": True,
        "next_boundary": "Phase66 future unattended apply reopen packet",
    }
    monitor["monitor_digest"] = sha256_obj({k: monitor[k] for k in monitor if k != "monitor_digest"})
    return monitor


def write_monitor(monitor: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(monitor, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    drift_lines = [f"- {item}" for item in monitor.get("drift", [])] or ["- none"]
    REPORT_MD.write_text("\n".join([
        "# Workflow Kernel Manual-only Monitoring — Phase 65",
        "",
        f"Status: **{monitor['status']}**",
        f"Monitor digest: `{monitor['monitor_digest']}`",
        f"Unattended dispatch enabled: `{monitor['unattended_dispatch_enabled']}`",
        f"External side effects executed: `{monitor['external_side_effects_executed']}`",
        "",
        "## Drift",
        *drift_lines,
    ]) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    monitor = build_monitor(Path.cwd(), args.hermes_home)
    if args.write_report:
        write_monitor(monitor)
    if args.json:
        print(json.dumps(monitor, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if monitor["status"] == "MANUAL_ONLY_MONITORING_CLEAR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
