#!/usr/bin/env python3
"""Read-only drift watcher for workflow-kernel projection and receipt evidence.

Phase 40 boundary: detect drift only. No cron triggers, scheduler mutation, model
calls, credential reads, or external delivery.
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

from workflow_kernel.io import read_json_artifact, write_json_artifact

DEFAULT_PROJECTION_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-runtime-projection" / "workflow_kernel_cron_runtime_projection_*.json"
DEFAULT_CORRELATION_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-run-correlation" / "workflow_kernel_cron_run_correlation_*.json"
DEFAULT_SUMMARY_GLOB = ROOT / "docs" / "private" / "workflow-kernel-runtime-readiness-summary" / "workflow_kernel_runtime_readiness_summary_*.json"
DEFAULT_RECEIPT_LEDGER = Path.home() / ".hermes" / "state" / "cron-provider-health-gate" / "wrapper_run_receipts.jsonl"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-drift-watch"
DEFAULT_STALE_HOURS = 24.0
WATCHED_JOBS = {"42baeebb646c": "multi-model-smoke-daily-full", "2c14ca2a9bd2": "multi-model-smoke-every4h"}


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


def load_optional(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    obj = read_json_artifact(path)
    return obj if isinstance(obj, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows=[]
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj=json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def artifact_drift(name: str, path: Path | None, payload: dict[str, Any] | None, *, now: datetime, stale_hours: float) -> dict[str, Any]:
    created=parse_time(str((payload or {}).get("created_at") or "")) if payload else None
    age=max(0.0,(now-created).total_seconds()) if created else None
    stale=True if age is None else age>stale_hours*3600
    return {"name":name,"path":str(path) if path else None,"exists":bool(path and payload),"status":(payload or {}).get("status"),"created_at":(payload or {}).get("created_at"),"age_seconds":round(age,3) if age is not None else None,"stale":stale}


def latest_receipts(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for row in read_jsonl(path):
        job=str(row.get("job_id") or "")
        ts=parse_time(str(row.get("created_at") or ""))
        if job and ts and (job not in latest or ts>latest[job][0]):
            latest[job]=(ts,row)
    return {job: row for job, (_ts,row) in latest.items()}


def build_watch(*, receipt_ledger: Path = DEFAULT_RECEIPT_LEDGER, stale_hours: float = DEFAULT_STALE_HOURS) -> dict[str, Any]:
    now=utc_now()
    p_path=newest_path(DEFAULT_PROJECTION_GLOB); c_path=newest_path(DEFAULT_CORRELATION_GLOB); s_path=newest_path(DEFAULT_SUMMARY_GLOB)
    projection=load_optional(p_path); correlation=load_optional(c_path); summary=load_optional(s_path)
    receipts=latest_receipts(receipt_ledger)
    artifacts={
        "projection": artifact_drift("projection", p_path, projection, now=now, stale_hours=stale_hours),
        "correlation": artifact_drift("correlation", c_path, correlation, now=now, stale_hours=stale_hours),
        "summary": artifact_drift("summary", s_path, summary, now=now, stale_hours=stale_hours),
    }
    job_receipts={job:{"job_name":name,"receipt_present":job in receipts,"latest_receipt_created_at":receipts.get(job,{}).get("created_at"),"runtime_mode":receipts.get(job,{}).get("runtime_mode"),"kernel_shim":receipts.get(job,{}).get("kernel_shim")} for job,name in WATCHED_JOBS.items()}
    drift=[]
    for k,v in artifacts.items():
        if not v["exists"]: drift.append(f"{k}_missing")
        elif v["stale"]: drift.append(f"{k}_stale")
    missing_receipts=[job for job,row in job_receipts.items() if not row["receipt_present"]]
    if missing_receipts:
        drift.append("production_receipts_not_complete")
    status="DRIFT_WATCH_CLEAR_READ_ONLY" if not drift else "DRIFT_WATCH_DEGRADED_NOT_BLOCKED"
    return {"report_id":"workflow_kernel_drift_watch_20260613_phase40","created_at":iso(now),"status":status,"drift_signals":drift,"artifact_states":artifacts,"receipt_ledger":str(receipt_ledger),"job_receipts":job_receipts,"missing_receipt_jobs":missing_receipts,"safe_boundaries":["no cron trigger","no cron/jobs.json mutation","no model generation","no credential read/write","no external delivery"],"next_boundary":"If only missing production receipts remain, wait for natural scheduled runs or explicitly authorize B/C."}


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str,str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path=out_dir/"workflow_kernel_drift_watch_20260613_phase40.json"; md_path=out_dir/"workflow_kernel_drift_watch_20260613_phase40.md"
    artifact=write_json_artifact(json_path, report)
    lines=["# Workflow Kernel Drift Watch — Phase 40","",f"Status: **{report['status']}**","","## Drift signals",*( ["- none"] if not report["drift_signals"] else [f"- `{x}`" for x in report["drift_signals"]] ),"","## Job receipts"]
    for job,row in report["job_receipts"].items():
        lines.append(f"- `{job}` {row['job_name']}: present=`{row['receipt_present']}`, created_at=`{row['latest_receipt_created_at']}`")
    lines.extend(["","## Safe boundaries",*[f"- {x}" for x in report["safe_boundaries"]]])
    md_path.write_text("\n".join(lines)+"\n")
    return {"json":str(json_path),"md":str(md_path),"sha256":artifact["sha256"]}


def main(argv: list[str] | None=None) -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--receipt-ledger",default=str(DEFAULT_RECEIPT_LEDGER)); ap.add_argument("--stale-hours",type=float,default=DEFAULT_STALE_HOURS); ap.add_argument("--write-report",action="store_true"); ap.add_argument("--out-dir",default=str(DEFAULT_OUT)); ap.add_argument("--json",action="store_true")
    args=ap.parse_args(argv); report=build_watch(receipt_ledger=Path(args.receipt_ledger), stale_hours=args.stale_hours)
    if args.write_report: report["artifacts"]=write_report(report, Path(args.out_dir))
    if args.json: print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True))
    else: print(f"{report['status']}: drift_signals={len(report['drift_signals'])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
