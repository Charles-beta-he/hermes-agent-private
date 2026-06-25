from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import control_tower

PLUGIN_API = Path(__file__).resolve().parents[2] / "plugins" / "control-tower" / "dashboard" / "plugin_api.py"


def load_router():
    spec = importlib.util.spec_from_file_location("control_tower_plugin_api", PLUGIN_API)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.router


def test_workflow_kernel_policy_editor_decision_api_records_local_intent(monkeypatch, tmp_path):
    paths = {
        "base": tmp_path,
        "ledger": tmp_path / "approvals.jsonl",
        "projection": tmp_path / "projection.json",
    }
    monkeypatch.setattr(control_tower, "_workflow_kernel_policy_editor_paths", lambda: paths)
    app = FastAPI()
    app.include_router(load_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    response = client.post(
        "/api/plugins/control-tower/workflow-kernel/policy-editor/decision",
        json={"decision": "approve_dry_run_only", "actor_id": "pytest", "reason": "dry-run only"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recorded"
    assert body["event"]["unattended_dispatch_enabled"] is False
    assert body["event"]["external_side_effects_executed"] is False
    assert paths["ledger"].exists()


def test_workflow_kernel_policy_editor_decision_api_blocks_unknown_decision(monkeypatch, tmp_path):
    paths = {
        "base": tmp_path,
        "ledger": tmp_path / "approvals.jsonl",
        "projection": tmp_path / "projection.json",
    }
    monkeypatch.setattr(control_tower, "_workflow_kernel_policy_editor_paths", lambda: paths)
    app = FastAPI()
    app.include_router(load_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    response = client.post(
        "/api/plugins/control-tower/workflow-kernel/policy-editor/decision",
        json={"decision": "enable_unattended_send", "actor_id": "pytest"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["event"]["unattended_dispatch_enabled"] is False


def test_loop_runtime_promotion_authorization_api_records_runtime_api_only(monkeypatch, tmp_path):
    paths = {
        "base": tmp_path,
        "ledger": tmp_path / "runtime_promotion_authorizations.jsonl",
        "projection": tmp_path / "runtime_promotion_activation.json",
    }
    monkeypatch.setattr(control_tower, "_loop_runtime_promotion_paths", lambda: paths)
    app = FastAPI()
    app.include_router(load_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    response = client.post(
        "/api/plugins/control-tower/workflow-kernel/loop-runtime-promotion/decision",
        json={
            "decision": "approve_runtime_api_only",
            "actor_id": "pytest",
            "reason": "user authorized runtime api activation only",
            "packet_id": "authz.hermes.workflow_kernel.control_tower_runtime_promotion.v1",
            "executor_binding": "dashboard_plugin:/api/plugins/control-tower/snapshot",
            "budget_max_turns": 12,
            "budget_runtime_seconds": 600,
            "rollback_command": "delete runtime_promotion_activation.json",
            "kill_switch": "disable_loop_runtime_contract_promotion",
            "evaluator_command": "pytest targeted control tower snapshot",
            "readback_surface": "workflow_kernel_control_tower.runtime_api_activation",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recorded"
    assert body["event"]["runtime_api_enabled"] is True
    assert body["event"]["scheduler_enabled"] is False
    assert body["event"]["worker_spawn_enabled"] is False
    assert body["event"]["taskpool_write_enabled"] is False
    assert body["event"]["external_side_effects_executed"] is False
    assert body["projection"]["status"] == "RUNTIME_API_AUTHORIZED_LOCAL_ONLY"
    assert paths["ledger"].exists()
    assert paths["projection"].exists()


def test_loop_runtime_promotion_authorization_api_blocks_scheduler_scope(monkeypatch, tmp_path):
    paths = {
        "base": tmp_path,
        "ledger": tmp_path / "runtime_promotion_authorizations.jsonl",
        "projection": tmp_path / "runtime_promotion_activation.json",
    }
    monkeypatch.setattr(control_tower, "_loop_runtime_promotion_paths", lambda: paths)
    app = FastAPI()
    app.include_router(load_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    response = client.post(
        "/api/plugins/control-tower/workflow-kernel/loop-runtime-promotion/decision",
        json={"decision": "enable_scheduler", "actor_id": "pytest"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["event"]["runtime_api_enabled"] is False
    assert body["event"]["scheduler_enabled"] is False
    assert body["event"]["external_side_effects_executed"] is False
