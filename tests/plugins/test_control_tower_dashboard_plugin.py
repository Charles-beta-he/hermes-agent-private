from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _load_plugin_router():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "control-tower" / "dashboard" / "plugin_api.py"
    assert plugin_file.exists(), f"plugin file missing: {plugin_file}"
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_control_tower_test",
        plugin_file,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.router


def test_control_tower_dashboard_plugin_exposes_read_only_snapshot(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    response = client.get("/api/plugins/control-tower/snapshot")

    assert response.status_code == 200
    data = response.json()
    assert data["ledger"]["canonical_fact_source"] == "Control Tower Ledger"
    assert data["ui_host"] == {
        "selected": "dashboard_plugin",
        "plugin_name": "control-tower",
        "reason": "Drop-in dashboard plugin integrates with existing /api/plugins host without making the dashboard a fact source.",
    }


def test_control_tower_plugin_manifest_declares_dashboard_host():
    repo_root = Path(__file__).resolve().parents[2]
    manifest = repo_root / "plugins" / "control-tower" / "dashboard" / "manifest.json"

    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    assert '"name": "control-tower"' in text
    assert '"api": "plugin_api.py"' in text
    assert '"path": "/control-tower"' in text


def test_control_tower_dashboard_plugin_proposes_command_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    response = client.post(
        "/api/plugins/control-tower/commands/propose",
        json={
            "command_type": "RefreshProjection",
            "actor_id": "charles",
            "target_type": "projection",
            "target_id": "overview",
            "risk_level": "L0",
            "payload": {"reason": "manual refresh"},
            "idempotency_key": "dashboard-refresh-overview",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "proposed"
    assert data["execution"] == "not_executed"
    assert data["requires_human"] is False
    assert data["source_surface"] == "dashboard"
