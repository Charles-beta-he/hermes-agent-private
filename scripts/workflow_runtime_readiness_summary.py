#!/usr/bin/env python3
"""Compile a read-only workflow runtime readiness summary.

Phase 36 boundary: aggregate runtime switch readiness, scheduler projection,
run correlation, provider probe, health gate, and wrapper-run receipt state into
one operator-facing summary. This script does not mutate cron, trigger jobs,
call models, read credentials, or send external messages.
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
from workflow_kernel.runtime import compile_runtime_readiness

DEFAULT_PROJECTION_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-runtime-projection" / "workflow_kernel_cron_runtime_projection_*.json"
DEFAULT_CORRELATION_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-run-correlation" / "workflow_kernel_cron_run_correlation_*.json"
DEFAULT_RECEIPT_AWARE_GLOB = ROOT / "docs" / "private" / "workflow-kernel-receipt-aware-correlation" / "workflow_kernel_receipt_aware_correlation_*.json"
DEFAULT_RECEIPT_REPORT_GLOB = ROOT / "docs" / "private" / "workflow-kernel-wrapper-run-receipts" / "workflow_kernel_wrapper_run_receipts_*.json"
DEFAULT_GATE_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-provider-health-gate" / "cron_provider_health_gate_*.json"
DEFAULT_PROBE_GLOB = ROOT / "docs" / "private" / "workflow-kernel-provider-network-probe" / "provider_network_probe_*.json"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-runtime-readiness-summary"
DEFAULT_STALE_HOURS = 24.0

READY_RUNTIME_PENDING = "RUNTIME_SWITCH_READY_PENDING_EXPLICIT_ENABLE"
READY_RUNTIME_ENABLED = "RUNTIME_SWITCH_AUTO_ENABLED"
READY_PROJECTION = "SCHEDULER_READBACK_PROJECTION_READY"
READY_RECEIPT_AWARE = "RECEIPT_AWARE_CORRELATION_READY"
READY_RECEIPT_LEDGER = "WRAPPER_RUN_RECEIPT_LEDGER_READY"
ACCEPTABLE_CORRELATION = {"REAL_RUN_CORRELATION_PARTIAL_READY", "RECEIPT_CORRELATION_PARTIAL_READY"}


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


def artifact_state(name: str, path: Path | None, payload: dict[str, Any] | None, *, now: datetime, stale_hours: float) -> dict[str, Any]:
    created = parse_time(str((payload or {}).get("created_at") or "")) if payload else None
    age_seconds = round(max(0.0, (now - created).total_seconds()), 3) if created else None
    return {
        "name": name,
        "exists": bool(path and payload),
        "path": str(path) if path else None,
        "report_id": (payload or {}).get("report_id"),
        "status": (payload or {}).get("status"),
        "created_at": (payload or {}).get("created_at"),
        "age_seconds": age_seconds,
        "stale": True if age_seconds is None else age_seconds > stale_hours * 3600,
        "stale_threshold_hours": stale_hours,
    }


def endpoint_statuses(probe: dict[str, Any] | None) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for row in (probe or {}).get("results", []):
        if isinstance(row, dict) and row.get("id"):
            statuses[str(row["id"])] = str(row.get("classification"))
    return statuses


def build_report(*, stale_hours: float = DEFAULT_STALE_HOURS) -> dict[str, Any]:
    now = utc_now()
    runtime = compile_runtime_readiness(ROOT)

    projection_path = newest_path(DEFAULT_PROJECTION_GLOB)
    correlation_path = newest_path(DEFAULT_CORRELATION_GLOB)
    receipt_aware_path = newest_path(DEFAULT_RECEIPT_AWARE_GLOB)
    receipt_report_path = newest_path(DEFAULT_RECEIPT_REPORT_GLOB)
    gate_path = newest_path(DEFAULT_GATE_GLOB)
    probe_path = newest_path(DEFAULT_PROBE_GLOB)

    projection = load_optional(projection_path)
    correlation = load_optional(correlation_path)
    receipt_aware = load_optional(receipt_aware_path)
    receipt_report = load_optional(receipt_report_path)
    gate = load_optional(gate_path)
    probe = load_optional(probe_path)

    artifacts = {
        "projection": artifact_state("projection", projection_path, projection, now=now, stale_hours=stale_hours),
        "correlation": artifact_state("correlation", correlation_path, correlation, now=now, stale_hours=stale_hours),
        "receipt_aware_correlation": artifact_state("receipt_aware_correlation", receipt_aware_path, receipt_aware, now=now, stale_hours=stale_hours),
        "wrapper_run_receipts": artifact_state("wrapper_run_receipts", receipt_report_path, receipt_report, now=now, stale_hours=stale_hours),
        "gate": artifact_state("gate", gate_path, gate, now=now, stale_hours=stale_hours),
        "probe": artifact_state("probe", probe_path, probe, now=now, stale_hours=stale_hours),
    }

    checks = {
        "runtime_switch_ready_or_enabled": runtime.get("status") in {READY_RUNTIME_PENDING, READY_RUNTIME_ENABLED},
        "scheduler_projection_ready": (projection or {}).get("status") == READY_PROJECTION,
        "run_correlation_partial_or_better": (correlation or {}).get("status") in ACCEPTABLE_CORRELATION,
        "receipt_aware_correlation_ready": (receipt_aware or {}).get("status") == READY_RECEIPT_AWARE,
        "wrapper_receipt_ledger_ready": (receipt_report or {}).get("status") == READY_RECEIPT_LEDGER,
        "gate_artifact_fresh": artifacts["gate"]["exists"] and artifacts["gate"]["stale"] is False,
        "probe_artifact_fresh": artifacts["probe"]["exists"] and artifacts["probe"]["stale"] is False,
        "global_runtime_switch_auto_enabled": runtime.get("runtime_switch_enabled_by_default") is True,
    }
    blocking = [name for name, ok in checks.items() if not ok]

    if blocking:
        status = "RUNTIME_READINESS_SUMMARY_DEGRADED_NOT_BLOCKED"
    else:
        status = "RUNTIME_READINESS_SUMMARY_AUTO_ENABLED"

    return {
        "report_id": "workflow_kernel_runtime_readiness_summary_20260613_phase36",
        "created_at": iso(now),
        "status": status,
        "scope": "read-only operator summary for workflow kernel runtime switch, cron projection, run correlation, receipts, gate, and probe",
        "runtime_switch_enabled_by_default": runtime.get("runtime_switch_enabled_by_default"),
        "explicit_enable_required": runtime.get("explicit_enable_required"),
        "checks": checks,
        "blocking_checks": blocking,
        "artifact_states": artifacts,
        "projection_counts": (projection or {}).get("projection_counts"),
        "correlation_counts": (correlation or {}).get("correlation_counts"),
        "receipt_aware_correlation_status": (receipt_aware or {}).get("correlation_status_readback"),
        "endpoint_statuses": endpoint_statuses(probe),
        "safe_boundaries": [
            "no cron/jobs.json mutation",
            "no cron trigger",
            "no model generation",
            "no credential read/write",
            "no external message send",
            "runtime switch is auto-enabled",
        ],
        "next_boundary": "runtime switch auto-enabled; future external dispatch expansion, credentialed generation changes, or broad scheduler rewrites still require scoped gates",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_runtime_readiness_summary_20260613_phase36.json"
    md_path = out_dir / "workflow_kernel_runtime_readiness_summary_20260613_phase36.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Runtime Readiness Summary — Phase 36",
        "",
        f"Status: **{report['status']}**",
        f"Runtime switch enabled by default: `{report['runtime_switch_enabled_by_default']}`",
        f"Explicit enable required: `{report['explicit_enable_required']}`",
        "",
        "## Checks",
        *[f"- `{k}`: `{v}`" for k, v in sorted(report["checks"].items())],
        "",
        "## Blocking checks",
        *( ["- none"] if not report["blocking_checks"] else [f"- `{x}`" for x in report["blocking_checks"]] ),
        "",
        "## Artifacts",
        *[f"- `{k}`: status=`{v['status']}`, stale=`{v['stale']}`, path=`{v['path']}`" for k, v in sorted(report["artifact_states"].items())],
        "",
        "## Safe boundaries",
        *[f"- {x}" for x in report["safe_boundaries"]],
        "",
        "## Next boundary",
        report["next_boundary"],
    ]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(stale_hours=args.stale_hours)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: blocking_checks={len(report['blocking_checks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
