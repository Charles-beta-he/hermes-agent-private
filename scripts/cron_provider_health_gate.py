#!/usr/bin/env python3
"""Compile read-only cron/provider health-gate decisions.

Phase 24 guardrail: this script consumes existing local evidence (cron jobs and
provider network probe ledger) and emits decisions. It does not mutate cron jobs,
does not rerun jobs, does not call LLM generation APIs, and does not send
messages.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_kernel.io import read_json_artifact, write_json_artifact

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_PROBE = Path("docs/private/workflow-kernel-provider-network-probe/provider_network_probe_20260613_phase23.json")
DEFAULT_TRIAGE = Path("docs/private/workflow-kernel-cron-provider-triage/cron_provider_triage_20260613_phase22.json")
DEFAULT_OUT = Path("docs/private/workflow-kernel-cron-provider-health-gate")

PROVIDER_ENDPOINTS = {
    "openai": "openai",
    "openai-codex": "openai",
    "codex": "openai",
    "deepseek": "deepseek",
    "xiaomi": "xiaomi",
    "mimo": "xiaomi",
    "google-gemini-cli": "google_gemini",
    "gemini": "google_gemini",
    "telegram": "telegram",
}

MODEL_PROVIDER_HINTS = {
    "mimo": "xiaomi",
    "gpt": "openai",
    "codex": "openai",
    "deepseek": "deepseek",
    "gemini": "google_gemini",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return read_json_artifact(path)


def load_jobs(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        raise SystemExit(f"invalid jobs shape: {path}")
    return [j for j in jobs if isinstance(j, dict)]


def endpoint_statuses(probe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for r in probe.get("results", []):
        if isinstance(r, dict) and r.get("id"):
            statuses[str(r["id"])] = r
    return statuses


def infer_endpoint(job: dict[str, Any]) -> str | None:
    provider = str(job.get("provider") or "").strip().lower()
    if provider in PROVIDER_ENDPOINTS:
        return PROVIDER_ENDPOINTS[provider]
    model = str(job.get("model") or "").strip().lower()
    for hint, endpoint in MODEL_PROVIDER_HINTS.items():
        if hint in model:
            return endpoint
    deliver = str(job.get("deliver") or "").strip().lower()
    if deliver.startswith("telegram"):
        return "telegram"
    name = str(job.get("name") or "").strip().lower()
    if "telegram" in name or "tg" in name:
        return "telegram"
    if "upstream" in name or "ratchet" in name:
        return "github"
    return None


def triage_by_job_id(triage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(j.get("job_id")): j for j in triage.get("failed_jobs", []) if j.get("job_id")}


def is_reachable(endpoint: str | None, statuses: dict[str, dict[str, Any]]) -> bool | None:
    if not endpoint:
        return None
    record = statuses.get(endpoint)
    if not record:
        return None
    return record.get("classification") == "reachable"


def decide_job(job: dict[str, Any], triage_item: dict[str, Any] | None, statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    endpoint = infer_endpoint(job)
    triage_class = (triage_item or {}).get("primary_class")
    if triage_class == "telegram_delivery":
        endpoint = "telegram"
    elif triage_class == "script_network_failure":
        endpoint = endpoint or "github"
    reachable = is_reachable(endpoint, statuses)
    endpoint_record = statuses.get(endpoint) if endpoint else None

    if triage_class == "telegram_delivery" or endpoint == "telegram":
        if reachable is False:
            decision = "skip_delivery_health_degraded"
            reason = "telegram endpoint degraded in latest provider network probe"
        else:
            decision = "retry_allowed_by_provider_health_manual_only"
            reason = "telegram endpoint reachable; retry must still go through governed notification ledger"
    elif triage_class == "script_network_failure" or endpoint == "github":
        if reachable is False:
            decision = "skip_fetch_use_no_fetch_fallback"
            reason = "github endpoint degraded; use no-fetch/stale-ref fallback"
        else:
            decision = "retry_allowed_by_provider_health"
            reason = "github endpoint reachable; script may retry fetch in governed slice"
    elif reachable is False:
        klass = endpoint_record.get("classification") if endpoint_record else "unknown"
        decision = "skip_provider_health_degraded"
        reason = f"target endpoint {endpoint} classified as {klass}"
    elif reachable is True:
        decision = "retry_allowed_by_provider_health"
        reason = f"target endpoint {endpoint} reachable; provider generation failure is not explained by edge reachability alone"
    else:
        degraded = [k for k, v in statuses.items() if v.get("classification") != "reachable"]
        if triage_class == "provider_generation" and degraded:
            decision = "run_with_degraded_provider_exclusions"
            reason = f"job provider is unpinned/unknown; exclude degraded endpoints {degraded} if a retry is later authorized"
        else:
            decision = "manual_investigation_required"
            reason = "no endpoint inference available"

    return {
        "job_id": job.get("id") or job.get("job_id"),
        "name": job.get("name"),
        "provider": job.get("provider"),
        "model": job.get("model"),
        "script": job.get("script"),
        "deliver": job.get("deliver"),
        "last_status": job.get("last_status"),
        "triage_class": triage_class,
        "target_endpoint": endpoint,
        "endpoint_classification": endpoint_record.get("classification") if endpoint_record else None,
        "decision": decision,
        "reason": reason,
    }


def build_report(jobs_path: Path, probe_path: Path, triage_path: Path) -> dict[str, Any]:
    jobs = load_jobs(jobs_path)
    probe = load_json(probe_path)
    triage = load_json(triage_path)
    statuses = endpoint_statuses(probe)
    triage_map = triage_by_job_id(triage)
    failed_jobs = [j for j in jobs if j.get("last_status") == "error"]
    decisions = [decide_job(j, triage_map.get(str(j.get("id") or j.get("job_id"))), statuses) for j in failed_jobs]
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    status = "PASS" if not decisions else "GATE_COMPILED_NO_MUTATION"
    return {
        "report_id": "cron_provider_health_gate_20260613_phase24",
        "created_at": utc_now(),
        "status": status,
        "scope": "read-only provider health gate decisions for failed cron jobs",
        "jobs_path": str(jobs_path),
        "probe_path": str(probe_path),
        "triage_path": str(triage_path),
        "failed_job_count": len(decisions),
        "decision_counts": counts,
        "degraded_endpoints": [k for k, v in statuses.items() if v.get("classification") != "reachable"],
        "decisions": decisions,
        "safe_boundaries": [
            "no cron job mutation",
            "no failed job rerun",
            "no LLM generation call",
            "no external message send",
            "no provider credential check",
        ],
        "next_actions": [
            "wire cron wrappers to read this gate before provider generation in a later opt-in phase",
            "skip only the target degraded provider; do not globally disable cron",
            "use governed notification ledger for Telegram retry even when endpoint is reachable",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "cron_provider_health_gate_20260613_phase24.json"
    md_path = out_dir / "cron_provider_health_gate_20260613_phase24.md"
    artifact = write_json_artifact(json_path, report)
    sha = artifact["sha256"]
    lines = [
        "# Hermes Cron Provider Health Gate — Phase 24",
        "",
        f"Status: **{report['status']}**",
        f"Failed jobs evaluated: **{report['failed_job_count']}**",
        f"Decision counts: `{json.dumps(report['decision_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"Degraded endpoints: `{json.dumps(report['degraded_endpoints'], ensure_ascii=False)}`",
        "",
        "## Decisions",
    ]
    for d in report["decisions"]:
        lines.append(f"- **{d['job_id']} {d['name']}** — `{d['decision']}` endpoint={d['target_endpoint']} reason={d['reason']}")
    lines += ["", "## Safe Boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next Actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS))
    ap.add_argument("--probe", default=str(DEFAULT_PROBE))
    ap.add_argument("--triage", default=str(DEFAULT_TRIAGE))
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(Path(args.jobs), Path(args.probe), Path(args.triage))
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: {report['decision_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
