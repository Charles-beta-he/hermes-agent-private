#!/usr/bin/env python3
"""Apply the authorized single-wrapper scheduler-native rollout.

Phase 42 / option C boundary: mutate exactly one cron job's script field from the
legacy every-4h wrapper to the workflow-kernel scheduler-native runner. The
script creates a backup, verifies a scoped textual change, and supports rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
TARGETS = {
    "every4h": {
        "phase": "42",
        "job_id": "2c14ca2a9bd2",
        "old_script": "multi-model-smoke-every4h-wrapper.py",
        "new_script": "workflow-kernel-multi-model-smoke-every4h-runner.py",
    },
    "daily-full": {
        "phase": "44",
        "job_id": "42baeebb646c",
        "old_script": "multi-model-smoke-daily-full-wrapper.py",
        "new_script": "workflow-kernel-multi-model-smoke-daily-full-runner.py",
    },
}
JOBS = DEFAULT_JOBS
OLD_SCRIPT = TARGETS["every4h"]["old_script"]
NEW_SCRIPT = TARGETS["every4h"]["new_script"]
JOB_ID = TARGETS["every4h"]["job_id"]
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-scheduler-native-rollout"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jobs(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return data["jobs"]
    if isinstance(data, list):
        return data
    raise ValueError("Unsupported jobs.json shape")


def find_job(path: Path, job_id: str = JOB_ID) -> dict[str, Any] | None:
    for job in load_jobs(path):
        if job.get("id") == job_id:
            return job
    return None


def backup_path(path: Path, *, phase: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.bak-phase{phase}-scheduler-native-{stamp}")


def apply_rollout(*, jobs_path: Path = JOBS, target: str = "every4h", dry_run: bool = False) -> dict[str, Any]:
    cfg = TARGETS[target]
    job_id = cfg["job_id"]
    old_script = cfg["old_script"]
    new_script = cfg["new_script"]
    phase = cfg["phase"]
    before_text = jobs_path.read_text()
    before_sha = hashlib.sha256(before_text.encode()).hexdigest()
    needle = f'"script": "{old_script}"'
    repl = f'"script": "{new_script}"'
    old_count = before_text.count(needle)
    new_count_before = before_text.count(repl)
    if old_count != 1:
        status = "SCHEDULER_NATIVE_ROLLOUT_ALREADY_APPLIED" if new_count_before == 1 else "SCHEDULER_NATIVE_ROLLOUT_BLOCKED_UNEXPECTED_SCRIPT_COUNT"
        return {"report_id": f"workflow_kernel_scheduler_native_rollout_20260613_phase{phase}", "created_at": utc_now(), "status": status, "target": target, "job_id": job_id, "old_script_count": old_count, "new_script_count": new_count_before, "cron_jobs_sha_before": before_sha, "cron_jobs_sha_after": before_sha, "cron_state_mutated": False, "backup_path": None, "dry_run": dry_run}
    after_text = before_text.replace(needle, repl, 1)
    parsed_before = json.loads(before_text)
    parsed_after = json.loads(after_text)
    before_job = next((j for j in (parsed_before.get("jobs", parsed_before) if isinstance(parsed_before, dict) else parsed_before) if j.get("id") == job_id), None)
    # Validate after through temp parsing, not by writing first.
    after_jobs = parsed_after.get("jobs", parsed_after) if isinstance(parsed_after, dict) else parsed_after
    after_job = next((j for j in after_jobs if j.get("id") == job_id), None)
    if not after_job or after_job.get("script") != new_script:
        raise RuntimeError("post-change job readback failed")
    backup = None
    if not dry_run:
        backup = backup_path(jobs_path, phase=phase)
        shutil.copy2(jobs_path, backup)
        jobs_path.write_text(after_text)
    after_sha = hashlib.sha256(after_text.encode()).hexdigest()
    return {
        "report_id": f"workflow_kernel_scheduler_native_rollout_20260613_phase{phase}",
        "created_at": utc_now(),
        "status": "SCHEDULER_NATIVE_ROLLOUT_DRY_RUN_READY" if dry_run else "SCHEDULER_NATIVE_ROLLOUT_APPLIED",
        "target": target,
        "phase": phase,
        "job_id": job_id,
        "old_script": old_script,
        "new_script": new_script,
        "old_script_count": old_count,
        "new_script_count_before": new_count_before,
        "cron_jobs_sha_before": before_sha,
        "cron_jobs_sha_after": after_sha,
        "cron_state_mutated": not dry_run,
        "backup_path": str(backup) if backup else None,
        "dry_run": dry_run,
        "before_job_script": (before_job or {}).get("script"),
        "after_job_script": after_job.get("script"),
        "rollback_command": f"cp {backup} {jobs_path}" if backup else None,
        "external_side_effects": False,
        "model_generation_attempted": False,
    }


def rollback(*, jobs_path: Path, backup: Path) -> dict[str, Any]:
    before = sha(jobs_path)
    shutil.copy2(backup, jobs_path)
    after = sha(jobs_path)
    job = find_job(jobs_path)
    return {"status": "SCHEDULER_NATIVE_ROLLOUT_ROLLED_BACK", "job_id": job.get("id") if job else JOB_ID, "restored_script": (job or {}).get("script"), "cron_jobs_sha_before": before, "cron_jobs_sha_after": after}


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = report.get("report_id", "workflow_kernel_scheduler_native_rollout_20260613_phase42")
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    artifact = write_json_artifact(json_path, report)
    md_path.write_text(
        "\n".join([
            "# Workflow Kernel Scheduler-native Rollout — Phase 42",
            "",
            f"Status: **{report['status']}**",
            f"Job: `{report.get('job_id')}`",
            f"Old script: `{report.get('old_script')}`",
            f"New script: `{report.get('new_script')}`",
            f"Backup: `{report.get('backup_path')}`",
            f"Rollback: `{report.get('rollback_command')}`",
        ]) + "\n"
    )
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(JOBS))
    ap.add_argument("--target", choices=sorted(TARGETS), default="every4h")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback-backup", default=None)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.rollback_backup:
        report = rollback(jobs_path=Path(args.jobs), backup=Path(args.rollback_backup))
    else:
        report = apply_rollout(jobs_path=Path(args.jobs), target=args.target, dry_run=args.dry_run)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report["status"])
    return 0 if "BLOCKED" not in report["status"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
