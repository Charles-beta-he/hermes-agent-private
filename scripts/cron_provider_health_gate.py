#!/usr/bin/env python3
"""Provider health gate for Hermes cron/provider jobs.

Phase 24 guardrail: read-only decision compiler. It reads the latest provider
network probe ledger plus cron jobs, then emits local run/skip decisions. It
never mutates cron state, never calls model generation APIs, and never sends
messages.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_PROBE_GLOB = "docs/private/workflow-kernel-provider-network-probe/provider_network_probe_*.json"
DEFAULT_OUT = Path("docs/private/workflow-kernel-provider-health-gate")

PROVIDER_TO_ENDPOINT = {
    "openai": "openai",
    "openai-codex": "openai",
    "custom:openai": "openai",
    "deepseek": "deepseek",
    "xiaomi": "xiaomi",
    "mimo": "xiaomi",
    "google-gemini-cli": "google_gemini",
    "gemini": "google_gemini",
    "telegram": "telegram",
}
MODEL_TO_PROVIDER_HINT = {
    "mimo": "xiaomi",
    "mi": "xiaomi",
    "gpt": "openai",
    "codex": "openai-codex",
    "deepseek": "deepseek",
    "gemini": "google-gemini-cli",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_jobs(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        raise SystemExit(f"invalid jobs file shape: {path}")
    return [j for j in jobs if isinstance(j, dict)]


def latest_probe_path(pattern: str) -> Path:
    matches = [Path(p) for p in glob.glob(pattern)]
    if not matches:
        raise SystemExit(f"no provider network probe found for pattern: {pattern}")
    return max(matches, key=lambda p: p.stat().st_mtime)


def endpoint_health_map(probe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in probe.get("results", []):
        if isinstance(row, dict) and row.get("id"):
            result[str(row["id"])] = row
    return result


def infer_provider(job: dict[str, Any]) -> str | None:
    provider = job.get("provider")
    if provider:
        return str(provider)
    model = str(job.get("model") or "").lower()
    for needle, prov in MODEL_TO_PROVIDER_HINT.items():
        if needle in model:
            return prov
    name_script = f"{job.get('name') or ''} {job.get('script') or ''}".lower()
    if "telegram" in name_script:
        return "telegram"
    if "mimo" in name_script or "xiaomi" in name_script:
        return "xiaomi"
    if "gemini" in name_script:
        return "google-gemini-cli"
    if "deepseek" in name_script:
        return "deepseek"
    if "multi-model" in name_script or "multimodel" in name_script:
        return "multi_provider"
    return None


def provider_endpoint(provider: str | None) -> str | None:
    if not provider:
        return None
    return PROVIDER_TO_ENDPOINT.get(provider, PROVIDER_TO_ENDPOINT.get(provider.split(":", 1)[0]))


def decide_job(job: dict[str, Any], health: dict[str, dict[str, Any]]) -> dict[str, Any]:
    provider = infer_provider(job)
    endpoint = provider_endpoint(provider)
    status = str(job.get("last_status") or "")
    base = {
        "job_id": job.get("id") or job.get("job_id"),
        "name": job.get("name"),
        "script": job.get("script"),
        "provider": job.get("provider"),
        "model": job.get("model"),
        "inferred_provider": provider,
        "endpoint": endpoint,
        "last_status": status,
    }
    if provider == "multi_provider":
        degraded = [k for k, v in health.items() if v.get("classification") != "reachable"]
        decision = "run_with_degraded_provider_exclusions" if degraded else "run"
        reason = "multi-provider job can proceed only if runtime excludes degraded providers" if degraded else "all probed endpoints reachable"
        return {**base, "decision": decision, "reason": reason, "degraded_endpoints": degraded}
    if not endpoint:
        return {**base, "decision": "needs_provider_resolution", "reason": "no provider/model endpoint could be inferred"}
    endpoint_status = health.get(endpoint)
    if not endpoint_status:
        return {**base, "decision": "needs_probe", "reason": f"endpoint {endpoint} missing from probe"}
    classification = endpoint_status.get("classification")
    if classification != "reachable":
        return {**base, "decision": "skip_provider_health_degraded", "reason": f"endpoint {endpoint} classified {classification}", "endpoint_classification": classification}
    if status == "error":
        return {**base, "decision": "retry_allowed_by_provider_health", "reason": f"endpoint {endpoint} reachable; failure likely not current edge reachability", "endpoint_classification": classification}
    return {**base, "decision": "run", "reason": f"endpoint {endpoint} reachable", "endpoint_classification": classification}


def build_report(jobs_path: Path, probe_path: Path, include_all: bool = False) -> dict[str, Any]:
    jobs = load_jobs(jobs_path)
    probe = json.loads(probe_path.read_text())
    health = endpoint_health_map(probe)
    candidate_jobs = jobs if include_all else [j for j in jobs if j.get("last_status") == "error" or j.get("provider") or j.get("model")]
    decisions = [decide_job(j, health) for j in candidate_jobs]
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    return {
        "report_id": "cron_provider_health_gate_20260613_phase24",
        "created_at": utc_now(),
        "status": "READY",
        "scope": "read-only provider health gate decisions for cron/provider jobs",
        "jobs_path": str(jobs_path),
        "probe_path": str(probe_path),
        "probe_status": probe.get("status"),
        "probe_classification_counts": probe.get("classification_counts"),
        "decision_counts": counts,
        "decision_count": len(decisions),
        "decisions": decisions,
        "safe_boundaries": [
            "no cron job mutation",
            "no failed job rerun",
            "no model generation call",
            "no credential read/write",
            "no external message send",
        ],
        "next_actions": [
            "wire cron provider jobs to read this gate before generation",
            "for skip_provider_health_degraded, emit typed local skip instead of RuntimeError red",
            "for multi-provider jobs, exclude degraded providers or fall back before generation",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "cron_provider_health_gate_20260613_phase24.json"
    md_path = out_dir / "cron_provider_health_gate_20260613_phase24.md"
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    json_path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    lines = [
        "# Hermes Cron Provider Health Gate — Phase 24",
        "",
        f"Status: **{report['status']}**",
        f"Probe status: **{report['probe_status']}**",
        f"Decision counts: `{json.dumps(report['decision_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Decisions",
    ]
    for d in report["decisions"]:
        lines.append(f"- **{d['job_id']} {d['name']}** — `{d['decision']}`; {d['reason']}")
    lines += ["", "## Safe Boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next Actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS))
    ap.add_argument("--probe", default="")
    ap.add_argument("--probe-glob", default=DEFAULT_PROBE_GLOB)
    ap.add_argument("--include-all", action="store_true")
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    probe_path = Path(args.probe) if args.probe else latest_probe_path(args.probe_glob)
    report = build_report(Path(args.jobs), probe_path, include_all=args.include_all)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: {report['decision_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
