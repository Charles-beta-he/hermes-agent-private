"""JSON health report for configured or catalog MCP servers.

The doctor path is intentionally read-only: it inspects manifest/config state,
optionally performs the same list_tools probe used by ``hermes mcp test``, and
never prints raw env/header values.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from hermes_constants import display_hermes_home
from hermes_cli.config import get_env_value, load_config
from hermes_cli.mcp_catalog import CatalogEntry, get_entry
from hermes_cli.mcp_config import _probe_single_server

_SCHEMA_VERSION = 1
_TRUST_NOTE = (
    "Nous-approved catalog entry shipped with this Hermes install; review the "
    "manifest source and required auth before enabling third-party code."
)
_SECRET_MARKERS = ("key", "token", "secret", "password", "credential", "auth", "bearer")


def _enabled_value(raw: Any) -> bool:
    if isinstance(raw, str):
        return raw.lower() in {"true", "1", "yes", "on"}
    return bool(raw)


def _redact_string(value: str) -> str:
    lower = value.lower()
    if "${" in value:
        return value
    if any(marker in lower for marker in _SECRET_MARKERS):
        return "***REDACTED***"
    return value


def _safe_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return [_redact_string(str(v)) for v in values]


def _catalog_block(entry: Optional[CatalogEntry]) -> Dict[str, Any]:
    if entry is None:
        return {
            "present": False,
            "manifest_path": None,
            "manifest_version": None,
            "source": None,
            "description": None,
            "trust_note": None,
        }
    manifest_path = str(entry.manifest_path) if entry.manifest_path else None
    return {
        "present": True,
        "manifest_path": manifest_path,
        "manifest_version": 1,
        "source": entry.source or None,
        "description": entry.description or None,
        "trust_note": _TRUST_NOTE,
    }


def _configured_servers() -> Dict[str, dict]:
    cfg = load_config()
    servers = cfg.get("mcp_servers") or {}
    return servers if isinstance(servers, dict) else {}


def _transport_from_config_or_catalog(cfg: Optional[dict], entry: Optional[CatalogEntry]) -> Dict[str, Any]:
    if cfg:
        if cfg.get("url"):
            return {
                "type": "http",
                "command": None,
                "args": [],
                "url": _redact_string(str(cfg.get("url") or "")),
                "version": None,
            }
        if cfg.get("command"):
            return {
                "type": "stdio",
                "command": _redact_string(str(cfg.get("command") or "")),
                "args": _safe_list(cfg.get("args") or []),
                "url": None,
                "version": None,
            }
    if entry:
        transport = entry.transport
        return {
            "type": transport.type,
            "command": _redact_string(transport.command or "") if transport.command else None,
            "args": _safe_list(transport.args),
            "url": _redact_string(transport.url or "") if transport.url else None,
            "version": transport.version,
        }
    return {"type": "unknown", "command": None, "args": [], "url": None, "version": None}


def _auth_from_config_or_catalog(name: str, cfg: Optional[dict], entry: Optional[CatalogEntry]) -> Dict[str, Any]:
    if entry:
        requirements = []
        for spec in entry.auth.env:
            requirements.append(
                {
                    "name": spec.name,
                    "required": bool(spec.required),
                    "secret": bool(spec.secret),
                    "status": "present" if get_env_value(spec.name) else "missing",
                }
            )
        status = "not_required"
        if requirements:
            missing_required = [r for r in requirements if r["required"] and r["status"] != "present"]
            status = "missing_required" if missing_required else "ready"
        elif entry.auth.type == "oauth":
            status = "oauth_required"
        return {
            "type": entry.auth.type,
            "provider": entry.auth.provider,
            "requirements": requirements,
            "status": status,
            "headers_configured": bool(cfg and cfg.get("headers")),
            "oauth_configured": bool(cfg and cfg.get("auth") == "oauth"),
        }

    headers = cfg.get("headers") if isinstance(cfg, dict) else None
    auth_type = "oauth" if cfg and cfg.get("auth") == "oauth" else ("header" if headers else "none")
    status = "configured" if auth_type != "none" else "not_required"
    return {
        "type": auth_type,
        "provider": None,
        "requirements": [],
        "status": status,
        "headers_configured": bool(headers),
        "oauth_configured": bool(cfg and cfg.get("auth") == "oauth"),
    }


def _tools_filter(cfg: Optional[dict], entry: Optional[CatalogEntry]) -> Dict[str, Any]:
    include: List[str] = []
    exclude: List[str] = []
    mode = "all"
    if cfg:
        tools_cfg = cfg.get("tools") or {}
        if isinstance(tools_cfg, dict):
            include_raw = tools_cfg.get("include")
            exclude_raw = tools_cfg.get("exclude")
            if isinstance(include_raw, list):
                include = [str(x) for x in include_raw]
                mode = "include"
            elif isinstance(exclude_raw, list):
                exclude = [str(x) for x in exclude_raw]
                mode = "exclude"
    elif entry and entry.tools.default_enabled is not None:
        include = list(entry.tools.default_enabled)
        mode = "catalog_default_include"
    enabled_count: Optional[int]
    if mode in {"include", "catalog_default_include"}:
        enabled_count = len(include)
    else:
        enabled_count = None
    return {"mode": mode, "include": include, "exclude": exclude, "enabled_count": enabled_count}


def _probe_block(
    name: str,
    cfg: Optional[dict],
    enabled: bool,
    *,
    probe: bool,
    connect_timeout: float,
    catalog_present: bool = False,
) -> Dict[str, Any]:
    if not cfg:
        if catalog_present:
            return {
                "attempted": False,
                "ok": False,
                "tool_count": 0,
                "tools": [],
                "last_error": f"not_installed: catalog MCP '{name}' is not installed in config.yaml",
                "action": f"Install it with `hermes mcp install {name}` or open `hermes mcp picker`.",
            }
        return {
            "attempted": False,
            "ok": False,
            "tool_count": 0,
            "tools": [],
            "last_error": f"not_configured: MCP server '{name}' is not present in config.yaml or catalog",
            "action": f"Install a catalog MCP with `hermes mcp install {name}`, or add a custom server with `hermes mcp add {name} ...`.",
        }
    if not enabled:
        return {
            "attempted": False,
            "ok": False,
            "tool_count": 0,
            "tools": [],
            "last_error": f"disabled: MCP server '{name}' is configured but disabled",
            "action": "Enable it in config.yaml or reinstall/toggle it from `hermes mcp picker`.",
        }
    if not probe:
        return {
            "attempted": False,
            "ok": False,
            "tool_count": 0,
            "tools": [],
            "last_error": None,
            "action": "Probe skipped by request.",
        }

    started = time.monotonic()
    try:
        tools = _probe_single_server(name, cfg, connect_timeout=connect_timeout)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        err = str(exc) or exc.__class__.__name__
        return {
            "attempted": True,
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "tool_count": 0,
            "tools": [],
            "last_error": err,
            "action": f"Run `hermes mcp test {name}` after fixing transport/auth; check command availability, URL reachability, and required env vars.",
        }

    tool_rows = [{"name": str(tool_name), "description": str(desc or "")} for tool_name, desc in tools]
    return {
        "attempted": True,
        "ok": True,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "tool_count": len(tool_rows),
        "tools": tool_rows,
        "last_error": None,
        "action": None,
    }


def build_mcp_doctor_report(name: str, *, probe: bool = True, connect_timeout: float = 30) -> Dict[str, Any]:
    """Build a stable, secret-safe JSON-serializable MCP health report."""
    servers = _configured_servers()
    cfg = servers.get(name)
    if name.startswith("official/"):
        name = name[len("official/"):]
        cfg = servers.get(name)
    entry = get_entry(name)
    config_present = isinstance(cfg, dict)
    enabled = _enabled_value(cfg.get("enabled", True)) if config_present else False
    report = {
        "schema_version": _SCHEMA_VERSION,
        "name": name,
        "profile": {"home": display_hermes_home()},
        "catalog": _catalog_block(entry),
        "config": {"present": config_present, "enabled": enabled},
        "transport": _transport_from_config_or_catalog(cfg, entry),
        "auth": _auth_from_config_or_catalog(name, cfg, entry),
        "tools_filter": _tools_filter(cfg, entry),
        "probe": _probe_block(
            name,
            cfg,
            enabled,
            probe=probe,
            connect_timeout=connect_timeout,
            catalog_present=entry is not None,
        ),
    }
    return report


def print_mcp_doctor_json(name: str, *, probe: bool = True, connect_timeout: float = 30) -> None:
    """Print the doctor report as deterministic JSON."""
    print(json.dumps(build_mcp_doctor_report(name, probe=probe, connect_timeout=connect_timeout), indent=2, sort_keys=True))
