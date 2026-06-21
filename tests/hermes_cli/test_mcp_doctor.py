from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    hh = tmp_path / "hermes-home"
    hh.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setattr("hermes_cli.config.get_hermes_home", lambda: hh)
    monkeypatch.setattr("hermes_cli.config.get_config_path", lambda: hh / "config.yaml")
    monkeypatch.setattr("hermes_cli.config.get_env_path", lambda: hh / ".env")
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hh)
    return hh


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch):
    cat = tmp_path / "optional-mcps"
    cat.mkdir()
    monkeypatch.setenv("HERMES_OPTIONAL_MCPS", str(cat))
    return cat


def _write_manifest(catalog_dir: Path, name: str, body: dict) -> Path:
    entry_dir = catalog_dir / name
    entry_dir.mkdir(exist_ok=True)
    path = entry_dir / "manifest.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def _manifest(name: str = "demo", **overrides) -> dict:
    body = {
        "manifest_version": 1,
        "name": name,
        "description": "Demo MCP",
        "source": "https://example.com/demo",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "demo-mcp"]},
        "auth": {
            "type": "api_key",
            "env": [{"name": "DEMO_API_KEY", "prompt": "API key", "secret": True}],
        },
        "tools": {"default_enabled": ["demo_read"]},
    }
    body.update(overrides)
    return body


def test_doctor_reports_catalog_config_probe_and_redacts_secrets(hermes_home, catalog_dir, monkeypatch):
    _write_manifest(catalog_dir, "demo", _manifest())
    (hermes_home / ".env").write_text("DEMO_API_KEY=super-secret-token\n", encoding="utf-8")
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "demo": {
                        "command": "npx",
                        "args": ["-y", "demo-mcp"],
                        "enabled": True,
                        "headers": {"Authorization": "Bearer ${DEMO_API_KEY}"},
                        "env": {"PRIVATE_VALUE": "do-not-leak"},
                        "tools": {"include": ["demo_read"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    from hermes_cli.mcp_doctor import build_mcp_doctor_report

    with patch("hermes_cli.mcp_doctor._probe_single_server", return_value=[("demo_read", "Read demo")]):
        report = build_mcp_doctor_report("demo")

    dumped = json.dumps(report, sort_keys=True)
    assert "super-secret-token" not in dumped
    assert "do-not-leak" not in dumped
    assert report["name"] == "demo"
    assert report["catalog"]["present"] is True
    assert "Nous-approved" in report["catalog"]["trust_note"]
    assert report["config"]["present"] is True
    assert report["config"]["enabled"] is True
    assert report["transport"] == {"type": "stdio", "command": "npx", "args": ["-y", "demo-mcp"], "url": None, "version": None}
    assert report["auth"]["type"] == "api_key"
    assert report["auth"]["requirements"][0] == {"name": "DEMO_API_KEY", "required": True, "secret": True, "status": "present"}
    assert report["tools_filter"] == {"mode": "include", "include": ["demo_read"], "exclude": [], "enabled_count": 1}
    assert report["probe"]["ok"] is True
    assert report["probe"]["tool_count"] == 1
    assert report["probe"]["last_error"] is None


def test_doctor_reports_missing_catalog_entry_without_probe(hermes_home, catalog_dir):
    from hermes_cli.mcp_doctor import build_mcp_doctor_report

    report = build_mcp_doctor_report("missing", probe=False)

    assert report["name"] == "missing"
    assert report["catalog"]["present"] is False
    assert report["config"] == {"present": False, "enabled": False}
    assert report["transport"]["type"] == "unknown"
    assert report["probe"] == {
        "attempted": False,
        "ok": False,
        "tool_count": 0,
        "tools": [],
        "last_error": "not_configured: MCP server 'missing' is not present in config.yaml or catalog",
        "action": "Install a catalog MCP with `hermes mcp install missing`, or add a custom server with `hermes mcp add missing ...`.",
    }


def test_doctor_reports_catalog_entry_not_installed_without_probe(hermes_home, catalog_dir):
    _write_manifest(catalog_dir, "demo", _manifest())
    from hermes_cli.mcp_doctor import build_mcp_doctor_report

    report = build_mcp_doctor_report("demo", probe=False)

    assert report["catalog"]["present"] is True
    assert report["config"] == {"present": False, "enabled": False}
    assert report["probe"]["last_error"] == "not_installed: catalog MCP 'demo' is not installed in config.yaml"
    assert report["probe"]["action"] == "Install it with `hermes mcp install demo` or open `hermes mcp picker`."


def test_doctor_reports_disabled_config_without_probe(hermes_home, catalog_dir):
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"mcp_servers": {"off": {"url": "https://mcp.example/mcp", "enabled": False}}}),
        encoding="utf-8",
    )

    from hermes_cli.mcp_doctor import build_mcp_doctor_report

    report = build_mcp_doctor_report("off")

    assert report["config"] == {"present": True, "enabled": False}
    assert report["transport"]["type"] == "http"
    assert report["probe"]["attempted"] is False
    assert report["probe"]["last_error"] == "disabled: MCP server 'off' is configured but disabled"
    assert report["probe"]["action"] == "Enable it in config.yaml or reinstall/toggle it from `hermes mcp picker`."
