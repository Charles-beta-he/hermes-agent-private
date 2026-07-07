"""Read-only startup handshake projection for Hermes CLI.

This module is intentionally control-plane only: it builds a local startup
receipt from cheap/read-only probes, optionally persists that receipt into the
selfcheck directory, and never calls an LLM or performs external dispatch.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from agent.health_records import read_health_summary, record_health_observation
from hermes_cli.config import get_hermes_home, load_config
from hermes_cli.auth import AuthError, resolve_provider
from hermes_cli.models import provider_label
from hermes_cli.runtime_provider import resolve_requested_provider

SCHEMA = "hermes_startup_handshake_v1"


def _now() -> float:
    return time.time()


def _utc_iso(ts: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()


def _safe_count_dir(path: Path) -> int:
    try:
        if not path.exists() or not path.is_dir():
            return 0
        return sum(1 for item in path.iterdir() if item.is_dir() and not item.name.startswith("."))
    except OSError:
        return 0


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _probe_status(statuses: list[str]) -> str:
    if any(s in {"blocked", "degraded"} for s in statuses):
        return "degraded"
    if any(s == "warn" for s in statuses):
        return "warn"
    # Deferred probes are optional/cached checks that were skipped or absent;
    # they should not make a clean startup look unhealthy.
    return "ok"


def _probe(
    probe_id: str,
    status: str,
    message: str,
    *,
    suggested_command: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": probe_id,
        "status": status,
        "message": message,
        "action_taken": False,
    }
    if suggested_command:
        payload["suggested_command"] = suggested_command
    return payload


def _model_from_config(config: Mapping[str, Any]) -> str:
    raw = config.get("model")
    if isinstance(raw, Mapping):
        value = raw.get("default") or raw.get("name") or ""
    elif isinstance(raw, str):
        value = raw
    else:
        value = ""
    return str(value or "(not set)").strip() or "(not set)"


def _provider_summary() -> tuple[str, str, dict[str, Any]]:
    try:
        config = load_config()
    except Exception as exc:
        config = {}
        model = "(not set)"
        return model, "(unknown)", _probe(
            "provider_model",
            "warn",
            f"config load failed: {str(exc)[:120]}",
            suggested_command="hermes doctor --quick",
        )

    model = _model_from_config(config)
    requested = resolve_requested_provider()
    try:
        provider = resolve_provider(requested)
    except AuthError:
        provider = requested or "auto"
    except Exception:
        provider = requested or "auto"
    label = provider_label(provider)
    status = "warn" if model == "(not set)" else "ok"
    suggested = "hermes model" if status == "warn" else None
    return model, label, _probe(
        "provider_model",
        status,
        f"{model} via {label}",
        suggested_command=suggested,
    )


def _dynamic_selfcheck_probe() -> dict[str, Any]:
    try:
        summary = read_health_summary(limit=20)
    except Exception as exc:
        return _probe(
            "dynamic_selfcheck",
            "warn",
            f"health summary unavailable: {str(exc)[:120]}",
            suggested_command="hermes selfcheck health --json",
        )
    status = str(summary.get("status") or "ok")
    degraded = int(summary.get("degraded_count") or 0)
    records = int(summary.get("record_count") or 0)
    if status == "degraded" or degraded:
        msg = f"{degraded} degraded health record(s) out of {records}"
        return _probe("dynamic_selfcheck", "warn", msg, suggested_command="hermes selfcheck health --json")
    return _probe("dynamic_selfcheck", "ok", f"{records} health record(s), no degradation")


def _cron_probe(home: Path) -> dict[str, Any]:
    raw = _read_json_file(home / "cron" / "jobs.json")
    if isinstance(raw, Mapping):
        jobs = raw.get("jobs")
    else:
        jobs = raw
    if not isinstance(jobs, list):
        return _probe("cron_summary", "deferred", "cron jobs file not present or unreadable")
    active = sum(1 for job in jobs if isinstance(job, Mapping) and bool(job.get("enabled", True)))
    return _probe("cron_summary", "ok", f"{active} active / {len(jobs)} total job(s)")


def _gateway_probe(home: Path) -> dict[str, Any]:
    candidates = [
        home / "gateway_state.json",
        home / "gateway" / "gateway_status.json",
        home / "gateway" / "runtime_status.json",
        home / "gateway_status.json",
    ]
    for candidate in candidates:
        data = _read_json_file(candidate)
        if isinstance(data, Mapping):
            state = data.get("gateway_state") or data.get("status") or "present"
            return _probe("gateway_status", "ok", f"runtime status: {state}")
    return _probe("gateway_status", "deferred", "no cached gateway runtime status")


def collect_startup_handshake(
    *,
    write_receipt: bool = False,
    observed_at: float | None = None,
) -> dict[str, Any]:
    """Collect a cheap startup readiness receipt.

    The default path is read-only.  ``write_receipt=True`` persists a local
    selfcheck projection and records one local health observation; it still does
    not call models, send messages, or perform external actions.
    """
    ts = _now() if observed_at is None else float(observed_at)
    home = get_hermes_home()
    model, provider, provider_probe = _provider_summary()
    probes = [
        provider_probe,
        _probe("profile", "ok", os.environ.get("HERMES_PROFILE", "default") or "default"),
        _probe("skills_index", "ok", f"{_safe_count_dir(home / 'skills')} local skill group(s)"),
        _probe("plugins_static", "ok", f"{_safe_count_dir(home / 'plugins')} local plugin(s)"),
        _gateway_probe(home),
        _cron_probe(home),
        _dynamic_selfcheck_probe(),
    ]
    status = _probe_status([str(p.get("status") or "ok") for p in probes])
    writes = ["selfcheck_receipt", "health_record"] if write_receipt else []
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _utc_iso(ts),
        "status": status,
        "summary": {
            "profile": os.environ.get("HERMES_PROFILE", "default") or "default",
            "model": model,
            "provider": provider,
        },
        "probes": probes,
        "side_effects": {"external": False, "llm": False, "writes": writes},
    }
    if write_receipt:
        root = home / "state" / "selfcheck"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "startup_handshake_last.json"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        record_health_observation(
            chain_type="startup_handshake",
            target_id="cli",
            target_label="Hermes CLI startup handshake",
            status="success" if status == "ok" else "warning",
            success=status == "ok",
            details={"receipt_path": str(path), "probe_count": len(probes)},
            observed_at=ts,
        )
    return receipt


def render_startup_handshake(args: Any) -> int:
    write_receipt = not bool(getattr(args, "no_write", False))
    receipt = collect_startup_handshake(write_receipt=write_receipt)
    if bool(getattr(args, "json", False)):
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    probes = receipt.get("probes") or []
    warn_count = sum(1 for p in probes if str(p.get("status")) in {"warn", "degraded", "blocked"})
    print(f"Hermes startup handshake: {receipt.get('status')} ({len(probes)} probes, {warn_count} warning(s))")
    summary = receipt.get("summary") or {}
    print(f"  profile: {summary.get('profile')}  model: {summary.get('model')}  provider: {summary.get('provider')}")
    for probe in probes:
        status = probe.get("status")
        if status == "ok":
            continue
        print(f"  - {probe.get('id')}: {status} — {probe.get('message')}")
        if probe.get("suggested_command"):
            print(f"    next: {probe.get('suggested_command')}")
    if write_receipt:
        print(f"  receipt: {get_hermes_home() / 'state' / 'selfcheck' / 'startup_handshake_last.json'}")
    return 0


__all__ = ["SCHEMA", "collect_startup_handshake", "render_startup_handshake"]
