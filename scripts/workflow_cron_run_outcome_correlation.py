#!/usr/bin/env python3
"""Correlate real cron wrapper run outcomes with kernel-shim projection evidence.

Phase 33 boundary: read-only correlation only. This reads cron state, the latest
scheduler projection, and wrapper JSONL logs to determine whether any real
scheduled wrapper run occurred after the projection and whether the outcome is
consistent with the projected kernel-shim path. It does not execute cron jobs,
call models, mutate scheduler state, or send messages.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel import cron_wrappers
from workflow_kernel.io import read_json_artifact, write_json_artifact

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_PROJECTION_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-runtime-projection" / "workflow_kernel_cron_runtime_projection_*.json"
DEFAULT_LOG_DIR = Path.home() / ".hermes" / "logs"
DEFAULT_RECEIPT_LEDGER = Path.home() / ".hermes" / "state" / "cron-provider-health-gate" / "wrapper_run_receipts.jsonl"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-cron-run-correlation"

WRAPPER_LOGS = {
    cron_wrappers.DAILY_FULL_JOB_ID: "multi-model-smoke-daily-full-wrapper.jsonl",
    cron_wrappers.EVERY4H_JOB_ID: "multi-model-smoke-every4h-wrapper.jsonl",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def newest_path(pattern: str | Path) -> Path | None:
    matches = [Path(p) for p in glob.glob(str(pattern))]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def load_json(path: Path) -> Any:
    return read_json_artifact(path)


def load_jobs(path: Path) -> dict[str, dict[str, Any]]:
    data = load_json(path)
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        raise SystemExit(f"invalid jobs shape: {path}")
    return {str(j.get("id") or j.get("job_id")): j for j in jobs if isinstance(j, dict) and (j.get("id") or j.get("job_id"))}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def latest_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [(parse_time(str(r.get("timestamp") or "")), r) for r in rows]
    candidates = [(t, r) for t, r in candidates if t]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def projection_by_job(projection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(p.get("job_id")): p for p in projection.get("wrapper_projections", []) if isinstance(p, dict) and p.get("job_id")}


def latest_receipts_by_job(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for row in rows:
        job_id = str(row.get("job_id") or "")
        created = parse_time(str(row.get("created_at") or ""))
        if not job_id or not created:
            continue
        if job_id not in latest or created > latest[job_id][0]:
            latest[job_id] = (created, row)
    return {job_id: row for job_id, (_created, row) in latest.items()}


def outcome_status(*, projection: dict[str, Any] | None, job: dict[str, Any] | None, row: dict[str, Any] | None, receipt: dict[str, Any] | None, projection_created_at: datetime | None) -> str:
    if projection is None:
        return "NO_PROJECTION_FOR_JOB"
    if job is None:
        return "CRON_JOB_MISSING"
    receipt_ts = parse_time(str((receipt or {}).get("created_at") or ""))
    if receipt and projection_created_at and receipt_ts and receipt_ts >= projection_created_at:
        return "RECEIPT_AFTER_PROJECTION_CORRELATED"
    if row is None:
        return "NO_WRAPPER_LOG_FOUND"
    row_ts = parse_time(str(row.get("timestamp") or ""))
    if projection_created_at and row_ts and row_ts >= projection_created_at:
        return "REAL_RUN_AFTER_PROJECTION_CORRELATED"
    if row_ts:
        return "ONLY_PRE_PROJECTION_RUN_OBSERVED"
    return "WRAPPER_LOG_TIMESTAMP_UNPARSEABLE"


def summarize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "timestamp": row.get("timestamp"),
        "status": row.get("status"),
        "ok": row.get("ok"),
        "returncode": row.get("returncode"),
        "script_args": row.get("script_args"),
        "failed_models": row.get("failed_models"),
        "summary": row.get("summary"),
        "model_records": row.get("model_records"),
    }


def summarize_receipt(receipt: dict[str, Any] | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    return {
        "created_at": receipt.get("created_at"),
        "job_id": receipt.get("job_id"),
        "runtime_mode": receipt.get("runtime_mode"),
        "kernel_shim": receipt.get("kernel_shim"),
        "gate_report_id": receipt.get("gate_report_id"),
        "gate_decision": receipt.get("gate_decision"),
        "payload_status": receipt.get("payload_status"),
        "payload_ok": receipt.get("payload_ok"),
        "model_generation_attempted": receipt.get("model_generation_attempted"),
        "cron_state_mutated": receipt.get("cron_state_mutated"),
    }


def correlate_job(job_id: str, *, jobs: dict[str, dict[str, Any]], projection: dict[str, dict[str, Any]], receipts: dict[str, dict[str, Any]], projection_created_at: datetime | None, log_dir: Path) -> dict[str, Any]:
    job = jobs.get(job_id)
    proj = projection.get(job_id)
    receipt = receipts.get(job_id)
    log_path = log_dir / WRAPPER_LOGS[job_id]
    row = latest_row(read_jsonl(log_path))
    row_ts = parse_time(str(row.get("timestamp") or "")) if row else None
    status = outcome_status(projection=proj, job=job, row=row, receipt=receipt, projection_created_at=projection_created_at)
    return {
        "job_id": job_id,
        "job_name": (proj or {}).get("job_name") or (job or {}).get("name"),
        "projection_status": (proj or {}).get("projection_status"),
        "kernel_shim_detected_at_projection": (proj or {}).get("kernel_shim_detected"),
        "gate_decision_at_projection": (proj or {}).get("gate_decision"),
        "cron_last_run_at": (job or {}).get("last_run_at"),
        "cron_last_status": (job or {}).get("last_status"),
        "cron_next_run_at": (job or {}).get("next_run_at"),
        "log_path": str(log_path),
        "latest_log": summarize_row(row),
        "latest_receipt": summarize_receipt(receipt),
        "latest_log_after_projection": bool(projection_created_at and row_ts and row_ts >= projection_created_at),
        "correlation_status": status,
        "interpretation": interpretation(status, row),
    }


def interpretation(status: str, row: dict[str, Any] | None) -> str:
    if status == "RECEIPT_AFTER_PROJECTION_CORRELATED":
        return "direct wrapper-run receipt after projection confirms kernel/gate metadata capture"
    if status == "REAL_RUN_AFTER_PROJECTION_CORRELATED":
        if row and row.get("ok") is True:
            return "scheduler ran wrapper after projection and wrapper outcome was ok"
        return "scheduler ran wrapper after projection but wrapper outcome was not ok"
    if status == "ONLY_PRE_PROJECTION_RUN_OBSERVED":
        return "no post-projection scheduled wrapper run has occurred yet"
    if status == "NO_WRAPPER_LOG_FOUND":
        return "wrapper has no local run log to correlate"
    return status.lower()


def build_report(*, jobs_path: Path, projection_path: Path | None, log_dir: Path, receipt_ledger: Path) -> dict[str, Any]:
    projection_report = load_json(projection_path) if projection_path else None
    projection_created_at = parse_time(str((projection_report or {}).get("created_at") or ""))
    jobs = load_jobs(jobs_path)
    projections = projection_by_job(projection_report or {})
    receipts = latest_receipts_by_job(receipt_ledger)
    outcomes = [
        correlate_job(cron_wrappers.DAILY_FULL_JOB_ID, jobs=jobs, projection=projections, receipts=receipts, projection_created_at=projection_created_at, log_dir=log_dir),
        correlate_job(cron_wrappers.EVERY4H_JOB_ID, jobs=jobs, projection=projections, receipts=receipts, projection_created_at=projection_created_at, log_dir=log_dir),
    ]
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o["correlation_status"]] = counts.get(o["correlation_status"], 0) + 1
    if counts.get("RECEIPT_AFTER_PROJECTION_CORRELATED", 0) >= 1:
        status = "RECEIPT_CORRELATION_PARTIAL_READY"
    elif counts.get("REAL_RUN_AFTER_PROJECTION_CORRELATED", 0) >= 1:
        status = "REAL_RUN_CORRELATION_PARTIAL_READY"
    elif all(o["correlation_status"] == "ONLY_PRE_PROJECTION_RUN_OBSERVED" for o in outcomes):
        status = "AWAITING_POST_PROJECTION_SCHEDULED_RUN"
    else:
        status = "RUN_CORRELATION_DEGRADED"
    return {
        "report_id": "workflow_kernel_cron_run_correlation_20260613_phase33",
        "created_at": iso(utc_now()),
        "status": status,
        "scope": "read-only correlation between scheduler state, kernel-shim projection, and wrapper JSONL outcomes",
        "jobs_path": str(jobs_path),
        "projection_path": str(projection_path) if projection_path else None,
        "receipt_ledger": str(receipt_ledger),
        "projection_created_at": (projection_report or {}).get("created_at"),
        "log_dir": str(log_dir),
        "correlation_counts": counts,
        "outcomes": outcomes,
        "safe_boundaries": [
            "no cron/jobs.json mutation",
            "no cron job execution or run trigger",
            "no model generation call",
            "no external message send",
            "no credential read/write",
            "read-only log/projection correlation only",
        ],
        "next_actions": [
            "wait for the daily-full wrapper's next scheduled run before claiming full two-wrapper runtime correlation",
            "add durable wrapper-run ledger metadata if future correlation needs gate_report_id/kernal_shim in each run receipt",
            "keep global runtime switch disabled until a separate explicit enable gate",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_cron_run_correlation_20260613_phase33.json"
    md_path = out_dir / "workflow_kernel_cron_run_correlation_20260613_phase33.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Cron Run Correlation — Phase 33",
        "",
        f"Status: **{report['status']}**",
        f"Correlation counts: `{json.dumps(report['correlation_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"Projection: `{report['projection_path']}`",
        "",
        "## Outcomes",
    ]
    for o in report["outcomes"]:
        latest = o.get("latest_log") or {}
        lines.append(
            f"- **{o['job_id']} {o['job_name']}** — `{o['correlation_status']}` "
            f"cron_last={o['cron_last_run_at']} log_ts={latest.get('timestamp')} log_status={latest.get('status')} interpretation={o['interpretation']}"
        )
    lines += ["", "## Safe boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS))
    ap.add_argument("--projection", default=None, help="Projection report path. Defaults to newest workflow_kernel_cron_runtime_projection_*.json")
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    ap.add_argument("--receipt-ledger", default=str(DEFAULT_RECEIPT_LEDGER))
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    projection_path = Path(args.projection) if args.projection else newest_path(DEFAULT_PROJECTION_GLOB)
    report = build_report(jobs_path=Path(args.jobs), projection_path=projection_path, log_dir=Path(args.log_dir), receipt_ledger=Path(args.receipt_ledger))
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: {report['correlation_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
