#!/usr/bin/env python3
"""Phase 57: roll out Workflow Kernel unattended dry-run cron job.

This mutates ~/.hermes/cron/jobs.json in a narrowly scoped, reversible way by
adding/updating one local no-agent dry-run job. The job calls a wrapper that
only writes no-send receipts.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JOB_ID = "wk57dryrun01"
JOB_NAME = "Workflow Kernel unattended dry-run receipt"
SCRIPT_NAME = "workflow-kernel-unattended-dry-run-runner.py"
SCHEDULE_EXPR = "45 12 * * *"
REPORT_DIR = Path("docs/private/workflow-kernel-dry-run-cron-rollout")
REPORT_JSON = REPORT_DIR / "workflow_kernel_dry_run_cron_rollout_20260613_phase57.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_dry_run_cron_rollout_20260613_phase57.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def hermes_home() -> Path:
    return Path.home() / ".hermes"


def cron_path() -> Path:
    return hermes_home() / "cron" / "jobs.json"


def backup_path(now: datetime) -> Path:
    stamp = now.astimezone().strftime("%Y%m%d%H%M%S")
    return hermes_home() / "cron" / f"jobs.json.bak-phase57-dry-run-cron-{stamp}"


def load_jobs(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_job(now: datetime) -> dict[str, Any]:
    # Keep next run conservative and local-time-ish; scheduler recomputes future runs after execution.
    next_run = now.astimezone() + timedelta(hours=1)
    return {
        "id": JOB_ID,
        "name": JOB_NAME,
        "prompt": "No-agent Workflow Kernel unattended dry-run receipt. Script output is delivered locally and never sends external messages.",
        "skills": [],
        "skill": None,
        "model": None,
        "provider": None,
        "base_url": None,
        "script": SCRIPT_NAME,
        "context_from": None,
        "schedule": {"kind": "cron", "expr": SCHEDULE_EXPR, "display": SCHEDULE_EXPR},
        "schedule_display": SCHEDULE_EXPR,
        "repeat": {"times": None, "completed": 0},
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": iso(now.astimezone()),
        "next_run_at": iso(next_run),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "deliver": "local",
        "origin": None,
        "enabled_toolsets": ["terminal", "file"],
        "workdir": str(Path.home() / ".hermes" / "hermes-agent"),
        "no_agent": True,
    }


def rollout(dry_run: bool = False) -> dict[str, Any]:
    now = utc_now()
    path = cron_path()
    data = load_jobs(path)
    jobs = data.setdefault("jobs", [])
    existing = next((j for j in jobs if j.get("id") == JOB_ID), None)
    desired = build_job(now)
    action = "updated" if existing else "created"
    before_count = len(jobs)
    backup = backup_path(now)
    if existing:
        desired["repeat"] = existing.get("repeat", desired["repeat"])
        desired["created_at"] = existing.get("created_at", desired["created_at"])
        desired["last_run_at"] = existing.get("last_run_at")
        desired["last_status"] = existing.get("last_status")
        desired["last_error"] = existing.get("last_error")
        desired["last_delivery_error"] = existing.get("last_delivery_error")
        existing.clear(); existing.update(desired)
    else:
        jobs.append(desired)
    if not dry_run:
        shutil.copy2(path, backup)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    report = {
        "schema": "workflow_kernel_dry_run_cron_rollout_v1",
        "phase": 57,
        "status": "DRY_RUN_CRON_ROLLOUT_APPLIED" if not dry_run else "DRY_RUN_CRON_ROLLOUT_DRY_RUN_READY",
        "action": action,
        "job_id": JOB_ID,
        "job_name": JOB_NAME,
        "script": SCRIPT_NAME,
        "schedule_display": SCHEDULE_EXPR,
        "before_job_count": before_count,
        "after_job_count": len(jobs),
        "backup_path": str(backup) if not dry_run else None,
        "rollback_command": f"cp {backup} {path}" if not dry_run else None,
        "no_agent": True,
        "deliver": "local",
        "would_send": False,
        "external_side_effects_executed": False,
        "unattended_dispatch_enabled": False,
        "cron_state_mutated": not dry_run,
        "safe_boundaries": [
            "dry-run receipt only",
            "local delivery only",
            "no Telegram/Lark/GitHub send",
            "no credential read/write",
            "single cron job idempotent rollout",
        ],
        "created_at": iso(now),
    }
    return report


def write_report(report: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Workflow Kernel Dry-run Cron Rollout — Phase 57",
        "",
        f"Status: **{report['status']}**",
        f"Job: `{report['job_id']}` — {report['job_name']}",
        f"Script: `{report['script']}`",
        f"Schedule: `{report['schedule_display']}`",
        f"Backup: `{report.get('backup_path')}`",
        "",
        "## Safety",
        f"- would_send: `{report['would_send']}`",
        f"- external_side_effects_executed: `{report['external_side_effects_executed']}`",
        f"- unattended_dispatch_enabled: `{report['unattended_dispatch_enabled']}`",
        f"- cron_state_mutated: `{report['cron_state_mutated']}`",
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = rollout(dry_run=args.dry_run)
    if args.write_report:
        write_report(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
