"""
Session sidecar consumer — reads, aggregates, and bridges sidecars.

Provides:
  - sidecar_bridge CLI — shell‐invocable tool to query/aggregate/verify
  - taskpool_proof_ingest — convert sidecar evidence into taskpool‐friendly
    structured evidence entries
  - daily_report_section — produce a summary section for the daily report
  - selfcheck_probe — verify sidecar layer health for ``selfcheck``
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from session_sidecar import (
    SessionSidecar,
    ArtifactRef,
    aggregate_sidecars,
    query_sidecars,
    read_sidecar,
    write_sidecar,
    strip_sensitive,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Taskpool evidence ingestion
# ---------------------------------------------------------------------------

SIDECAR_PROOF_PREFIX = "proof:session-sidecar:"


def sidecar_evidence_for_selfcheck(
    *,
    date_prefix: str = "",
    min_evidence: int = 0,
) -> List[Dict[str, Any]]:
    """Produce evidence entries suitable for ``brain_task_orchestrator.py``.

    Each entry uses the ``proof:session-sidecar:`` prefix recognised by the
    proof/evidence subsystem.

    Example output::

        {
            "type": "proof:session-sidecar",
            "evidence": [
                "proof:session-sidecar:2026-06-11:cli:3",
                "proof:session-sidecar:2026-06-11:cron:1",
                "proof:session-sidecar:2026-06-11:automation:1",
            ]
        }
    """
    agg = aggregate_sidecars(date_prefix=date_prefix)
    entries: List[Dict[str, Any]] = []
    prefix = SIDECAR_PROOF_PREFIX
    if date_prefix:
        prefix += f"{date_prefix}:"

    for ep, count in agg.get("by_entrypoint", {}).items():
        if min_evidence and int(count) < int(min_evidence):
            continue
        entries.append({
            "type": "proof:session-sidecar",
            "evidence": [f"proof:session-sidecar:{ep}:{count}"],
            "value": count,
        })

    entries.append({
        "type": "proof:session-sidecar",
        "evidence": [f"proof:session-sidecar:total:{agg.get('total_runs', 0)}"],
        "value": agg.get("total_runs", 0),
    })
    return entries


# ---------------------------------------------------------------------------
# Daily report section
# ---------------------------------------------------------------------------
def daily_report_section(*, date_prefix: str = "") -> str:
    """Produce a text section for the daily report.

    Returns an empty string when no sidecars exist for the date.
    """
    agg = aggregate_sidecars(date_prefix=date_prefix)
    if agg["total_runs"] == 0:
        return ""

    lines: List[str] = [
        f"## Session Sidecar Summary — {agg['date']}",
        "",
        f"- Total runs: {agg['total_runs']}",
        f"- Total duration: {agg['total_duration_s']:.1f}s",
        f"- Artifacts produced: {agg['artifacts_count']}",
        f"- Evidence refs recorded: {agg['evidence_refs_count']}",
        "",
        "### By entrypoint:",
    ]
    for ep, count in sorted(agg["by_entrypoint"].items(), key=lambda x: -x[1]):
        lines.append(f"  - {ep}: {count}")
    lines.append("")
    lines.append("### By status:")
    for st, count in sorted(agg["by_status"].items(), key=lambda x: -x[1]):
        lines.append(f"  - {st}: {count}")
    if agg["total_degraded_flags"]:
        lines.append(f"\n### Degraded flags total: {agg['total_degraded_flags']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Selfcheck probe
# ---------------------------------------------------------------------------
def selfcheck_probe() -> Dict[str, Any]:
    """Health check for the sidecar layer.

    Returns a dict with ``status`` (``OK`` | ``WARN``) and details.
    Used by ``brain_task_orchestrator selfcheck`` / ``brainctl``.
    """
    sidecar_dir = Path(os.environ.get(
        "HERMES_HOME",
        str(Path.home() / ".hermes"),
    )) / "sidecars"

    if not sidecar_dir.exists():
        return {
            "status": "WARN",
            "reason": "sidecar directory does not exist (no runs yet)",
            "sidecar_dir": str(sidecar_dir),
        }

    day_dirs = sorted(sidecar_dir.iterdir(), reverse=True)
    if not day_dirs:
        return {
            "status": "WARN",
            "reason": "sidecar directory exists but empty",
            "sidecar_dir": str(sidecar_dir),
        }

    # Check today's aggregate
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_agg = aggregate_sidecars(date_prefix=today)
    total = today_agg["total_runs"]

    # Verify at least one daily.ndjson is valid
    parse_ok = 0
    parse_err = 0
    for dd in day_dirs[:3]:  # check 3 most recent days
        daily = dd / "daily.ndjson"
        if daily.exists():
            for line in daily.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        json.loads(line)
                        parse_ok += 1
                    except json.JSONDecodeError:
                        parse_err += 1

    warnings: List[str] = []
    if parse_err:
        warnings.append(f"{parse_err} malformed NDJSON lines found")
    if total == 0:
        warnings.append("no sidecars recorded today")

    sidecar_sha = None
    if day_dirs:
        first_run_files = list(day_dirs[0].glob("*.json"))
        if first_run_files:
            pass  # sha computed on demand

    return {
        "status": "WARN" if warnings else "OK",
        "reason": "; ".join(warnings) if warnings else "healthy",
        "sidecar_dir": str(sidecar_dir),
        "days_present": len(day_dirs),
        "today_runs": total,
        "parse_ok": parse_ok,
        "parse_err": parse_err,
    }


# ---------------------------------------------------------------------------
# Bridge CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``python -m session_sidecar.consumer ...``"""
    argv = argv or sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    command = argv[0]
    kwargs: Dict[str, Any] = {}
    for arg in argv[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            kwargs[k.lstrip("-")] = v

    if command == "aggregate":
        result = aggregate_sidecars(**kwargs)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if command == "query":
        limit = int(kwargs.pop("limit", "50"))
        try:
            results = query_sidecars(limit=limit, **kwargs)
        except TypeError:
            results = []
        print(json.dumps([
            {"run_id": sc.run_id, "entrypoint": sc.entrypoint,
             "status": sc.status, "duration_s": sc.duration_s,
             "created_at": sc.created_at}
            for sc in results
        ], indent=2, ensure_ascii=False))
        return 0

    if command == "read":
        run_id = kwargs.pop("run_id", "")
        if not run_id:
            print('error: --run_id required for "read"', file=sys.stderr)
            return 1
        sc = read_sidecar(run_id, **kwargs)
        if sc is None:
            print(f"sidecar {run_id} not found", file=sys.stderr)
            return 1
        print(json.dumps({
            "run_id": sc.run_id,
            "entrypoint": sc.entrypoint,
            "status": sc.status,
            "duration_s": sc.duration_s,
            "model": sc.model,
            "provider": sc.provider,
            "evidence_refs": sc.evidence_refs,
            "artifacts": [a.path for a in sc.artifacts],
            "degraded_flags": sc.degraded_flags,
            "error_class": sc.error_class,
        }, indent=2, ensure_ascii=False))
        return 0

    if command in {"selfcheck", "selfcheck_probe", "selfcheck-probe"}:
        result = selfcheck_probe()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "OK" else 1

    if command == "daily-report":
        result = daily_report_section(**kwargs)
        print(result)
        return 0

    if command == "ingest-evidence":
        result = sidecar_evidence_for_selfcheck(**kwargs)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if command == "inject-test-sidecar":
        status = kwargs.pop("status", "succeeded")
        ep = kwargs.pop("entrypoint", "script")
        _inject_test_sidecar(status=status, entrypoint=ep, **kwargs)
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    _print_help()
    return 1


def _print_help() -> None:
    print("""\
Session Sidecar Consumer Bridge

Usage:
    python -m session_sidecar.consumer <command> [key=value ...]

Commands:
    aggregate    [date_prefix=YYYY-MM-DD]
    query        [date_prefix=YYYY-MM-DD] [entrypoint=...] [status=...] [limit=N]
    read         run_id=<id> [date_prefix=YYYY-MM-DD]
    selfcheck
    daily-report [date_prefix=YYYY-MM-DD]
    ingest-evidence [date_prefix=YYYY-MM-DD] [min_evidence=N]
    inject-test-sidecar status=succeeded|degraded|failed entrypoint=...
""")


def _inject_test_sidecar(*, status: str = "succeeded",
                         entrypoint: str = "script",
                         **kwargs: Any) -> str:
    """Produce a test sidecar for verification purposes.

    ``entrypoint`` selects one of the producer names accepted by
    ``produce_and_write``.  Producer signatures differ, so fixture kwargs are
    normalised and then filtered to the target producer signature.  This keeps
    the shell bridge tolerant of taskpool-style metadata such as ``task_id``
    when a specific producer does not accept that field.
    """
    from session_sidecar.producers import (
        browser_producer,
        cli_producer,
        cron_producer,
        gateway_producer,
        produce_and_write,
        script_producer,
    )

    producer = "browser" if entrypoint in {"automation", "browser"} else entrypoint
    producers = {
        "cli": cli_producer,
        "cron": cron_producer,
        "browser": browser_producer,
        "script": script_producer,
        "gateway": gateway_producer,
    }
    common: Dict[str, Any] = {
        "status": status,
        "duration_s": 1.23,
        "evidence_refs": [
            "proof:injected-test",
            "artifact:/tmp/test-output.json",
        ],
    }
    if producer in {"cli", "browser", "gateway"}:
        common.update({"model": "test-model", "provider": "test-provider"})
    if producer == "script":
        common.update({"script_path": "session-sidecar-injected-test", "args": []})
    if producer == "cron":
        common.update({"job_id": "injected-test", "job_name": "injected test", "schedule": "manual"})

    normalised = dict(kwargs)
    if "evidence_refs" in normalised and isinstance(normalised["evidence_refs"], str):
        normalised["evidence_refs"] = [r for r in normalised["evidence_refs"].split(",") if r]
    if "degraded_flags" in normalised and isinstance(normalised["degraded_flags"], str):
        normalised["degraded_flags"] = [f for f in normalised["degraded_flags"].split(",") if f]

    common.update(normalised)
    fn = producers.get(producer)
    if fn is None:
        return produce_and_write(producer_kwargs=common, producer=producer)
    allowed = set(inspect.signature(fn).parameters)
    filtered = {k: v for k, v in common.items() if k in allowed}
    return produce_and_write(producer_kwargs=filtered, producer=producer)


if __name__ == "__main__":
    sys.exit(main())
