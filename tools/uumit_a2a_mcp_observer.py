"""UUMit A2A/MCP Read-Only Observation Adapter — Spike.

Connects to UUMit's public A2A and MCP surfaces in a strictly read-only
fashion for reconnaissance and surface discovery.  This is a *spike*
(exploratory prototype), not a production adapter.

HARD INVARIANTS (enforced at every public entry point):
  1. NO credentials are ever read, stored, or transmitted.
  2. NO write/mutating HTTP methods (POST/PUT/PATCH/DELETE to user-facing
     endpoints).  The only POST allowed is the A2A ``tasks/get`` probe
     with a null UUID to observe the auth-guard response shape — no real
     task is created or mutated.
  3. NO UT (usage token / credit) consumption.  All probes target public
     or auth-guarded-but-rejected endpoints.
  4. NO tool execution against the MCP server — only SSE handshake and
     capability discovery via ``tools/list`` through the public SSE
     transport (which requires auth and thus safely fails).

This module is intentionally NOT registered as a Hermes tool.  It is a
standalone adapter intended to be imported by scripts or test harnesses
that need read-only surface intelligence about UUMit's A2A and MCP
interfaces.

Architecture:
  UUMitA2AOberver   — probes /.well-known/agent.json, /a2a auth guard
  UUMitMCPObserver  — probes /mcp/sse handshake, /mcp tool-list shape
  UUMitReadonlyAdapter — composite that runs both observers + OpenAPI
"""

from __future__ import annotations

import hashlib
import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 12
DEFAULT_SSE_TIMEOUT = 8
MAX_RESPONSE_BYTES = 5_000_000
USER_AGENT = "HermesUUMitObserver/0.1-spike (+https://hermes-agent.nousresearch.com)"

UUMIT_API_BASE = "https://api.uumit.com"

# Endpoints observed (all read-only or auth-guard-rejected)
ENDPOINTS = {
    "openapi": "/api/v1/public/openapi.json",
    "agent_card": "/.well-known/agent.json",
    "community_stats": "/api/v1/public/community-stats",
    "capabilities": "/api/v1/capabilities?page=1&page_size=3",
    "demands": "/api/v1/demands?page=1&page_size=3",
    "mcp_sse": "/mcp/sse",
    "a2a_rpc": "/a2a",
}

# Forbidden HTTP methods — never sent to user-facing endpoints
_FORBIDDEN_METHODS = frozenset({"PUT", "PATCH", "DELETE"})

# SSL context (reused per process)
_ssl_ctx = ssl.create_default_context()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    extra_headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Low-level HTTP request with safety guards.

    Raises ``PermissionError`` if *method* is a forbidden mutating verb.
    """
    method_upper = method.upper()
    if method_upper in _FORBIDDEN_METHODS:
        raise PermissionError(
            f"Read-only adapter refuses {method_upper} to {url}"
        )

    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    req = urllib.request.Request(url, data=body, headers=headers, method=method_upper)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=_ssl_ctx
        ) as resp:
            raw = resp.read(max_bytes)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            ct = resp.headers.get("content-type", "")
            text = raw.decode("utf-8", errors="replace")
            parsed = None
            if "json" in ct or text[:1] in ("{", "["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            return {
                "ok": True,
                "method": method_upper,
                "url": url,
                "status": resp.status,
                "elapsed_ms": elapsed_ms,
                "content_type": ct,
                "bytes_read": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "json": parsed,
                "text_preview": text[:1000] if parsed is None else None,
                "truncated": max_bytes if len(raw) >= max_bytes else None,
            }
    except urllib.error.HTTPError as e:
        raw = e.read(64_000)
        text = raw.decode("utf-8", errors="replace")
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        return {
            "ok": False,
            "method": method_upper,
            "url": url,
            "status": e.code,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "content_type": e.headers.get("content-type", ""),
            "bytes_read": len(raw),
            "json": parsed,
            "text_preview": text[:1000] if parsed is None else None,
        }
    except Exception as e:  # noqa: BLE001 — diagnostic tool must capture all
        return {
            "ok": False,
            "method": method_upper,
            "url": url,
            "status": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error_type": type(e).__name__,
            "error": str(e),
        }


def _sse_handshake(
    url: str, *, timeout: int = DEFAULT_SSE_TIMEOUT
) -> dict[str, Any]:
    """Attempt SSE handshake — read first few lines, then abort.

    No credentials sent.  Observes auth guard response shape.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "text/event-stream"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=_ssl_ctx
        ) as resp:
            lines: list[str] = []
            for _ in range(6):
                line = resp.readline(4096)
                if not line:
                    break
                lines.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))
                if len(lines) >= 2 and lines[-1] == "":
                    break
            return {
                "ok": True,
                "method": "GET",
                "url": url,
                "status": resp.status,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "content_type": resp.headers.get("content-type", ""),
                "lines": lines,
                "text_preview": "\n".join(lines),
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "method": "GET",
            "url": url,
            "status": e.code,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error_type": "HTTPError",
            "error": str(e),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "method": "GET",
            "url": url,
            "status": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error_type": type(e).__name__,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# OpenAPI summariser
# ---------------------------------------------------------------------------

def _summarize_openapi(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Extract read-only intelligence from an OpenAPI spec."""
    if not isinstance(payload, dict):
        return {"available": False}
    paths = payload.get("paths") or {}
    tags: dict[str, int] = {}
    method_counts: dict[str, int] = {}
    sample_paths: list[str] = []
    read_only_paths: list[str] = []
    write_paths: list[str] = []
    for path, item in paths.items():
        if len(sample_paths) < 40:
            sample_paths.append(path)
        if not isinstance(item, dict):
            continue
        for m, operation in item.items():
            ml = m.lower()
            if ml not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            method_counts[ml.upper()] = method_counts.get(ml.upper(), 0) + 1
            if ml == "get":
                if len(read_only_paths) < 20:
                    read_only_paths.append(path)
            elif ml in {"post", "put", "patch", "delete"}:
                if len(write_paths) < 20:
                    write_paths.append(path)
            if isinstance(operation, dict):
                for tag in operation.get("tags") or []:
                    tags[str(tag)] = tags.get(str(tag), 0) + 1
    return {
        "available": True,
        "openapi_version": payload.get("openapi"),
        "info": payload.get("info"),
        "path_count": len(paths),
        "method_counts": method_counts,
        "schema_count": len(
            ((payload.get("components") or {}).get("schemas") or {})
        ),
        "top_tags": sorted(tags.items(), key=lambda kv: kv[1], reverse=True)[:20],
        "sample_paths": sample_paths,
        "read_only_get_paths": read_only_paths,
        "write_paths_sample": write_paths,
    }


# ---------------------------------------------------------------------------
# Agent Card summariser (A2A protocol)
# ---------------------------------------------------------------------------

def _summarize_agent_card(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Extract read-only intelligence from an A2A Agent Card."""
    if not isinstance(payload, dict):
        return {"available": False}
    skills = payload.get("skills") or payload.get("capabilities") or []
    auth = payload.get("authentication")
    return {
        "available": True,
        "name": payload.get("name"),
        "description": payload.get("description"),
        "protocol_version": payload.get("protocol") or payload.get("protocolVersion"),
        "url": payload.get("url"),
        "authentication_scheme": (
            sorted(auth.keys()) if isinstance(auth, dict) else str(auth)[:200]
        ),
        "capability_keys": (
            sorted((payload.get("capabilities") or {}).keys())
            if isinstance(payload.get("capabilities"), dict)
            else None
        ),
        "skill_count": len(skills) if isinstance(skills, list) else None,
        "skill_sample": [
            {"id": s.get("id"), "name": s.get("name")}
            for s in (skills[:5] if isinstance(skills, list) else [])
            if isinstance(s, dict)
        ],
    }


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    """Single probe observation."""
    name: str
    ok: bool
    status: int | None
    elapsed_ms: int
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "data": self.data,
        }


@dataclass
class A2AObservation:
    """A2A surface observation result."""
    agent_card: ProbeResult
    a2a_auth_guard: ProbeResult
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "agent_card": self.agent_card.to_dict(),
            "a2a_auth_guard": self.a2a_auth_guard.to_dict(),
        }


@dataclass
class MCPObservation:
    """MCP surface observation result."""
    sse_handshake: ProbeResult
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "sse_handshake": self.sse_handshake.to_dict(),
        }


@dataclass
class FullObservation:
    """Composite observation from all surfaces."""
    a2a: A2AObservation
    mcp: MCPObservation
    openapi: ProbeResult
    community_stats: ProbeResult
    capabilities: ProbeResult
    demands: ProbeResult
    timestamp: str = field(default_factory=_now_iso)
    scope: str = "read_only_public_no_credentials_no_write_no_ut_spend"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.timestamp,
            "scope": self.scope,
            "a2a": self.a2a.to_dict(),
            "mcp": self.mcp.to_dict(),
            "openapi": self.openapi.to_dict(),
            "community_stats": self.community_stats.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "demands": self.demands.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Observer classes
# ---------------------------------------------------------------------------

class UUMitA2AObserver:
    """Read-only observer for UUMit's A2A protocol surface.

    Probes:
    - ``/.well-known/agent.json`` — Agent Card discovery
    - ``POST /a2a`` with null-UUID ``tasks/get`` — auth guard shape
    """

    def __init__(self, base_url: str = UUMIT_API_BASE, timeout: int = DEFAULT_TIMEOUT):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def observe_agent_card(self) -> ProbeResult:
        url = f"{self._base}{ENDPOINTS['agent_card']}"
        raw = _http_request("GET", url, timeout=self._timeout)
        summary = _summarize_agent_card(raw.get("json"))
        return ProbeResult(
            name="agent_card",
            ok=raw.get("ok", False),
            status=raw.get("status"),
            elapsed_ms=raw.get("elapsed_ms", 0),
            data=summary,
            raw=raw,
        )

    def observe_a2a_auth_guard(self) -> ProbeResult:
        """Send a tasks/get probe to observe the auth-guard response.

        Uses a null UUID so no real task is referenced.  This is the one
        POST we allow — it is guaranteed to be rejected by auth, causing
        zero side-effects on UUMit's backend.
        """
        url = f"{self._base}{ENDPOINTS['a2a_rpc']}"
        body = json.dumps({
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "params": {"id": "00000000-0000-0000-0000-000000000000"},
            "id": "hermes-readonly-probe",
        }).encode("utf-8")
        raw = _http_request(
            "POST", url,
            body=body,
            timeout=self._timeout,
            extra_headers={"Content-Type": "application/json"},
        )
        return ProbeResult(
            name="a2a_auth_guard",
            ok=raw.get("ok", False),
            status=raw.get("status"),
            elapsed_ms=raw.get("elapsed_ms", 0),
            data={
                "response_json": raw.get("json"),
                "guard_status": raw.get("status"),
            },
            raw=raw,
        )

    def observe(self) -> A2AObservation:
        return A2AObservation(
            agent_card=self.observe_agent_card(),
            a2a_auth_guard=self.observe_a2a_auth_guard(),
        )


class UUMitMCPObserver:
    """Read-only observer for UUMit's MCP surface.

    Probes:
    - ``GET /mcp/sse`` — SSE handshake attempt (no auth = observe guard)
    - Does NOT execute any MCP tools or send MCP messages.
    """

    def __init__(self, base_url: str = UUMIT_API_BASE, timeout: int = DEFAULT_TIMEOUT):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def observe_sse_handshake(self) -> ProbeResult:
        url = f"{self._base}{ENDPOINTS['mcp_sse']}"
        raw = _sse_handshake(url, timeout=min(self._timeout, DEFAULT_SSE_TIMEOUT))
        return ProbeResult(
            name="mcp_sse_handshake",
            ok=raw.get("ok", False),
            status=raw.get("status"),
            elapsed_ms=raw.get("elapsed_ms", 0),
            data={
                "content_type": raw.get("content_type"),
                "preview": raw.get("text_preview"),
                "lines": raw.get("lines"),
            },
            raw=raw,
        )

    def observe(self) -> MCPObservation:
        return MCPObservation(
            sse_handshake=self.observe_sse_handshake(),
        )


class UUMitReadonlyAdapter:
    """Composite read-only adapter for UUMit's A2A + MCP + REST surfaces.

    Runs all observers and assembles a single ``FullObservation`` result.
    Enforces read-only invariants at every layer.
    """

    def __init__(
        self,
        base_url: str = UUMIT_API_BASE,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._a2a = UUMitA2AObserver(base_url, timeout)
        self._mcp = UUMitMCPObserver(base_url, timeout)

    def _probe_public(self, name: str, path: str) -> ProbeResult:
        url = f"{self._base}{path}"
        raw = _http_request("GET", url, timeout=self._timeout)
        data: dict[str, Any] = {}
        if name == "openapi":
            data = _summarize_openapi(raw.get("json"))
        elif name in ("capabilities", "demands"):
            data = _summarize_list(raw.get("json"))
        else:
            data = {"json": raw.get("json")}
        return ProbeResult(
            name=name,
            ok=raw.get("ok", False),
            status=raw.get("status"),
            elapsed_ms=raw.get("elapsed_ms", 0),
            data=data,
            raw=raw,
        )

    def observe_all(self) -> FullObservation:
        """Run all read-only probes and return a composite observation."""
        a2a_obs = self._a2a.observe()
        mcp_obs = self._mcp.observe()
        openapi = self._probe_public("openapi", ENDPOINTS["openapi"])
        stats = self._probe_public("community_stats", ENDPOINTS["community_stats"])
        caps = self._probe_public("capabilities", ENDPOINTS["capabilities"])
        dems = self._probe_public("demands", ENDPOINTS["demands"])
        return FullObservation(
            a2a=a2a_obs,
            mcp=mcp_obs,
            openapi=openapi,
            community_stats=stats,
            capabilities=caps,
            demands=dems,
        )


def _summarize_list(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"available": False}
    data = payload.get("data", payload)
    items = None
    if isinstance(data, dict):
        for key in ("items", "records", "list", "capabilities", "demands"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    elif isinstance(data, list):
        items = data
    sample = []
    for item in (items or [])[:3]:
        if isinstance(item, dict):
            sample.append({
                k: item.get(k)
                for k in ("id", "title", "name", "category", "price_ut", "status")
                if k in item
            })
    return {"available": True, "item_count": len(items or []), "sample": sample}


# ---------------------------------------------------------------------------
# CLI entry point (for standalone usage)
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the read-only adapter from the command line."""
    import argparse

    parser = argparse.ArgumentParser(
        description="UUMit A2A/MCP read-only observation adapter (spike)"
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--base-url", default=UUMIT_API_BASE)
    parser.add_argument("--a2a-only", action="store_true")
    parser.add_argument("--mcp-only", action="store_true")
    args = parser.parse_args()

    adapter = UUMitReadonlyAdapter(base_url=args.base_url, timeout=args.timeout)

    if args.a2a_only:
        obs = adapter._a2a.observe()
        print(json.dumps(obs.to_dict(), ensure_ascii=False, indent=2))
    elif args.mcp_only:
        obs = adapter._mcp.observe()
        print(json.dumps(obs.to_dict(), ensure_ascii=False, indent=2))
    else:
        obs = adapter.observe_all()
        print(obs.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
