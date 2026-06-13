#!/usr/bin/env python3
"""Compile read-only scheduler-level projection for kernel-shim cron wrappers.

Phase 32 boundary: project what the scheduler will execute and which local
provider-health/probe artifacts the opted-in wrappers will consume. This does
not mutate cron jobs, run cron jobs, call model generation, or send messages.
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
DEFAULT_GATE_GLOB = ROOT / "docs" / "private" / "workflow-kernel-cron-provider-health-gate" / "cron_provider_health_gate_*.json"
DEFAULT_PROBE_GLOB = ROOT / "docs" / "private" / "workflow-kernel-provider-network-probe" / "provider_network_probe_*.json"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-cron-runtime-projection"
DEFAULT_STALE_HOURS = 24.0

WRAPPER_REGISTRY = [
    {
        "job_id": cron_wrappers.DAILY_FULL_JOB_ID,
        "job_name": cron_wrappers.DAILY_FULL_JOB_NAME,
        "script": "multi-model-smoke-daily-full-wrapper.py",
        "allowed_scripts": ["multi-model-smoke-daily-full-wrapper.py", "workflow-kernel-multi-model-smoke-daily-full-runner.py"],
        "kernel_module": "workflow_kernel.cron_wrappers",
        "expected_runtime_mode": "typed_skip_or_original_run_or_scheduler_native",
    },
    {
        "job_id": cron_wrappers.EVERY4H_JOB_ID,
        "job_name": cron_wrappers.EVERY4H_JOB_NAME,
        "script": "multi-model-smoke-every4h-wrapper.py",
        "allowed_scripts": ["multi-model-smoke-every4h-wrapper.py", "workflow-kernel-multi-model-smoke-every4h-runner.py"],
        "kernel_module": "workflow_kernel.cron_wrappers",
        "expected_runtime_mode": "provider_exclusion_plan_or_original_run_or_scheduler_native",
    },
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def newest_path(pattern: str | Path) -> Path | None:
    matches = [Path(p) for p in glob.glob(str(pattern))]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def load_json(path: Path) -> Any:
    return read_json_artifact(path)


def load_jobs(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        raise SystemExit(f"invalid jobs shape: {path}")
    return [j for j in jobs if isinstance(j, dict)]


def job_map(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(j.get("id") or j.get("job_id")): j for j in jobs if j.get("id") or j.get("job_id")}


def decision_map(gate: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not gate:
        return {}
    return {str(d.get("job_id")): d for d in gate.get("decisions", []) if isinstance(d, dict) and d.get("job_id")}


def age_projection(artifact: dict[str, Any] | None, *, now: datetime, stale_hours: float) -> dict[str, Any]:
    if not artifact:
        return {"exists": False, "created_at": None, "age_seconds": None, "stale": True, "stale_threshold_hours": stale_hours}
    created = parse_time(str(artifact.get("created_at") or ""))
    if not created:
        return {"exists": True, "created_at": artifact.get("created_at"), "age_seconds": None, "stale": True, "stale_threshold_hours": stale_hours, "reason": "missing_or_unparseable_created_at"}
    age = max(0.0, (now - created).total_seconds())
    return {
        "exists": True,
        "created_at": artifact.get("created_at"),
        "age_seconds": round(age, 3),
        "stale": age > stale_hours * 3600,
        "stale_threshold_hours": stale_hours,
    }


def wrapper_uses_kernel(script_path: Path) -> bool:
    if not script_path.exists():
        return False
    text = script_path.read_text(encoding="utf-8")
    return "workflow_kernel" in text and "cron_wrappers" in text


def endpoint_statuses(probe: dict[str, Any] | None) -> dict[str, str]:
    statuses: dict[str, str] = {}
    if not probe:
        return statuses
    for row in probe.get("results", []):
        if isinstance(row, dict) and row.get("id"):
            statuses[str(row["id"])] = str(row.get("classification"))
    return statuses


def project_wrapper(entry: dict[str, Any], *, jobs: dict[str, dict[str, Any]], decisions: dict[str, dict[str, Any]], script_dir: Path) -> dict[str, Any]:
    job_id = entry["job_id"]
    job = jobs.get(job_id)
    decision = decisions.get(job_id)
    script_name = str((job or {}).get("script") or entry["script"])
    script_path = script_dir / script_name
    allowed_scripts = set(entry.get("allowed_scripts") or [entry["script"]])
    return {
        "job_id": job_id,
        "job_name": entry["job_name"],
        "cron_job_present": job is not None,
        "cron_enabled": job.get("enabled") if job else None,
        "cron_state": job.get("state") if job else None,
        "last_status": job.get("last_status") if job else None,
        "next_run_at": job.get("next_run_at") if job else None,
        "script": script_name,
        "expected_scripts": sorted(allowed_scripts),
        "script_allowed": script_name in allowed_scripts,
        "script_path": str(script_path),
        "script_exists": script_path.exists(),
        "kernel_shim_declared": entry["kernel_module"],
        "kernel_shim_detected": wrapper_uses_kernel(script_path),
        "expected_runtime_mode": entry["expected_runtime_mode"],
        "gate_decision": decision.get("decision") if decision else None,
        "gate_target_endpoint": decision.get("target_endpoint") if decision else None,
        "gate_endpoint_classification": decision.get("endpoint_classification") if decision else None,
        "degraded_endpoints": decision.get("degraded_endpoints") if decision else None,
        "projection_status": projection_status(job, script_path, decision, script_name in allowed_scripts),
    }


def projection_status(job: dict[str, Any] | None, script_path: Path, decision: dict[str, Any] | None, script_allowed: bool = True) -> str:
    if job is None:
        return "CRON_JOB_MISSING"
    if not script_allowed:
        return "CRON_JOB_SCRIPT_UNEXPECTED"
    if not script_path.exists():
        return "WRAPPER_SCRIPT_MISSING"
    if not wrapper_uses_kernel(script_path):
        return "WRAPPER_NOT_USING_KERNEL_SHIM"
    if decision is None:
        if job.get("last_status") == "error":
            return "NO_GATE_DECISION_FOR_WRAPPER"
        return "KERNEL_SHIM_NO_GATE_DECISION_NEEDED"
    return "KERNEL_SHIM_SCHEDULER_READBACK_READY"


def build_report(
    *,
    jobs_path: Path,
    gate_path: Path | None,
    probe_path: Path | None,
    script_dir: Path,
    stale_hours: float,
) -> dict[str, Any]:
    now = utc_now()
    jobs = load_jobs(jobs_path)
    gate = load_json(gate_path) if gate_path else None
    probe = load_json(probe_path) if probe_path else None
    decisions = decision_map(gate)
    projections = [project_wrapper(e, jobs=job_map(jobs), decisions=decisions, script_dir=script_dir) for e in WRAPPER_REGISTRY]
    counts: dict[str, int] = {}
    for p in projections:
        counts[p["projection_status"]] = counts.get(p["projection_status"], 0) + 1
    gate_age = age_projection(gate, now=now, stale_hours=stale_hours)
    probe_age = age_projection(probe, now=now, stale_hours=stale_hours)
    ready_statuses = {"KERNEL_SHIM_SCHEDULER_READBACK_READY", "KERNEL_SHIM_NO_GATE_DECISION_NEEDED"}
    all_ready = all(p["projection_status"] in ready_statuses for p in projections)
    artifacts_fresh = not gate_age["stale"] and not probe_age["stale"]
    status = "SCHEDULER_READBACK_PROJECTION_READY" if all_ready and artifacts_fresh else "SCHEDULER_READBACK_PROJECTION_DEGRADED"
    return {
        "report_id": "workflow_kernel_cron_runtime_projection_20260613_phase32",
        "created_at": iso(now),
        "status": status,
        "scope": "read-only scheduler-level projection for opt-in kernel-shim cron wrappers",
        "jobs_path": str(jobs_path),
        "gate_path": str(gate_path) if gate_path else None,
        "probe_path": str(probe_path) if probe_path else None,
        "gate_artifact": age_projection(gate, now=now, stale_hours=stale_hours),
        "probe_artifact": age_projection(probe, now=now, stale_hours=stale_hours),
        "endpoint_statuses": endpoint_statuses(probe),
        "wrapper_count": len(projections),
        "projection_counts": counts,
        "wrapper_projections": projections,
        "runtime_switch_enabled_by_default": False,
        "safe_boundaries": [
            "no cron/jobs.json mutation",
            "no cron job execution",
            "no model generation call",
            "no external message send",
            "no credential read/write",
            "read-only projection only",
        ],
        "next_actions": [
            "observe next real scheduled wrapper run before scheduler-native execution changes",
            "if artifacts become stale, refresh provider probe and health gate before trusting wrapper decisions",
            "keep global runtime switch disabled until a separate explicit enable gate",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_cron_runtime_projection_20260613_phase32.json"
    md_path = out_dir / "workflow_kernel_cron_runtime_projection_20260613_phase32.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Cron Runtime Projection — Phase 32",
        "",
        f"Status: **{report['status']}**",
        f"Projection counts: `{json.dumps(report['projection_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"Gate path: `{report['gate_path']}`",
        f"Probe path: `{report['probe_path']}`",
        "",
        "## Wrapper projections",
    ]
    for p in report["wrapper_projections"]:
        lines.append(
            f"- **{p['job_id']} {p['job_name']}** — `{p['projection_status']}` "
            f"kernel={p['kernel_shim_detected']} decision={p['gate_decision']} next={p['next_run_at']}"
        )
    lines += ["", "## Artifact freshness", "```json", json.dumps({"gate": report["gate_artifact"], "probe": report["probe_artifact"]}, ensure_ascii=False, indent=2, sort_keys=True), "```"]
    lines += ["", "## Safe boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS))
    ap.add_argument("--gate", default=None, help="Gate report path. Defaults to newest cron_provider_health_gate_*.json")
    ap.add_argument("--probe", default=None, help="Probe report path. Defaults to newest provider_network_probe_*.json")
    ap.add_argument("--script-dir", default=str(Path.home() / ".hermes" / "scripts"))
    ap.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    gate_path = Path(args.gate) if args.gate else newest_path(DEFAULT_GATE_GLOB)
    probe_path = Path(args.probe) if args.probe else newest_path(DEFAULT_PROBE_GLOB)
    report = build_report(
        jobs_path=Path(args.jobs),
        gate_path=gate_path,
        probe_path=probe_path,
        script_dir=Path(args.script_dir),
        stale_hours=args.stale_hours,
    )
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: {report['projection_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
