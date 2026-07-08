from __future__ import annotations

import json

import pytest


def _write_packet(path):
    packet = {
        "schema": "hermes_context_packet_v1",
        "objective": {"goal": "API preflight projection", "non_goals": ["runtime mutation"]},
        "sources": [{"id": "src-1", "kind": "local_file", "locator": "fixtures/source.md", "claim": "source claim", "evidence": "readback"}],
        "constraints": {"red_lines": ["read-only"], "negative_cases": ["missing readback"], "memory_policy": "do_not_save_wip"},
        "skills": [{"name": "context-engineering-os", "reason": "governs context packet"}],
        "authority": {"external_dispatch": False, "publish_deploy_push": False, "destructive": False, "credential_entry": False},
        "validation": {"required_checks": [{"id": "lint", "command": "context lint", "evidence_refs": ["src-1"]}]},
        "evidence_map": [{"id": "src-1", "source_id": "src-1", "claim": "claim", "verification": "readback"}],
    }
    path.write_text(json.dumps(packet, indent=2), encoding="utf-8")


@pytest.fixture
def client(monkeypatch, tmp_path):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    web_server.app.state.auth_required = False
    return TestClient(web_server.app), web_server


def test_context_preflight_api_requires_dashboard_session_token(client, tmp_path):
    test_client, _web_server = client
    packet = tmp_path / "packet.json"
    _write_packet(packet)

    response = test_client.get("/api/context/preflight", params={"packet": str(packet)})

    assert response.status_code == 401


def test_context_preflight_api_projects_disabled_default_without_writes(client, tmp_path):
    test_client, web_server = client
    packet = tmp_path / "packet.json"
    _write_packet(packet)

    response = test_client.get(
        "/api/context/preflight",
        params={"packet": str(packet)},
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["packet_checked"] is False
    assert payload["side_effects"] == {"external": False, "llm": False, "writes": []}
    assert payload["runtime_prompt_injection"] is False


def test_context_preflight_api_projects_enabled_valid_packet(client, tmp_path, monkeypatch):
    test_client, web_server = client
    hermes_home = tmp_path / "home"
    (hermes_home / "config.yaml").write_text("agent:\n  context_engineering_packet_preflight: true\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    packet = tmp_path / "packet.json"
    _write_packet(packet)

    response = test_client.get(
        "/api/context/preflight",
        params={"packet": str(packet)},
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["packet_checked"] is True
    assert payload["result"]["closed_authority"] == 4
    assert payload["side_effects"]["writes"] == []


def test_context_preflight_api_returns_blocked_projection_with_400_when_enabled_missing_packet(client, tmp_path, monkeypatch):
    test_client, web_server = client
    hermes_home = tmp_path / "home"
    (hermes_home / "config.yaml").write_text("agent:\n  context_engineering_packet_preflight: true\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    response = test_client.get(
        "/api/context/preflight",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "blocked"
    assert "requires --packet" in payload["message"]
    assert payload["side_effects"]["writes"] == []
