#!/usr/bin/env python3
"""Provider/network probe ledger for Hermes cron degradation.

Phase 23 guardrail: this script performs bounded, unauthenticated network
probes only. It does not call LLM generation APIs with prompts, does not read or
write credentials, does not mutate cron jobs, and does not send messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_OUT = Path("docs/private/workflow-kernel-provider-network-probe")
DEFAULT_ENDPOINTS = [
    {"id": "github", "url": "https://github.com/", "purpose": "upstream_fetch_visibility"},
    {"id": "openai", "url": "https://api.openai.com/", "purpose": "default_provider_edge_reachability"},
    {"id": "deepseek", "url": "https://api.deepseek.com/", "purpose": "configured_provider_edge_reachability"},
    {"id": "xiaomi", "url": "https://api.mimogpt.com/", "purpose": "configured_provider_edge_reachability"},
    {"id": "google_gemini", "url": "https://generativelanguage.googleapis.com/", "purpose": "oauth_provider_edge_reachability"},
    {"id": "telegram", "url": "https://api.telegram.org/", "purpose": "telegram_delivery_edge_reachability"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact_proxy(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    if "@" in text:
        scheme, rest = text.split("://", 1) if "://" in text else ("", text)
        host = rest.split("@", 1)[1]
        return f"{scheme + '://' if scheme else ''}[REDACTED]@{host}"
    return text


def proxy_snapshot() -> dict[str, str | None]:
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
    return {k: redact_proxy(os.environ.get(k)) for k in keys if os.environ.get(k)}


def classify_http_status(status: int | None, error: str | None) -> str:
    if status is not None:
        if 200 <= status < 500:
            # 401/403/404 still prove network + TLS + edge routing; auth is not tested here.
            return "reachable"
        if 500 <= status < 600:
            return "remote_5xx"
    if error:
        e = error.lower()
        if "timed out" in e or "timeout" in e:
            return "timeout"
        if "ssl" in e or "certificate" in e:
            return "tls_error"
        if "proxy" in e or "503 service unavailable" in e:
            return "proxy_or_gateway_error"
        if "name or service" in e or "nodename" in e or "dns" in e:
            return "dns_error"
        return "network_error"
    return "unknown"


def probe_url(endpoint: dict[str, str], timeout: float = 5.0) -> dict[str, Any]:
    url = endpoint["url"]
    started = time.time()
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Hermes-Provider-Probe/phase23"})
    status = None
    error = None
    final_url = url
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed endpoints, no user secrets
            status = int(getattr(resp, "status", 0) or resp.getcode())
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        final_url = getattr(exc, "url", url)
    except Exception as exc:  # network probes intentionally capture all boundary errors
        error = f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.time() - started) * 1000)
    return {
        "id": endpoint["id"],
        "purpose": endpoint.get("purpose"),
        "url_host": urllib.parse.urlparse(url).netloc,
        "status_code": status,
        "classification": classify_http_status(status, error),
        "error": error[:500] if error else None,
        "duration_ms": duration_ms,
        "final_host": urllib.parse.urlparse(final_url).netloc if final_url else None,
    }


def overall_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "UNKNOWN"
    bad = [r for r in results if r["classification"] not in {"reachable"}]
    if not bad:
        return "PASS"
    if len(bad) == len(results):
        return "BLOCKED_NETWORK"
    return "DEGRADED_PARTIAL"


def build_report(timeout: float = 5.0, endpoints: list[dict[str, str]] | None = None) -> dict[str, Any]:
    eps = endpoints or DEFAULT_ENDPOINTS
    results = [probe_url(e, timeout=timeout) for e in eps]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    return {
        "report_id": "provider_network_probe_20260613_phase23",
        "created_at": utc_now(),
        "status": overall_status(results),
        "scope": "bounded unauthenticated provider/network probes for cron degradation triage",
        "timeout_seconds": timeout,
        "proxy_env_present": proxy_snapshot(),
        "probe_count": len(results),
        "classification_counts": counts,
        "results": results,
        "safe_boundaries": [
            "no LLM generation call",
            "no credential read/write",
            "no cron job mutation",
            "no external message send",
            "no provider retry loop",
        ],
        "next_actions": [
            "if github is unreachable, use upstream ratchet no-fetch fallback",
            "if provider hosts are unreachable, keep provider_generation jobs quarantined until next probe",
            "if telegram is unreachable, keep failed notifications in governed ledger for manual retry",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "provider_network_probe_20260613_phase23.json"
    md_path = out_dir / "provider_network_probe_20260613_phase23.md"
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    json_path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    lines = [
        "# Hermes Provider / Network Probe Ledger — Phase 23",
        "",
        f"Status: **{report['status']}**",
        f"Probe count: **{report['probe_count']}**",
        f"Class counts: `{json.dumps(report['classification_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Results",
    ]
    for r in report["results"]:
        status = r.get("status_code") if r.get("status_code") is not None else "-"
        lines.append(f"- **{r['id']}** `{r['classification']}` status={status} host={r['url_host']} duration={r['duration_ms']}ms")
    lines += ["", "## Safe Boundaries"] + [f"- {x}" for x in report["safe_boundaries"]]
    lines += ["", "## Next Actions"] + [f"- {x}" for x in report["next_actions"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(timeout=args.timeout)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: {report['classification_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
