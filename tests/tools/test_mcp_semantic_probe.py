"""Tests for tools.mcp_semantic_probe — semantic health probes for MCP tools."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Ensure clean MCP module state before/after each test."""
    import tools.mcp_tool as mcp
    old_loop = mcp._mcp_loop
    old_thread = mcp._mcp_thread
    old_servers = dict(mcp._servers)
    yield
    mcp._servers.clear()
    mcp._servers.update(old_servers)
    mcp._mcp_loop = old_loop
    mcp._mcp_thread = old_thread


def _make_mock_server(tools=None, resources=None, prompts=None,
                      init_result=None, session_ok=True):
    """Build a mock MCPServerTask with configurable capabilities."""
    server = MagicMock()
    server._tools = tools or []
    server._registered_tool_names = [getattr(t, "name", "t") for t in (tools or [])]
    server.session = MagicMock() if session_ok else None
    server.shutdown = AsyncMock()

    if init_result is not None:
        server.initialize_result = init_result

    # Resource/prompt listing mocks
    if session_ok:
        resource_result = SimpleNamespace(resources=resources or [])
        prompt_result = SimpleNamespace(prompts=prompts or [])
        server.session.list_resources = AsyncMock(return_value=resource_result)
        server.session.list_prompts = AsyncMock(return_value=prompt_result)
        # Default call_tool returns a structured error
        error_result = SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="invalid arguments")],
        )
        server.session.call_tool = AsyncMock(return_value=error_result)

    return server


def _run_coro(coro_or_factory, timeout=30):
    """Run an async coroutine in a fresh event loop."""
    coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Schema validation unit tests
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    """Tests for _validate_schema_node and _validate_tool_schemas."""

    def test_valid_object_schema(self):
        from tools.mcp_semantic_probe import _validate_schema_node, Finding
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        findings = []
        _validate_schema_node(schema, "root", findings, "test")
        assert len(findings) == 0

    def test_invalid_type_flagged(self):
        from tools.mcp_semantic_probe import _validate_schema_node, Finding, FindingSeverity
        schema = {"type": "faketype", "properties": {}}
        findings = []
        _validate_schema_node(schema, "root", findings, "test")
        assert any(f.severity == FindingSeverity.WARNING and "faketype" in f.message
                   for f in findings)

    def test_required_without_properties_flagged(self):
        from tools.mcp_semantic_probe import _validate_schema_node, Finding, FindingSeverity
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a", "b"],
        }
        findings = []
        _validate_schema_node(schema, "root", findings, "test")
        assert any("b" in f.message and f.severity == FindingSeverity.ERROR
                   for f in findings)

    def test_nested_properties_validated(self):
        from tools.mcp_semantic_probe import _validate_schema_node, Finding, FindingSeverity
        schema = {
            "type": "object",
            "properties": {
                "inner": {
                    "type": "badtype",
                    "properties": {},
                },
            },
        }
        findings = []
        _validate_schema_node(schema, "root", findings, "test")
        assert any("badtype" in f.message for f in findings)

    def test_anyof_validated(self):
        from tools.mcp_semantic_probe import _validate_schema_node, Finding, FindingSeverity
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "weird"},
            ],
        }
        findings = []
        _validate_schema_node(schema, "root", findings, "test")
        assert any("weird" in f.message for f in findings)

    def test_validate_tool_schemas_no_schema(self):
        from tools.mcp_semantic_probe import _validate_tool_schemas, Finding, FindingSeverity
        tool = SimpleNamespace(name="my_tool")  # no inputSchema
        findings = []
        issues = _validate_tool_schemas([tool], "srv", findings)
        assert issues == 1
        assert any("no inputSchema" in f.message for f in findings)

    def test_validate_tool_schemas_valid(self):
        from tools.mcp_semantic_probe import _validate_tool_schemas, Finding
        tool = SimpleNamespace(
            name="good_tool",
            inputSchema={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        findings = []
        issues = _validate_tool_schemas([tool], "srv", findings)
        assert issues == 0


# ---------------------------------------------------------------------------
# Probe result types
# ---------------------------------------------------------------------------

class TestProbeResultTypes:
    """Tests for ProbeResult, Finding, ProbeStatus serialization."""

    def test_finding_to_dict(self):
        from tools.mcp_semantic_probe import Finding, FindingSeverity
        f = Finding(
            probe_name="test_probe",
            severity=FindingSeverity.WARNING,
            message="something happened",
            details={"key": "value"},
        )
        d = f.to_dict()
        assert d["probe"] == "test_probe"
        assert d["severity"] == "warning"
        assert d["message"] == "something happened"
        assert d["details"] == {"key": "value"}

    def test_finding_to_dict_no_details(self):
        from tools.mcp_semantic_probe import Finding, FindingSeverity
        f = Finding(probe_name="x", severity=FindingSeverity.INFO, message="ok")
        d = f.to_dict()
        assert "details" not in d

    def test_probe_result_to_dict(self):
        from tools.mcp_semantic_probe import ProbeResult, ProbeStatus, Finding, FindingSeverity
        r = ProbeResult(
            server_name="test_srv",
            status=ProbeStatus.HEALTHY,
            findings=[Finding("p", FindingSeverity.INFO, "good")],
            latency_ms=42.5,
            tool_count=3,
            probes_run=["connectivity", "schema_validation"],
        )
        d = r.to_dict()
        assert d["server"] == "test_srv"
        assert d["status"] == "healthy"
        assert d["latency_ms"] == 42.5
        assert d["tools"] == 3
        assert len(d["findings"]) == 1


# ---------------------------------------------------------------------------
# Status determination
# ---------------------------------------------------------------------------

class TestDetermineStatus:
    """Tests for _determine_status logic."""

    def test_healthy_with_info_only(self):
        from tools.mcp_semantic_probe import (
            _determine_status, Finding, FindingSeverity, ProbeStatus,
        )
        findings = [Finding("p", FindingSeverity.INFO, "ok")]
        assert _determine_status(findings) == ProbeStatus.HEALTHY

    def test_degraded_with_warning(self):
        from tools.mcp_semantic_probe import (
            _determine_status, Finding, FindingSeverity, ProbeStatus,
        )
        findings = [
            Finding("p", FindingSeverity.INFO, "ok"),
            Finding("p", FindingSeverity.WARNING, "warn"),
        ]
        assert _determine_status(findings) == ProbeStatus.DEGRADED

    def test_degraded_with_error(self):
        from tools.mcp_semantic_probe import (
            _determine_status, Finding, FindingSeverity, ProbeStatus,
        )
        findings = [Finding("p", FindingSeverity.ERROR, "err")]
        assert _determine_status(findings) == ProbeStatus.DEGRADED

    def test_unhealthy_with_critical(self):
        from tools.mcp_semantic_probe import (
            _determine_status, Finding, FindingSeverity, ProbeStatus,
        )
        findings = [Finding("p", FindingSeverity.CRITICAL, "fail")]
        assert _determine_status(findings) == ProbeStatus.UNHEALTHY

    def test_empty_findings_healthy(self):
        from tools.mcp_semantic_probe import _determine_status, ProbeStatus
        assert _determine_status([]) == ProbeStatus.HEALTHY


# ---------------------------------------------------------------------------
# Probe connected server (in-process, no reconnect)
# ---------------------------------------------------------------------------

class TestProbeConnectedServer:
    """Tests for probe_connected_server against already-connected servers."""

    def test_returns_none_when_not_connected(self):
        from tools.mcp_semantic_probe import probe_connected_server
        result = probe_connected_server("nonexistent_server")
        assert result is None

    def test_returns_none_when_mcp_unavailable(self):
        with patch("tools.mcp_tool._MCP_AVAILABLE", False):
            from tools.mcp_semantic_probe import probe_connected_server
            result = probe_connected_server("any")
            assert result is None

    def test_probes_connected_server_with_valid_tools(self):
        import tools.mcp_tool as mcp
        tool = SimpleNamespace(
            name="good_tool",
            inputSchema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        server = _make_mock_server(tools=[tool])

        with mcp._lock:
            mcp._servers["test_srv"] = server

        from tools.mcp_semantic_probe import probe_connected_server, ProbeStatus
        result = probe_connected_server("test_srv")

        assert result is not None
        assert result.server_name == "test_srv"
        assert result.tool_count == 1
        assert result.status in (ProbeStatus.HEALTHY, ProbeStatus.DEGRADED)
        assert "schema_validation" in result.probes_run
        assert "capability_consistency" in result.probes_run

    def test_probes_server_with_no_tools(self):
        import tools.mcp_tool as mcp
        server = _make_mock_server(tools=[])

        with mcp._lock:
            mcp._servers["empty_srv"] = server

        from tools.mcp_semantic_probe import probe_connected_server, ProbeStatus
        result = probe_connected_server("empty_srv")

        assert result is not None
        assert result.tool_count == 0
        # Should be degraded due to "no tools" warning
        assert result.status == ProbeStatus.DEGRADED

    def test_probes_server_with_bad_schema(self):
        import tools.mcp_tool as mcp
        tool = SimpleNamespace(
            name="bad_tool",
            inputSchema={"type": "object", "properties": {"a": {"type": "string"}},
                         "required": ["a", "missing"]},
        )
        server = _make_mock_server(tools=[tool])

        with mcp._lock:
            mcp._servers["bad_srv"] = server

        from tools.mcp_semantic_probe import probe_connected_server, ProbeStatus, FindingSeverity
        result = probe_connected_server("bad_srv")

        assert result is not None
        assert result.status == ProbeStatus.DEGRADED
        assert any(f.severity == FindingSeverity.ERROR for f in result.findings)

    def test_probes_server_with_capabilities(self):
        import tools.mcp_tool as mcp
        tool = SimpleNamespace(
            name="t1",
            inputSchema={"type": "object", "properties": {}},
        )
        caps = SimpleNamespace(
            tools=SimpleNamespace(),
            resources=SimpleNamespace(),
            prompts=None,
        )
        init_result = SimpleNamespace(capabilities=caps)
        server = _make_mock_server(tools=[tool], init_result=init_result)

        with mcp._lock:
            mcp._servers["cap_srv"] = server

        from tools.mcp_semantic_probe import probe_connected_server
        result = probe_connected_server("cap_srv")

        assert result is not None
        cap_findings = [f for f in result.findings if f.probe_name == "capability_consistency"]
        # Should note resources capability, not prompts
        assert any("resources" in f.message for f in cap_findings)
        assert not any("prompts" in f.message for f in cap_findings)


# ---------------------------------------------------------------------------
# Full probe orchestrator (with mocked connections)
# ---------------------------------------------------------------------------

class TestRunSemanticProbes:
    """Tests for run_semantic_probes and run_semantic_probe_for_server."""

    def test_returns_empty_when_mcp_unavailable(self):
        with patch("tools.mcp_tool._MCP_AVAILABLE", False):
            from tools.mcp_semantic_probe import run_semantic_probes
            result = run_semantic_probes()
        assert result == []

    def test_returns_empty_when_no_config(self):
        with patch("tools.mcp_tool._load_mcp_config", return_value={}):
            from tools.mcp_semantic_probe import run_semantic_probes
            result = run_semantic_probes()
        assert result == []

    def test_returns_empty_when_all_disabled(self):
        config = {"srv": {"command": "npx", "enabled": False}}
        with patch("tools.mcp_tool._load_mcp_config", return_value=config):
            from tools.mcp_semantic_probe import run_semantic_probes
            result = run_semantic_probes()
        assert result == []

    def test_probe_for_server_returns_unreachable_on_connect_failure(self):
        from tools.mcp_semantic_probe import run_semantic_probe_for_server, ProbeStatus

        config = {"command": "nonexistent", "connect_timeout": 5}

        with patch("tools.mcp_tool._MCP_AVAILABLE", True), \
             patch("tools.mcp_tool._ensure_mcp_loop"), \
             patch("tools.mcp_tool._run_on_mcp_loop",
                   side_effect=ConnectionError("refused")), \
             patch("tools.mcp_tool._stop_mcp_loop"):

            result = run_semantic_probe_for_server("broken", config)

        assert result.status == ProbeStatus.UNREACHABLE
        assert any("refused" in f.message or "ConnectionError" in f.message
                   for f in result.findings)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

class TestCLI:
    """Tests for the standalone CLI _main function."""

    def test_main_no_servers(self, capsys):
        with patch("tools.mcp_semantic_probe.run_semantic_probes", return_value=[]):
            from tools.mcp_semantic_probe import _main
            with pytest.raises(SystemExit) as exc_info:
                _main()
            assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "No MCP servers" in captured.out

    def test_main_with_results(self, capsys):
        from tools.mcp_semantic_probe import (
            ProbeResult, ProbeStatus, Finding, FindingSeverity,
        )
        results = [
            ProbeResult(
                server_name="good_srv",
                status=ProbeStatus.HEALTHY,
                findings=[Finding("p", FindingSeverity.INFO, "all good")],
                latency_ms=15.0,
                tool_count=5,
                probes_run=["connectivity"],
            ),
            ProbeResult(
                server_name="bad_srv",
                status=ProbeStatus.UNHEALTHY,
                findings=[Finding("p", FindingSeverity.CRITICAL, "dead")],
                probes_run=["connectivity"],
            ),
        ]
        with patch("tools.mcp_semantic_probe.run_semantic_probes", return_value=results):
            from tools.mcp_semantic_probe import _main
            with pytest.raises(SystemExit) as exc_info:
                _main()
            assert exc_info.value.code == 1  # has_issues
        captured = capsys.readouterr()
        assert "good_srv" in captured.out
        assert "bad_srv" in captured.out
        assert "healthy" in captured.out

    def test_main_json_output(self, capsys):
        from tools.mcp_semantic_probe import (
            ProbeResult, ProbeStatus, Finding, FindingSeverity,
        )
        results = [
            ProbeResult(server_name="s", status=ProbeStatus.HEALTHY,
                        probes_run=["connectivity"]),
            ProbeResult(server_name="bad", status=ProbeStatus.UNHEALTHY,
                        findings=[Finding("p", FindingSeverity.CRITICAL, "dead")],
                        probes_run=["connectivity"]),
        ]
        with patch("tools.mcp_semantic_probe.run_semantic_probes", return_value=results), \
             patch("sys.argv", ["prog", "--json"]):
            from tools.mcp_semantic_probe import _main
            with pytest.raises(SystemExit) as exc_info:
                _main()
            assert exc_info.value.code == 1  # has_issues due to UNHEALTHY
        captured = capsys.readouterr()
        assert "--- JSON ---" in captured.out
        # Verify the JSON is valid
        json_start = captured.out.index("--- JSON ---") + len("--- JSON ---")
        json_text = captured.out[json_start:].strip()
        parsed = json.loads(json_text)
        assert len(parsed) == 2
        assert parsed[0]["server"] == "s"


# ---------------------------------------------------------------------------
# Integration: get_mcp_status with include_semantic
# ---------------------------------------------------------------------------

class TestGetMcpStatusSemantic:
    """Tests for get_mcp_status(include_semantic=True)."""

    def test_semantic_field_absent_when_not_requested(self):
        import tools.mcp_tool as mcp
        tool = SimpleNamespace(
            name="t",
            inputSchema={"type": "object", "properties": {}},
        )
        server = _make_mock_server(tools=[tool])

        config = {"test_srv": {"command": "npx"}}
        with patch.object(mcp, "_load_mcp_config", return_value=config):
            with mcp._lock:
                mcp._servers["test_srv"] = server
            result = mcp.get_mcp_status(include_semantic=False)

        assert len(result) == 1
        assert "semantic_health" not in result[0]

    def test_semantic_field_present_when_requested(self):
        import tools.mcp_tool as mcp
        tool = SimpleNamespace(
            name="t",
            inputSchema={"type": "object", "properties": {}},
        )
        server = _make_mock_server(tools=[tool])

        config = {"test_srv": {"command": "npx"}}
        with patch.object(mcp, "_load_mcp_config", return_value=config):
            with mcp._lock:
                mcp._servers["test_srv"] = server
            result = mcp.get_mcp_status(include_semantic=True)

        assert len(result) == 1
        assert "semantic_health" in result[0]
        sh = result[0]["semantic_health"]
        assert "status" in sh
        assert "findings" in sh

    def test_disconnected_server_has_no_semantic(self):
        import tools.mcp_tool as mcp
        config = {"dead_srv": {"command": "npx"}}
        with patch.object(mcp, "_load_mcp_config", return_value=config):
            result = mcp.get_mcp_status(include_semantic=True)

        assert result[0]["connected"] is False
        assert "semantic_health" not in result[0]
