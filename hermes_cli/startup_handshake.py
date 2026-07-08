"""Read-only startup handshake projection for Hermes CLI.

This module is intentionally control-plane only: it builds a local startup
receipt from cheap/read-only probes, optionally persists that receipt into the
selfcheck directory, and never calls an LLM or performs external dispatch.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from agent.health_records import read_health_summary, record_health_observation
from hermes_cli.config import get_hermes_home, load_config
from hermes_cli.auth import AuthError, resolve_provider
from hermes_cli.models import provider_label
from hermes_cli.runtime_provider import resolve_requested_provider

SCHEMA = "hermes_startup_handshake_v1"
REMINDER_SCHEMA = "hermes_startup_handshake_reminders_v1"

DEFAULT_REMINDER_INTERVAL_SECONDS = {
    "blocked": 0,
    "degraded": 4 * 60 * 60,
    "warn": 24 * 60 * 60,
    "info": 7 * 24 * 60 * 60,
    "deferred": 7 * 24 * 60 * 60,
}


class StartupHandshakeError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _utc_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
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


def _write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def receipt_path(home: Path | None = None) -> Path:
    return (home or get_hermes_home()) / "state" / "selfcheck" / "startup_handshake_last.json"


def reminder_state_path(home: Path | None = None) -> Path:
    return (home or get_hermes_home()) / "state" / "selfcheck" / "startup_handshake_reminders.json"


def _severity_for_status(status: str) -> str:
    if status in {"blocked", "degraded"}:
        return status
    if status == "warn":
        return "warn"
    if status == "deferred":
        return "deferred"
    return "info"


def _probe_status(statuses: list[str]) -> str:
    if any(s == "blocked" for s in statuses):
        return "blocked"
    if any(s == "degraded" for s in statuses):
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
    severity: str | None = None,
    suggested_command: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": probe_id,
        "status": status,
        "severity": severity or _severity_for_status(status),
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


def _update_target_probe() -> dict[str, Any]:
    """Report the local update target without performing network checks."""
    try:
        from hermes_cli.update_target import load_install_metadata, resolve_update_target

        repo_dir = Path(__file__).parent.parent.resolve()
        metadata = load_install_metadata()
        target = resolve_update_target(repo_dir=repo_dir if (repo_dir / ".git").exists() else None)
    except Exception as exc:
        return _probe(
            "update_target",
            "deferred",
            f"update target unavailable: {str(exc)[:120]}",
            suggested_command="hermes update --check",
        )

    msg = f"{target.channel} via {target.compare_ref} ({target.source})"
    if target.channel == "charles-private" and target.source == "git-origin" and not metadata:
        return _probe(
            "update_target",
            "warn",
            f"{msg}; install metadata missing",
            suggested_command="scripts/install-private.sh --yes --skip-setup",
        )
    return _probe("update_target", "ok", msg, suggested_command="hermes update --check")


def _reminder_key(profile: str, entrypoint: str, probe: Mapping[str, Any]) -> str:
    return f"{profile}:{entrypoint}:{probe.get('id')}:{probe.get('severity') or _severity_for_status(str(probe.get('status') or 'info'))}"


def _load_reminder_state(home: Path | None = None) -> dict[str, Any]:
    data = _read_json_file(reminder_state_path(home))
    if isinstance(data, Mapping) and isinstance(data.get("items"), Mapping):
        return {"schema": data.get("schema") or REMINDER_SCHEMA, "items": dict(data.get("items") or {})}
    return {"schema": REMINDER_SCHEMA, "items": {}}


def _save_reminder_state(state: Mapping[str, Any], home: Path | None = None) -> None:
    _write_json_file(reminder_state_path(home), {"schema": REMINDER_SCHEMA, "items": dict(state.get("items") or {})})


def apply_reminder_policy(
    receipt: dict[str, Any],
    *,
    entrypoint: str = "manual",
    update_state: bool = False,
    observed_at: float | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Annotate receipt probes with severity-based reminder windows.

    Reminder state is keyed by profile + entrypoint + probe id + severity so a
    noisy warning in chat does not suppress a targeted status/selfcheck view.
    """
    ts = _now() if observed_at is None else float(observed_at)
    state = _load_reminder_state(home)
    items = dict(state.get("items") or {})
    profile = str((receipt.get("summary") or {}).get("profile") or "default")
    changed = False
    for probe in receipt.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        status = str(probe.get("status") or "ok")
        severity = str(probe.get("severity") or _severity_for_status(status))
        probe["severity"] = severity
        if status == "ok":
            probe["reminder"] = {
                "should_remind": False,
                "interval_seconds": None,
                "last_reminded_at": None,
                "next_reminder_at": None,
            }
            continue
        interval = int(DEFAULT_REMINDER_INTERVAL_SECONDS.get(severity, DEFAULT_REMINDER_INTERVAL_SECONDS["warn"]))
        key = _reminder_key(profile, entrypoint, probe)
        previous = items.get(key) if isinstance(items.get(key), Mapping) else {}
        last_ts = previous.get("last_reminded_at_epoch") if isinstance(previous, Mapping) else None
        try:
            last_float = float(last_ts) if last_ts is not None else None
        except Exception:
            last_float = None
        should = last_float is None or interval <= 0 or (ts - last_float) >= interval
        next_ts = ts if should else (last_float + interval if last_float is not None else ts)
        probe["reminder"] = {
            "should_remind": bool(should),
            "interval_seconds": interval,
            "last_reminded_at": _utc_iso(last_float),
            "next_reminder_at": _utc_iso(next_ts),
        }
        if update_state and should:
            items[key] = {
                "last_reminded_at_epoch": ts,
                "last_reminded_at": _utc_iso(ts),
                "entrypoint": entrypoint,
                "probe_id": probe.get("id"),
                "severity": severity,
            }
            changed = True
    receipt["entrypoint"] = entrypoint
    receipt["reminder_policy"] = {
        "schema": REMINDER_SCHEMA,
        "interval_seconds": DEFAULT_REMINDER_INTERVAL_SECONDS,
        "state_path": str(reminder_state_path(home)),
    }
    if update_state and changed:
        state["items"] = items
        _save_reminder_state(state, home)
    return receipt


def collect_startup_handshake(
    *,
    write_receipt: bool = False,
    observed_at: float | None = None,
    entrypoint: str = "manual",
    update_reminders: bool | None = None,
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
        _update_target_probe(),
    ]
    status = _probe_status([str(p.get("status") or "ok") for p in probes])
    writes = ["selfcheck_receipt", "health_record"] if write_receipt else []
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _utc_iso(ts),
        "entrypoint": entrypoint,
        "status": status,
        "summary": {
            "profile": os.environ.get("HERMES_PROFILE", "default") or "default",
            "model": model,
            "provider": provider,
        },
        "probes": probes,
        "side_effects": {"external": False, "llm": False, "writes": writes},
    }
    receipt = apply_reminder_policy(
        receipt,
        entrypoint=entrypoint,
        update_state=write_receipt if update_reminders is None else bool(update_reminders),
        observed_at=ts,
        home=home,
    )
    if write_receipt:
        _write_json_file(receipt_path(home), receipt)
        record_health_observation(
            chain_type="startup_handshake",
            target_id=entrypoint or "cli",
            target_label=f"Hermes CLI startup handshake ({entrypoint or 'cli'})",
            status="success" if status == "ok" else "warning",
            success=status == "ok",
            details={"receipt_path": str(receipt_path(home)), "probe_count": len(probes), "entrypoint": entrypoint},
            observed_at=ts,
        )
    return receipt


def compact_reminder_lines(receipt: Mapping[str, Any], *, max_items: int = 3) -> list[str]:
    probes = [
        p for p in receipt.get("probes", [])
        if isinstance(p, Mapping)
        and str(p.get("status")) != "ok"
        and bool((p.get("reminder") or {}).get("should_remind"))
    ]
    if not probes:
        return []
    lines = [f"⚕ Hermes startup handshake: {receipt.get('status')} ({len(probes)} due reminder(s))"]
    for probe in probes[:max_items]:
        lines.append(f"  - {probe.get('id')}: {probe.get('severity')} — {probe.get('message')}")
        if probe.get("suggested_command"):
            lines.append(f"    next: {probe.get('suggested_command')}")
    if len(probes) > max_items:
        lines.append(f"  … {len(probes) - max_items} more; run: hermes selfcheck startup")
    return lines


def render_startup_handshake(args: Any) -> int:
    write_receipt = not bool(getattr(args, "no_write", False))
    receipt = collect_startup_handshake(write_receipt=write_receipt, entrypoint="selfcheck.startup")
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
        reminder = probe.get("reminder") or {}
        suffix = ""
        if reminder.get("next_reminder_at"):
            suffix = f"; next reminder: {reminder.get('next_reminder_at')}"
        print(f"  - {probe.get('id')}: {status}/{probe.get('severity')} — {probe.get('message')}{suffix}")
        if probe.get("suggested_command"):
            print(f"    next: {probe.get('suggested_command')}")
    if write_receipt:
        print(f"  receipt: {receipt_path()}")
    return 0


def trigger_startup_handshake_for_entrypoint(
    entrypoint: str,
    *,
    emit_reminder: bool = False,
    write_receipt: bool = True,
    stream: Any | None = None,
) -> dict[str, Any] | None:
    """Central entrypoint hook used by CLI commands.

    This is fail-soft by design: startup health projection must never prevent a
    command from running.  It returns the receipt for tests/diagnostics.
    """
    try:
        receipt = collect_startup_handshake(write_receipt=write_receipt, entrypoint=entrypoint)
        if emit_reminder:
            out = stream or sys.stderr
            for line in compact_reminder_lines(receipt):
                print(line, file=out)
        return receipt
    except Exception:
        return None


__all__ = [
    "SCHEMA",
    "REMINDER_SCHEMA",
    "DEFAULT_REMINDER_INTERVAL_SECONDS",
    "apply_reminder_policy",
    "collect_startup_handshake",
    "compact_reminder_lines",
    "render_startup_handshake",
    "trigger_startup_handshake_for_entrypoint",
]
