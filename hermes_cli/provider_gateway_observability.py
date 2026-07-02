"""Local-only provider/gateway observability doctor projection.

This module is the CLI-facing wrapper for `scripts/provider_gateway_observability.py`.
It intentionally reads only a local JSONL evidence file and never probes networks,
reads credentials, mutates providers/accounts, or emits external telemetry.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from hermes_cli.config import get_hermes_home, get_project_root

SCHEMA_VERSION = "hermes.provider_gateway_observability.doctor.v1"


def _load_projection_module():
    script_path = get_project_root() / "scripts" / "provider_gateway_observability.py"
    if not script_path.exists():
        raise FileNotFoundError(f"provider gateway observability script not found: {script_path}")
    spec = importlib.util.spec_from_file_location("provider_gateway_observability_script", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load provider gateway observability script: {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _default_event_log() -> Path:
    return get_hermes_home() / "state" / "provider_gateway_observability" / "events.jsonl"


def build_provider_gateway_observability_report(event_log: str | Path | None = None) -> dict[str, Any]:
    """Build a side-effect-free doctor projection from local observability JSONL."""
    path = Path(event_log).expanduser() if event_log else _default_event_log()
    try:
        mod = _load_projection_module()
        projection = mod.build_doctor_projection(path)
    except Exception as exc:
        projection = {
            "schema_version": "hermes.provider_gateway_observability.v1",
            "created_at": None,
            "status": "ERROR",
            "event_log": str(path),
            "summary": {"event_count": 0},
            "safe_boundaries": [
                "local JSONL only",
                "no network calls",
                "no credential reads or writes",
                "no provider/account mutation",
                "no external telemetry",
            ],
            "next_actions": ["fix local provider_gateway_observability projection import before using this doctor surface"],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "Hermes provider/gateway observability; side-effect-free local JSONL projection only",
        "source": "CPA Manager Plus reference absorption: local-first traffic/cost/failure/quota projection",
        "projection": projection,
        "policy": {
            "external_telemetry": "forbidden_by_default",
            "credential_access": "forbidden",
            "provider_mutation": "forbidden_without_exact_authority_gate",
            "manual_override_precedence": "required_for_future_automation",
        },
    }


def print_provider_gateway_observability_report(*, as_json: bool = False, event_log: str | Path | None = None) -> None:
    report = build_provider_gateway_observability_report(event_log=event_log)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    projection = report["projection"]
    print(f"Provider/Gateway Observability: {projection.get('status')}")
    print(f"  event_log: {projection.get('event_log')}")
    summary = projection.get("summary") or {}
    print(f"  events: {summary.get('event_count', 0)}")
    print("  boundaries: " + ", ".join(projection.get("safe_boundaries") or []))
