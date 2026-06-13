#!/usr/bin/env python3
"""Compile Phase 46 post-auto-enable observation evidence.

This script is intentionally read-only. It summarizes the already-refreshed
projection/correlation/readiness/drift artifacts after the workflow kernel runtime
switch is auto-enabled. It does not trigger cron, call models, mutate scheduler
state, read credentials, or send external messages.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel import PHASE, RUNTIME_SWITCH_ENABLED
from workflow_kernel.io import write_json_artifact

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-post-auto-enable-observation"
DEFAULT_PROJECTION = ROOT / "docs" / "private" / "workflow-kernel-cron-runtime-projection" / "workflow_kernel_cron_runtime_projection_20260613_phase32.json"
DEFAULT_CORRELATION = ROOT / "docs" / "private" / "workflow-kernel-cron-run-correlation" / "workflow_kernel_cron_run_correlation_20260613_phase33.json"
DEFAULT_READINESS = ROOT / "docs" / "private" / "workflow-kernel-runtime-readiness-summary" / "workflow_kernel_runtime_readiness_summary_20260613_phase36.json"
DEFAULT_DRIFT = ROOT / "docs" / "private" / "workflow-kernel-drift-watch" / "workflow_kernel_drift_watch_20260613_phase40.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_report(*, projection_path: Path = DEFAULT_PROJECTION, correlation_path: Path = DEFAULT_CORRELATION, readiness_path: Path = DEFAULT_READINESS, drift_path: Path = DEFAULT_DRIFT) -> dict[str, Any]:
    projection = read_json(projection_path)
    correlation = read_json(correlation_path)
    readiness = read_json(readiness_path)
    drift = read_json(drift_path)
    wrappers = projection.get("wrapper_projections") or []
    scheduler_native_jobs = [
        {
            "job_id": w.get("job_id"),
            "job_name": w.get("job_name"),
            "script": w.get("script"),
            "projection_status": w.get("projection_status"),
            "next_run_at": w.get("next_run_at"),
            "last_status": w.get("last_status"),
        }
        for w in wrappers
    ]
    awaiting = correlation.get("status") == "AWAITING_POST_PROJECTION_SCHEDULED_RUN"
    ready_core = (
        RUNTIME_SWITCH_ENABLED is True
        and projection.get("status") == "SCHEDULER_READBACK_PROJECTION_READY"
        and readiness.get("status") in {"RUNTIME_READINESS_SUMMARY_AUTO_ENABLED", "RUNTIME_READINESS_SUMMARY_DEGRADED_NOT_BLOCKED"}
        and drift.get("status") == "DRIFT_WATCH_CLEAR_READ_ONLY"
        and not drift.get("missing_receipt_jobs")
    )
    status = "POST_AUTO_ENABLE_OBSERVATION_PENDING_SCHEDULED_RUN" if ready_core and awaiting else (
        "POST_AUTO_ENABLE_OBSERVATION_READY" if ready_core else "POST_AUTO_ENABLE_OBSERVATION_BLOCKED"
    )
    return {
        "report_id": "workflow_kernel_post_auto_enable_observation_20260613_phase46",
        "created_at": iso(utc_now()),
        "status": status,
        "kernel_phase": PHASE,
        "runtime_switch_enabled": RUNTIME_SWITCH_ENABLED,
        "projection_status": projection.get("status"),
        "correlation_status": correlation.get("status"),
        "correlation_counts": correlation.get("correlation_counts", {}),
        "readiness_status": readiness.get("status"),
        "readiness_blocking_checks": readiness.get("blocking_checks", []),
        "drift_status": drift.get("status"),
        "missing_receipt_jobs": drift.get("missing_receipt_jobs", []),
        "scheduler_native_jobs": scheduler_native_jobs,
        "interpretation": "auto-enabled runtime is healthy, but no post-projection natural scheduled run has occurred yet" if awaiting else "auto-enabled runtime observation has current post-projection evidence",
        "safe_boundaries": [
            "read-only observation",
            "no cron trigger",
            "no cron/jobs.json mutation",
            "no model generation",
            "no credential read/write",
            "no external message send",
        ],
        "next_boundary": "wait for natural scheduled run, then refresh projection/correlation/readiness/drift again",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_post_auto_enable_observation_20260613_phase46.json"
    md_path = out_dir / "workflow_kernel_post_auto_enable_observation_20260613_phase46.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Post-auto-enable Observation — Phase 46",
        "",
        f"Status: **{report['status']}**",
        f"Runtime switch enabled: `{report['runtime_switch_enabled']}`",
        f"Projection: `{report['projection_status']}`",
        f"Correlation: `{report['correlation_status']}`",
        f"Readiness: `{report['readiness_status']}`",
        f"Drift: `{report['drift_status']}`",
        "",
        "## Scheduler-native jobs",
    ]
    for job in report["scheduler_native_jobs"]:
        lines.append(f"- `{job['job_id']}` {job['job_name']}: script=`{job['script']}`, next=`{job['next_run_at']}`, last_status=`{job['last_status']}`")
    lines.extend(["", "## Boundaries", *[f"- {x}" for x in report["safe_boundaries"]]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report()
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: correlation={report['correlation_status']}")
    return 0 if report["status"] != "POST_AUTO_ENABLE_OBSERVATION_BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
