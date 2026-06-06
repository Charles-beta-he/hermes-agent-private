"""Tests for tools.uumit_a2a_mcp_observer — read-only observation adapter spike.

Tests verify:
  1. Hard guards: forbidden methods raise PermissionError
  2. A2A observer correctly probes agent card and auth guard
  3. MCP observer correctly probes SSE handshake
  4. Composite adapter assembles FullObservation correctly
  5. No credentials are ever read or transmitted
  6. All HTTP requests use read-only methods (GET/POST only for A2A guard)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

from tools.uumit_a2a_mcp_observer import (
    DEFAULT_TIMEOUT,
    ENDPOINTS,
    UUMIT_API_BASE,
    A2AObservation,
    FullObservation,
    MCPObservation,
    ProbeResult,
    UUMitA2AObserver,
    UUMitMCPObserver,
    UUMitReadonlyAdapter,
    _FORBIDDEN_METHODS,
    _http_request,
    _sse_handshake,
    _summarize_agent_card,
    _summarize_list,
    _summarize_openapi,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status=200, body=b'{}', content_type='application/json'):
    """Build a mock urllib response object."""
    resp = MagicMock()
    resp.status = status
    resp.read = MagicMock(return_value=body)
    resp.headers = {"content-type": content_type}
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_http_error(code=401, body=b'{"error":"unauthorized"}', content_type='application/json'):
    """Build a mock HTTPError."""
    import urllib.error
    err = urllib.error.HTTPError(
        url="https://api.uumit.com/test",
        code=code,
        msg="Unauthorized",
        hdrs={"content-type": content_type},
        fp=MagicMock(read=MagicMock(return_value=body)),
    )
    return err


# ---------------------------------------------------------------------------
# Guard tests — forbidden methods
# ---------------------------------------------------------------------------

class TestForbiddenMethods:
    """Verify the read-only adapter rejects all mutating HTTP methods."""

    @pytest.mark.parametrize("method", sorted(_FORBIDDEN_METHODS))
    def test_forbidden_method_raises(self, method):
        with pytest.raises(PermissionError, match=f"Read-only adapter refuses {method}"):
            _http_request(method, "https://api.uumit.com/test")

    def test_get_allowed(self):
        """GET should not raise PermissionError (may fail at network level)."""
        with patch("tools.uumit_a2a_mcp_observer.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionError("network blocked")
            # Should NOT raise PermissionError
            result = _http_request("GET", "https://api.uumit.com/test")
            assert result["ok"] is False
            assert "PermissionError" not in result.get("error_type", "")

    def test_post_allowed(self):
        """POST is allowed (for the A2A auth-guard probe)."""
        with patch("tools.uumit_a2a_mcp_observer.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionError("network blocked")
            result = _http_request("POST", "https://api.uumit.com/a2a", body=b'{}')
            assert result["ok"] is False
            assert "PermissionError" not in result.get("error_type", "")


# ---------------------------------------------------------------------------
# ProbeResult data class
# ---------------------------------------------------------------------------

class TestProbeResult:
    def test_to_dict(self):
        pr = ProbeResult(name="test", ok=True, status=200, elapsed_ms=42, data={"x": 1})
        d = pr.to_dict()
        assert d["name"] == "test"
        assert d["ok"] is True
        assert d["status"] == 200
        assert d["elapsed_ms"] == 42
        assert d["data"] == {"x": 1}


# ---------------------------------------------------------------------------
# OpenAPI summariser
# ---------------------------------------------------------------------------

class TestSummarizeOpenapi:
    def test_none_returns_unavailable(self):
        assert _summarize_openapi(None) == {"available": False}

    def test_non_dict_returns_unavailable(self):
        assert _summarize_openapi("garbage") == {"available": False}

    def test_extracts_basic_fields(self):
        payload = {
            "openapi": "3.1.0",
            "info": {"title": "UUMit API", "version": "1.0"},
            "paths": {
                "/api/v1/users": {"get": {"tags": ["users"]}},
                "/api/v1/tasks": {"get": {"tags": ["tasks"]}, "post": {"tags": ["tasks"]}},
            },
            "components": {"schemas": {"User": {}, "Task": {}}},
        }
        s = _summarize_openapi(payload)
        assert s["available"] is True
        assert s["openapi_version"] == "3.1.0"
        assert s["path_count"] == 2
        assert s["method_counts"]["GET"] == 2
        assert s["method_counts"]["POST"] == 1
        assert s["schema_count"] == 2
        assert len(s["top_tags"]) > 0

    def test_classifies_read_only_vs_write_paths(self):
        payload = {
            "openapi": "3.1.0",
            "info": {},
            "paths": {
                "/users": {"get": {}},
                "/tasks": {"get": {}, "post": {}},
                "/admin": {"delete": {}},
            },
        }
        s = _summarize_openapi(payload)
        assert "/users" in s["read_only_get_paths"]
        assert "/admin" in s["write_paths_sample"]


# ---------------------------------------------------------------------------
# Agent Card summariser
# ---------------------------------------------------------------------------

class TestSummarizeAgentCard:
    def test_none_returns_unavailable(self):
        assert _summarize_agent_card(None) == {"available": False}

    def test_extracts_agent_card_fields(self):
        card = {
            "name": "UUMit Agent",
            "description": "Agent marketplace",
            "protocol": "a2a/v1",
            "url": "https://api.uumit.com/a2a",
            "authentication": {"schemes": ["bearer"]},
            "capabilities": {"streaming": True},
            "skills": [
                {"id": "s1", "name": "Task Solver"},
                {"id": "s2", "name": "Code Writer"},
            ],
        }
        s = _summarize_agent_card(card)
        assert s["available"] is True
        assert s["name"] == "UUMit Agent"
        assert s["protocol_version"] == "a2a/v1"
        assert s["skill_count"] == 2
        assert len(s["skill_sample"]) == 2
        assert s["authentication_scheme"] == ["schemes"]


# ---------------------------------------------------------------------------
# List summariser
# ---------------------------------------------------------------------------

class TestSummarizeList:
    def test_none_returns_unavailable(self):
        assert _summarize_list(None) == {"available": False}

    def test_extracts_items(self):
        payload = {
            "data": {
                "items": [
                    {"id": "1", "name": "Agent A", "price_ut": 10},
                    {"id": "2", "name": "Agent B", "price_ut": 5},
                ]
            }
        }
        s = _summarize_list(payload)
        assert s["available"] is True
        assert s["item_count"] == 2
        assert len(s["sample"]) == 2


# ---------------------------------------------------------------------------
# A2A Observer
# ---------------------------------------------------------------------------

class TestUUMitA2AObserver:
    def test_observe_agent_card_success(self):
        card_data = {
            "name": "UUMit",
            "protocol": "a2a/v1",
            "skills": [{"id": "s1", "name": "Solver"}],
        }
        with patch(
            "tools.uumit_a2a_mcp_observer._http_request",
            return_value={
                "ok": True, "status": 200, "elapsed_ms": 150,
                "json": card_data,
            },
        ):
            obs = UUMitA2AObserver()
            pr = obs.observe_agent_card()
            assert pr.ok is True
            assert pr.status == 200
            assert pr.data["name"] == "UUMit"

    def test_observe_agent_card_failure(self):
        with patch(
            "tools.uumit_a2a_mcp_observer._http_request",
            return_value={"ok": False, "status": 404, "elapsed_ms": 50},
        ):
            obs = UUMitA2AObserver()
            pr = obs.observe_agent_card()
            assert pr.ok is False
            assert pr.status == 404

    def test_observe_a2a_auth_guard(self):
        with patch(
            "tools.uumit_a2a_mcp_observer._http_request",
            return_value={
                "ok": False, "status": 401, "elapsed_ms": 80,
                "json": {"error": "unauthorized"},
            },
        ):
            obs = UUMitA2AObserver()
            pr = obs.observe_a2a_auth_guard()
            assert pr.status == 401
            assert pr.data["guard_status"] == 401

    def test_observe_composite(self):
        """observe() returns an A2AObservation with both probes."""
        with patch(
            "tools.uumit_a2a_mcp_observer._http_request",
            return_value={"ok": True, "status": 200, "elapsed_ms": 100, "json": {}},
        ):
            obs = UUMitA2AObserver()
            result = obs.observe()
            assert isinstance(result, A2AObservation)
            d = result.to_dict()
            assert "agent_card" in d
            assert "a2a_auth_guard" in d


# ---------------------------------------------------------------------------
# MCP Observer
# ---------------------------------------------------------------------------

class TestUUMitMCPObserver:
    def test_observe_sse_handshake_success(self):
        with patch(
            "tools.uumit_a2a_mcp_observer._sse_handshake",
            return_value={
                "ok": True, "status": 200, "elapsed_ms": 50,
                "content_type": "text/event-stream",
                "lines": ["event: endpoint", "data: /mcp/messages"],
                "text_preview": "event: endpoint\ndata: /mcp/messages",
            },
        ):
            obs = UUMitMCPObserver()
            pr = obs.observe_sse_handshake()
            assert pr.ok is True
            assert pr.data["content_type"] == "text/event-stream"

    def test_observe_sse_handshake_auth_rejected(self):
        with patch(
            "tools.uumit_a2a_mcp_observer._sse_handshake",
            return_value={
                "ok": False, "status": 401, "elapsed_ms": 30,
                "error_type": "HTTPError",
            },
        ):
            obs = UUMitMCPObserver()
            pr = obs.observe_sse_handshake()
            assert pr.ok is False
            assert pr.status == 401

    def test_observe_composite(self):
        with patch(
            "tools.uumit_a2a_mcp_observer._sse_handshake",
            return_value={"ok": False, "status": 401, "elapsed_ms": 30},
        ):
            obs = UUMitMCPObserver()
            result = obs.observe()
            assert isinstance(result, MCPObservation)
            d = result.to_dict()
            assert "sse_handshake" in d


# ---------------------------------------------------------------------------
# Full adapter
# ---------------------------------------------------------------------------

class TestUUMitReadonlyAdapter:
    def test_observe_all_returns_full_observation(self):
        """The composite adapter assembles all probes."""
        with patch(
            "tools.uumit_a2a_mcp_observer._http_request",
            return_value={"ok": False, "status": 401, "elapsed_ms": 50, "json": None},
        ), patch(
            "tools.uumit_a2a_mcp_observer._sse_handshake",
            return_value={"ok": False, "status": 401, "elapsed_ms": 30},
        ):
            adapter = UUMitReadonlyAdapter()
            obs = adapter.observe_all()
            assert isinstance(obs, FullObservation)
            d = obs.to_dict()
            assert d["scope"] == "read_only_public_no_credentials_no_write_no_ut_spend"
            assert "a2a" in d
            assert "mcp" in d
            assert "openapi" in d
            assert "community_stats" in d

    def test_no_credentials_stored(self):
        """Adapter instance has no credential attributes."""
        adapter = UUMitReadonlyAdapter()
        # Should not have any token/key/secret attributes
        for attr in dir(adapter):
            lower = attr.lower()
            assert "token" not in lower
            assert "secret" not in lower
            assert "api_key" not in lower

    def test_json_serialisable(self):
        """FullObservation can be serialised to JSON without errors."""
        with patch(
            "tools.uumit_a2a_mcp_observer._http_request",
            return_value={"ok": False, "status": 401, "elapsed_ms": 50, "json": None},
        ), patch(
            "tools.uumit_a2a_mcp_observer._sse_handshake",
            return_value={"ok": False, "status": 401, "elapsed_ms": 30},
        ):
            adapter = UUMitReadonlyAdapter()
            obs = adapter.observe_all()
            j = obs.to_json()
            parsed = json.loads(j)
            assert parsed["scope"] == "read_only_public_no_credentials_no_write_no_ut_spend"

    def test_endpoint_constants_are_read_only_friendly(self):
        """All endpoint constants point to GET-safe or auth-guarded URLs."""
        for name, path in ENDPOINTS.items():
            assert path.startswith("/"), f"Endpoint {name} must be a path"
            # No endpoint should be a write-only destructive path
            assert "delete" not in path.lower()
            assert "destroy" not in path.lower()


# ---------------------------------------------------------------------------
# SSE handshake (unit)
# ---------------------------------------------------------------------------

class TestSSEHandshake:
    def test_network_error_returns_failure(self):
        with patch("tools.uumit_a2a_mcp_observer.urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionError("blocked")
            result = _sse_handshake("https://api.uumit.com/mcp/sse")
            assert result["ok"] is False
            assert result["error_type"] == "ConnectionError"


# ---------------------------------------------------------------------------
# Data class round-trip
# ---------------------------------------------------------------------------

class TestDataClassRoundTrip:
    def test_probe_result_round_trip(self):
        pr = ProbeResult(name="test", ok=True, status=200, elapsed_ms=10, data={"a": 1})
        d = pr.to_dict()
        assert d == {"name": "test", "ok": True, "status": 200, "elapsed_ms": 10, "data": {"a": 1}}

    def test_full_observation_json_round_trip(self):
        a2a = A2AObservation(
            agent_card=ProbeResult("card", True, 200, 50),
            a2a_auth_guard=ProbeResult("guard", False, 401, 30),
        )
        mcp = MCPObservation(
            sse_handshake=ProbeResult("sse", False, 401, 20),
        )
        full = FullObservation(
            a2a=a2a,
            mcp=mcp,
            openapi=ProbeResult("openapi", False, 401, 40),
            community_stats=ProbeResult("stats", False, 401, 35),
            capabilities=ProbeResult("caps", False, 401, 25),
            demands=ProbeResult("dems", False, 401, 15),
        )
        j = full.to_json()
        parsed = json.loads(j)
        assert parsed["a2a"]["agent_card"]["ok"] is True
        assert parsed["mcp"]["sse_handshake"]["ok"] is False
