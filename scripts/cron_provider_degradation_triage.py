#!/usr/bin/env python3
"""Classify Hermes cron/provider degradation without mutating cron state.

Phase 22 guardrail: this script is read-only unless --write-report is supplied,
and even then it only writes local evidence under docs/private/. It never runs
jobs, changes schedules, sends messages, or edits credentials/config.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_OUT = Path("docs/private/workflow-kernel-cron-provider-triage")

PROVIDER_GENERATION_RE = re.compile(r"(RuntimeError: Connection error|provider_generation|api connection|client closed|quota|rate limit|timeout)", re.I)
NETWORK_RE = re.compile(r"(SSL_connect|github\.com:443|unable to access|ProxyError|503 Service Unavailable|Connection error)", re.I)
TELEGRAM_DELIVERY_RE = re.compile(r"(Telegram send failed|telegram|ProxyError|503 Service Unavailable)", re.I)
SCRIPT_FAILURE_RE = re.compile(r"(Script exited with code|Traceback|SystemExit|failed \(\d+\))", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_jobs(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(jobs, list):
        raise SystemExit(f"invalid jobs file shape: {path}")
    return [j for j in jobs if isinstance(j, dict)]


def classify_job(job: dict[str, Any]) -> dict[str, Any]:
    last_error = str(job.get("last_error") or "")
    delivery_error = str(job.get("last_delivery_error") or "")
    text = f"{last_error}\n{delivery_error}"
    classes: list[str] = []
    if PROVIDER_GENERATION_RE.search(text):
        classes.append("provider_generation")
    if NETWORK_RE.search(text):
        classes.append("network_or_proxy")
    if TELEGRAM_DELIVERY_RE.search(delivery_error):
        classes.append("telegram_delivery")
    if SCRIPT_FAILURE_RE.search(text):
        classes.append("script_failure")
    if not classes:
        classes.append("unknown")

    primary = classes[0]
    if "telegram_delivery" in classes:
        primary = "telegram_delivery"
    elif "script_failure" in classes and "network_or_proxy" in classes:
        primary = "script_network_failure"
    elif "provider_generation" in classes:
        primary = "provider_generation"

    recommendation = {
        "provider_generation": "retry only after provider/network probe; avoid burning model calls in tight loops",
        "network_or_proxy": "collect proxy/GitHub/connectivity evidence; use no-fetch fallback where available",
        "telegram_delivery": "keep message in ledger and retry through governed notification policy after proxy recovers",
        "script_failure": "debug script with full traceback; do not mark provider unhealthy without script boundary evidence",
        "script_network_failure": "add no-fetch/offline fallback and classify as network-bound script failure",
        "unknown": "inspect full cron output before changing job state",
    }.get(primary, "inspect manually")

    return {
        "job_id": job.get("id") or job.get("job_id"),
        "name": job.get("name"),
        "script": job.get("script"),
        "model": job.get("model"),
        "provider": job.get("provider"),
        "deliver": job.get("deliver"),
        "last_run_at": job.get("last_run_at"),
        "last_error_excerpt": last_error[:900],
        "last_delivery_error": delivery_error[:900] if delivery_error else None,
        "classes": classes,
        "primary_class": primary,
        "recommendation": recommendation,
    }


def build_report(jobs_path: Path) -> dict[str, Any]:
    jobs = load_jobs(jobs_path)
    failed = [j for j in jobs if j.get("last_status") == "error"]
    classified = [classify_job(j) for j in failed]
    counts: dict[str, int] = {}
    for item in classified:
        counts[item["primary_class"]] = counts.get(item["primary_class"], 0) + 1
    p1 = [c for c in classified if c["primary_class"] in {"provider_generation", "script_network_failure", "telegram_delivery"}]
    status = "PASS" if not classified else "DEGRADED_NOT_BLOCKED"
    return {
        "report_id": "cron_provider_triage_20260613_phase22",
        "created_at": utc_now(),
        "status": status,
        "scope": "Hermes cron/provider degradation triage; read-only classification",
        "jobs_path": str(jobs_path),
        "total_jobs": len(jobs),
        "failed_count": len(classified),
        "class_counts": counts,
        "p1_count": len(p1),
        "failed_jobs": classified,
        "safe_boundaries": [
            "no cron schedule mutation",
            "no job pause/resume/remove",
            "no model-call retry loop",
            "no external message send",
            "no config migration",
        ],
        "next_actions": [
            "run provider/network probe once and persist result",
            "add no-fetch fallback for upstream ratchet review",
            "route Telegram retry through governed notification policy ledger",
            "quarantine repeated provider_generation jobs only after a second classified failure window",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "cron_provider_triage_20260613_phase22.json"
    md_path = out_dir / "cron_provider_triage_20260613_phase22.md"
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    json_path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    lines = [
        "# Hermes Cron / Provider Degradation Triage — Phase 22",
        "",
        f"Status: **{report['status']}**",
        f"Failed jobs: **{report['failed_count']} / {report['total_jobs']}**",
        f"Class counts: `{json.dumps(report['class_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Failed Jobs",
    ]
    for job in report["failed_jobs"]:
        lines.append(f"- **{job['job_id']} {job['name']}** — `{job['primary_class']}`; {job['recommendation']}")
    lines += ["", "## Safe Boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next Actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(DEFAULT_JOBS))
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(Path(args.jobs))
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: failed={report['failed_count']} classes={report['class_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
