#!/usr/bin/env python3
"""Local provider-health gate for Hermes cron jobs.

Phase 24 guardrail: this script consumes existing local evidence (cron jobs +
provider/network probe ledger) and emits a typed, local-only gate plan. It does
not mutate cron schedules, run jobs, call model generation, send messages, read
credentials, or write config. With --write-report it only writes local evidence
under docs/private/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_PROBE = Path("docs/private/workflow-kernel-provider-network-probe/provider_network_probe_20260613_phase23.json")
DEFAULT_OUT = Path("docs/private/workflow-kernel-provider-cron-health-gate")
REPORT_ID = "provider_cron_health_gate_20260613_phase24"

PROVIDER_ENDPOINT_HINTS = {
    "openai": "openai",
    "openai-codex": "openai",
    "codex": "openai",
    "deepseek": "deepseek",
    "xiaomi": "xiaomi",
    "mimogpt": "xiaomi",
    "gemini": "google_gemini",
    "google": "google_gemini",
    "telegram": "telegram",
}

PROVIDER_FAILURE_CLASSES = {"provider_generation"}
NETWORK_FAILURE_CLASSES = {"script_network_failure", "network_or_proxy"}
DELIVERY_FAILURE_CLASSES = {"telegram_delivery"}
UNHEALTHY_PROBE_CLASSES = {"remote_5xx", "timeout", "tls_error", "proxy_or_gateway_error", "dns_error", "network_error", "unknown"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_jobs(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        raise SystemExit(f"invalid jobs file shape: {path}")
    return [j for j in jobs if isinstance(j, dict)]


def endpoint_map(probe_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(r.get("id")): r for r in probe_report.get("results", []) if isinstance(r, dict) and r.get("id")}


def infer_endpoint(job: dict[str, Any]) -> str | None:
    text = " ".join(str(job.get(k) or "") for k in ("provider", "model", "name", "script", "deliver")).lower()
    for hint, endpoint in PROVIDER_ENDPOINT_HINTS.items():
        if hint in text:
            return endpoint
    return None


def classify_failure(job: dict[str, Any]) -> str:
    last_error = str(job.get("last_error") or "")
    delivery_error = str(job.get("last_delivery_error") or "")
    text = f"{last_error}\n{delivery_error}".lower()
    if "telegram" in delivery_error.lower() or "telegram send failed" in text:
        return "telegram_delivery"
    if "ssl_connect" in text or "github.com:443" in text or "unable to access" in text:
        return "script_network_failure"
    if "runtimeerror: connection error" in text or "provider_generation" in text or "rate limit" in text or "quota" in text:
        return "provider_generation"
    if "script exited with code" in text or "traceback" in text:
        return "script_failure"
    return "unknown"


def job_gate_decision(job: dict[str, Any], probes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    failure_class = classify_failure(job)
    endpoint = infer_endpoint(job)
    probe = probes.get(endpoint) if endpoint else None
    probe_class = probe.get("classification") if probe else None
    degraded = probe_class in UNHEALTHY_PROBE_CLASSES

    decision = "allow_noop_observe"
    reason = "not a failed provider/network/delivery cron job"
    if failure_class in PROVIDER_FAILURE_CLASSES:
        if endpoint and degraded:
            decision = "skip_provider_generation_retry"
            reason = f"target provider endpoint {endpoint} is currently {probe_class}; avoid burning retries"
        elif endpoint and probe_class == "reachable":
            decision = "allow_single_governed_retry_candidate"
            reason = f"target provider endpoint {endpoint} is reachable; generation may still fail on auth/quota/model path"
        else:
            decision = "hold_for_unmapped_provider_probe"
            reason = "provider-generation failure has no mapped endpoint probe; do not retry blindly"
    elif failure_class in NETWORK_FAILURE_CLASSES:
        if probes.get("github", {}).get("classification") != "reachable":
            decision = "use_no_fetch_or_offline_fallback"
            reason = "upstream fetch/network dependency is degraded"
        else:
            decision = "debug_script_without_provider_retry"
            reason = "network script failure is not provider generation; avoid model retry loop"
    elif failure_class in DELIVERY_FAILURE_CLASSES:
        if probes.get("telegram", {}).get("classification") != "reachable":
            decision = "hold_governed_notification_retry"
            reason = "telegram edge is degraded; keep receipt/notification in local ledger"
        else:
            decision = "manual_governed_notification_retry_candidate"
            reason = "telegram edge is reachable, but unattended/external sends remain blocked by mission policy"
    elif failure_class == "script_failure":
        decision = "debug_script_failure"
        reason = "script failure is not enough evidence for provider health mutation"

    return {
        "job_id": job.get("id") or job.get("job_id"),
        "name": job.get("name"),
        "script": job.get("script"),
        "provider": job.get("provider"),
        "model": job.get("model"),
        "deliver": job.get("deliver"),
        "failure_class": failure_class,
        "mapped_endpoint": endpoint,
        "probe_classification": probe_class,
        "gate_decision": decision,
        "typed_reason": reason,
    }


def build_report(jobs_path: Path, probe_path: Path) -> dict[str, Any]:
    jobs = load_jobs(jobs_path)
    probe_report = load_json(probe_path)
    probes = endpoint_map(probe_report)
    failed = [j for j in jobs if j.get("last_status") == "error"]
    decisions = [job_gate_decision(j, probes) for j in failed]
    counts: dict[str, int] = {}
    for item in decisions:
        counts[item["gate_decision"]] = counts.get(item["gate_decision"], 0) + 1
    skips = sum(1 for d in decisions if d["gate_decision"].startswith("skip_") or d["gate_decision"].startswith("hold_"))
    status = "PASS" if not decisions else "DEGRADED_GATED" if skips else "READY_WITH_GUARDS"
    return {
        "report_id": REPORT_ID,
        "created_at": utc_now(),
        "status": status,
        "scope": "local-only provider health gate plan for failed Hermes cron jobs",
        "jobs_path": str(jobs_path),
        "probe_path": str(probe_path),
        "probe_status": probe_report.get("status"),
        "failed_count": len(decisions),
        "gate_counts": counts,
        "job_decisions": decisions,
        "safe_boundaries": [
            "no cron schedule mutation",
            "no job pause/resume/remove",
            "no model generation call",
            "no provider retry loop",
            "no credential read/write",
            "no external message send",
        ],
        "next_actions": [
            "wire this gate into future cron retry planning as a dry-run preflight first",
            "only retry provider_generation jobs when endpoint is reachable and a separate governed retry scope exists",
            "route telegram retries through the governed notification policy and explicit send authorization",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{REPORT_ID}.json"
    md_path = out_dir / f"{REPORT_ID}.md"
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    json_path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    lines = [
        "# Hermes Provider Cron Health Gate — Phase 24",
        "",
        f"Status: **{report['status']}**",
        f"Probe status: **{report.get('probe_status')}**",
        f"Failed jobs gated: **{report['failed_count']}**",
        f"Gate counts: `{json.dumps(report['gate_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Job Decisions",
    ]
    for d in report["job_decisions"]:
        lines.append(f"- **{d['job_id']} {d['name']}** — `{d['gate_decision']}` ({d['failure_class']}, endpoint={d['mapped_endpoint']}, probe={d['probe_classification']}): {d['typed_reason']}")
    lines += ["", "## Safe Boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next Actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS))
    ap.add_argument("--probe", default=str(DEFAULT_PROBE))
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(Path(args.jobs), Path(args.probe))
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: failed={report['failed_count']} gates={report['gate_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
