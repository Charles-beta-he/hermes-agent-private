from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import kanban_db as kb


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


def test_control_tower_dashboard_plugin_decides_approval_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    proposed = client.post(
        "/api/plugins/control-tower/commands/propose",
        json={
            "command_type": "ReleaseLease",
            "actor_id": "charles",
            "target_type": "task",
            "target_id": "task:danger",
            "risk_level": "L2",
            "payload": {"lease_owner": "worker-1"},
            "idempotency_key": "dashboard-release-danger",
        },
    ).json()

    response = client.post(
        f"/api/plugins/control-tower/approvals/{proposed['approval_id']}/decision",
        json={
            "decision": "approved",
            "actor_id": "charles",
            "reason": "operator confirmed stale lease evidence",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "approved"
    assert data["execution"] == "not_executed"
    assert data["source_surface"] == "dashboard"


def test_control_tower_dashboard_plugin_generates_executor_plan_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    proposed = client.post(
        "/api/plugins/control-tower/commands/propose",
        json={
            "command_type": "RefreshProjection",
            "actor_id": "charles",
            "target_type": "projection",
            "target_id": "overview",
            "risk_level": "L0",
            "payload": {"reason": "manual dry run"},
            "idempotency_key": "dashboard-refresh-plan",
        },
    ).json()

    response = client.post(
        f"/api/plugins/control-tower/commands/{proposed['command_id']}/executor-plan",
        json={
            "actor_id": "charles",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "planned"
    assert data["mode"] == "dry_run_copy_command_only"
    assert data["execution"] == "not_executed"
    assert data["writes_enabled"] is False
    assert data["source_surface"] == "dashboard"


def test_control_tower_dashboard_plugin_reviews_executor_policy_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    proposed = client.post(
        "/api/plugins/control-tower/commands/propose",
        json={
            "command_type": "RefreshProjection",
            "actor_id": "charles",
            "target_type": "projection",
            "target_id": "overview",
            "risk_level": "L0",
            "payload": {"reason": "manual policy preview"},
            "idempotency_key": "dashboard-refresh-policy-review",
        },
    ).json()
    plan = client.post(
        f"/api/plugins/control-tower/commands/{proposed['command_id']}/executor-plan",
        json={"actor_id": "charles"},
    ).json()

    response = client.post(
        f"/api/plugins/control-tower/executor-plans/{plan['plan_id']}/policy-review",
        json={"actor_id": "charles"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["policy_status"] == "read_only_eligible"
    assert data["allowed_to_execute"] is True
    assert data["writes_enabled"] is False
    assert data["execution"] == "not_executed"
    assert data["blocked_reason"] == "read_only_executor_whitelisted"
    assert data["source_surface"] == "dashboard"


def test_control_tower_dashboard_plugin_records_evidence_and_reviews_gate_without_closeout(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    evidence_response = client.post(
        "/api/plugins/control-tower/evidence",
        json={
            "task_id": "task:dashboard-evidence",
            "evidence_type": "proof",
            "maturity": "verified",
            "source_type": "pytest",
            "summary": "Dashboard submitted verified proof",
            "producer_type": "human",
            "producer_id": "charles",
            "acceptance_refs": ["AC-1"],
        },
    )
    review_response = client.post(
        "/api/plugins/control-tower/evidence-gates/evaluate",
        json={
            "task_id": "task:dashboard-evidence",
            "risk_level": "L2",
            "acceptance_refs": ["AC-1"],
            "actor_id": "charles",
        },
    )

    assert evidence_response.status_code == 200, evidence_response.text
    assert evidence_response.json()["execution"] == "not_executed"
    assert review_response.status_code == 200, review_response.text
    data = review_response.json()
    assert data["evidence_status"] == "satisfied"
    assert data["required_maturity"] == "verified"
    assert data["closeout_allowed"] is False
    assert data["blocked_reason"] == "closeout_requires_human_boundary"
    assert data["source_surface"] == "dashboard"


def test_control_tower_dashboard_plugin_requests_and_decides_conflicting_evidence_override_without_unblocking_closeout(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    for payload in [
        {
            "task_id": "task:dashboard-conflict",
            "evidence_type": "proof",
            "maturity": "verified",
            "source_type": "pytest",
            "summary": "Dashboard verified proof",
            "producer_type": "human",
            "producer_id": "charles",
            "acceptance_refs": ["AC-1"],
        },
        {
            "task_id": "task:dashboard-conflict",
            "evidence_type": "counterexample",
            "maturity": "verified",
            "source_type": "reviewer",
            "summary": "Dashboard conflicting signal",
            "producer_type": "human",
            "producer_id": "reviewer",
            "acceptance_refs": ["AC-1"],
            "stale_status": "conflicting",
        },
    ]:
        response = client.post("/api/plugins/control-tower/evidence", json=payload)
        assert response.status_code == 200, response.text

    review = client.post(
        "/api/plugins/control-tower/evidence-gates/evaluate",
        json={
            "task_id": "task:dashboard-conflict",
            "risk_level": "L2",
            "acceptance_refs": ["AC-1"],
            "actor_id": "charles",
        },
    ).json()
    omitted_role_request = client.post(
        f"/api/plugins/control-tower/evidence-gates/{review['evidence_review_id']}/conflicting-evidence-override",
        json={
            "actor_id": "charles",
            "reason": "missing role must not become admin",
        },
    )
    requested = client.post(
        f"/api/plugins/control-tower/evidence-gates/{review['evidence_review_id']}/conflicting-evidence-override",
        json={
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "operator accepts documented conflict risk",
        },
    )

    assert omitted_role_request.status_code == 422, omitted_role_request.text
    assert requested.status_code == 200, requested.text
    data = requested.json()
    assert data["status"] == "requested"
    assert data["execution"] == "not_executed"
    assert data["override_allowed"] is False
    assert data["closeout_allowed"] is False
    omitted_role_decision = client.post(
        f"/api/plugins/control-tower/conflicting-evidence-overrides/{data['conflict_override_authorization_id']}/decision",
        json={
            "decision": "approved",
            "actor_id": "charles",
            "reason": "missing role must not become admin",
        },
    )
    decided = client.post(
        f"/api/plugins/control-tower/conflicting-evidence-overrides/{data['conflict_override_authorization_id']}/decision",
        json={
            "decision": "approved",
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "projection only",
        },
    )
    snapshot = client.get("/api/plugins/control-tower/snapshot")

    assert omitted_role_decision.status_code == 422, omitted_role_decision.text
    assert decided.status_code == 200, decided.text
    decided_data = decided.json()
    assert decided_data["status"] == "approved"
    assert decided_data["execution"] == "not_executed"
    assert decided_data["override_allowed"] is False
    assert decided_data["closeout_allowed"] is False
    snapshot_data = snapshot.json()["conflicting_evidence_override_authorizations"]
    assert snapshot_data["mode"] == "conflicting_evidence_override_projection_only"
    assert snapshot_data["override_consumption_enabled"] is False
    assert snapshot_data["closeout_execution_enabled"] is False
    assert snapshot_data["decided_count"] == 1


def test_control_tower_dashboard_plugin_requests_and_decides_closeout_authorization_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    client.post(
        "/api/plugins/control-tower/evidence",
        json={
            "task_id": "task:dashboard-closeout",
            "evidence_type": "proof",
            "maturity": "verified",
            "source_type": "pytest",
            "summary": "Dashboard submitted verified proof",
            "producer_type": "human",
            "producer_id": "charles",
            "acceptance_refs": ["AC-1"],
        },
    )
    review = client.post(
        "/api/plugins/control-tower/evidence-gates/evaluate",
        json={
            "task_id": "task:dashboard-closeout",
            "risk_level": "L2",
            "acceptance_refs": ["AC-1"],
            "actor_id": "charles",
        },
    ).json()

    request_response = client.post(
        f"/api/plugins/control-tower/evidence-gates/{review['evidence_review_id']}/closeout-authorization",
        json={"actor_id": "charles", "actor_role": "owner", "reason": "review evidence digest"},
    )

    assert request_response.status_code == 200, request_response.text
    requested = request_response.json()
    assert requested["status"] == "requested"
    assert requested["required_role"] == "owner"
    assert requested["closeout_allowed"] is False
    assert requested["execution"] == "not_executed"

    decision_response = client.post(
        f"/api/plugins/control-tower/closeout-authorizations/{requested['closeout_authorization_id']}/decision",
        json={
            "decision": "approved",
            "actor_id": "charles",
            "actor_role": "owner",
            "reason": "human approved evidence-bound closeout but execution remains disabled",
        },
    )

    assert decision_response.status_code == 200, decision_response.text
    decided = decision_response.json()
    assert decided["status"] == "approved"
    assert decided["closeout_allowed"] is False
    assert decided["execution"] == "not_executed"

def test_control_tower_dashboard_plugin_executes_read_only_executor_without_adapter_writes(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)

    proposed = client.post(
        "/api/plugins/control-tower/commands/propose",
        json={
            "command_type": "RunReadOnlySelfCheck",
            "actor_id": "charles",
            "target_type": "system",
            "target_id": "control-tower",
            "risk_level": "L1",
            "payload": {},
            "idempotency_key": "dashboard-read-only-execute",
        },
    ).json()
    plan = client.post(
        f"/api/plugins/control-tower/commands/{proposed['command_id']}/executor-plan",
        json={"actor_id": "charles"},
    ).json()
    client.post(
        f"/api/plugins/control-tower/executor-plans/{plan['plan_id']}/policy-review",
        json={"actor_id": "charles"},
    )

    response = client.post(
        f"/api/plugins/control-tower/executor-plans/{plan['plan_id']}/execute-read-only",
        json={"actor_id": "charles"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["executor_status"] == "read_only_completed"
    assert data["writes_enabled"] is False
    assert data["adapter_writes"] == []
    assert data["result"]["check"] == "self_check"
    assert data["source_surface"] == "dashboard"


def test_control_tower_dashboard_plugin_executes_closeout_to_control_tower_ledger_only(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)
    task_id = "task:dashboard-execute-closeout"

    client.post(
        "/api/plugins/control-tower/evidence",
        json={
            "task_id": task_id,
            "evidence_type": "proof",
            "maturity": "verified",
            "source_type": "pytest",
            "summary": "Dashboard submitted verified proof",
            "producer_type": "human",
            "producer_id": "charles",
            "acceptance_refs": ["AC-1"],
        },
    )
    review = client.post(
        "/api/plugins/control-tower/evidence-gates/evaluate",
        json={"task_id": task_id, "risk_level": "L2", "acceptance_refs": ["AC-1"], "actor_id": "charles"},
    ).json()
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            """
            INSERT INTO tasks(task_id, title, status, risk_class, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, "Dashboard closeout target", "review", "L2", "2026-06-07T00:00:00+00:00", "2026-06-07T00:00:00+00:00"),
        )
        ledger.commit()
    requested = client.post(
        f"/api/plugins/control-tower/evidence-gates/{review['evidence_review_id']}/closeout-authorization",
        json={"actor_id": "charles", "actor_role": "owner"},
    ).json()
    decided = client.post(
        f"/api/plugins/control-tower/closeout-authorizations/{requested['closeout_authorization_id']}/decision",
        json={"decision": "approved", "actor_id": "charles", "actor_role": "owner"},
    ).json()

    response = client.post(
        f"/api/plugins/control-tower/closeout-authorizations/{decided['closeout_authorization_id']}/execute",
        json={"actor_id": "charles", "actor_role": "owner", "reason": "execute approved closeout"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["execution_status"] == "closeout_completed"
    assert data["task_status"] == "closed"
    assert data["adapter_writes"] == []
    assert data["external_adapter_write_through_enabled"] is False
    assert data["source_surface"] == "dashboard"
    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[0] == "closed"
        assert ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0] == 0


def test_control_tower_dashboard_plugin_executes_digest_bound_kanban_external_write(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/control-tower")
    client = TestClient(app)
    task_id = "task:dashboard-external-intent"

    client.post(
        "/api/plugins/control-tower/evidence",
        json={
            "task_id": task_id,
            "evidence_type": "proof",
            "maturity": "verified",
            "source_type": "pytest",
            "summary": "Dashboard submitted verified proof",
            "producer_type": "human",
            "producer_id": "charles",
            "acceptance_refs": ["AC-1"],
        },
    )
    review = client.post(
        "/api/plugins/control-tower/evidence-gates/evaluate",
        json={"task_id": task_id, "risk_level": "L2", "acceptance_refs": ["AC-1"], "actor_id": "charles"},
    ).json()
    kb.init_db()
    kanban_conn = kb.connect()
    try:
        kanban_task_id = kb.create_task(
            kanban_conn,
            title="Dashboard external write-through target",
            assignee="default",
        )
    finally:
        kanban_conn.close()
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            """
            INSERT INTO tasks(task_id, external_ref, title, status, risk_class, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                kanban_task_id,
                "Dashboard external intent target",
                "review",
                "L2",
                "2026-06-07T00:00:00+00:00",
                "2026-06-07T00:00:00+00:00",
            ),
        )
        ledger.commit()
    requested = client.post(
        f"/api/plugins/control-tower/evidence-gates/{review['evidence_review_id']}/closeout-authorization",
        json={"actor_id": "charles", "actor_role": "owner"},
    ).json()
    decided = client.post(
        f"/api/plugins/control-tower/closeout-authorizations/{requested['closeout_authorization_id']}/decision",
        json={"decision": "approved", "actor_id": "charles", "actor_role": "owner"},
    ).json()
    execution = client.post(
        f"/api/plugins/control-tower/closeout-authorizations/{decided['closeout_authorization_id']}/execute",
        json={"actor_id": "charles", "actor_role": "owner"},
    ).json()

    response = client.post(
        f"/api/plugins/control-tower/closeout-executions/{execution['closeout_execution_id']}/external-write-intent",
        json={
            "adapter_name": "kanban",
            "external_ref": kanban_task_id,
            "actor_id": "charles",
            "actor_role": "owner",
            "reason": "prepare intent only",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["intent_status"] == "blocked_pending_adapter_contract"
    assert data["writes_enabled"] is False
    assert data["external_adapter_write_through_enabled"] is False
    assert data["rollback_required"] is True
    assert data["rollback_available"] is False
    assert data["source_surface"] == "dashboard"
    review_response = client.post(
        f"/api/plugins/control-tower/external-write-intents/{data['external_write_intent_id']}/review",
        json={
            "actor_id": "charles",
            "actor_role": "owner",
            "reason": "review readiness only",
        },
    )
    assert review_response.status_code == 200, review_response.text
    review = review_response.json()
    assert review["review_status"] == "blocked"
    assert review["write_through_allowed"] is False
    assert review["external_adapter_write_through_enabled"] is False
    assert "rollback_unavailable" in review["blocked_reasons"]
    auth_response = client.post(
        f"/api/plugins/control-tower/external-write-reviews/{review['external_write_review_id']}/authorization",
        json={
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "request external write auth only",
        },
    )
    assert auth_response.status_code == 200, auth_response.text
    auth = auth_response.json()
    assert auth["status"] == "requested"
    assert auth["write_through_allowed"] is False
    assert auth["external_adapter_write_through_enabled"] is False
    decision_response = client.post(
        f"/api/plugins/control-tower/external-write-authorizations/{auth['external_write_authorization_id']}/decision",
        json={
            "decision": "approved",
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "approve authorization record only",
        },
    )
    assert decision_response.status_code == 200, decision_response.text
    decision = decision_response.json()
    assert decision["status"] == "approved"
    assert decision["write_through_allowed"] is False
    assert decision["writes_enabled"] is False
    preflight_response = client.post(
        f"/api/plugins/control-tower/external-write-authorizations/{decision['external_write_authorization_id']}/execution-preflight",
        json={
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "prepare command digest only",
        },
    )
    assert preflight_response.status_code == 200, preflight_response.text
    preflight = preflight_response.json()
    assert preflight["preflight_status"] == "blocked_pending_external_write_executor"
    assert preflight["command_execution_digest"]
    assert preflight["write_through_allowed"] is False
    assert preflight["external_adapter_write_through_enabled"] is False
    assert preflight["external_write_executor_enabled"] is False
    execute_response = client.post(
        f"/api/plugins/control-tower/external-write-preflights/{preflight['external_write_preflight_id']}/execute",
        json={
            "expected_command_execution_digest": preflight["command_execution_digest"],
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "execute approved kanban write-through",
        },
    )
    assert execute_response.status_code == 200, execute_response.text
    write_run = execute_response.json()
    assert write_run["execution_status"] == "write_completed"
    assert write_run["adapter_name"] == "kanban"
    assert write_run["external_ref"] == kanban_task_id
    assert write_run["write_through_allowed"] is True
    assert write_run["external_write_executor_enabled"] is True
    assert write_run["rollback_available"] is True
    assert write_run["rollback_reopen_enabled"] is True
    kanban_conn = kb.connect()
    try:
        kanban_task = kb.get_task(kanban_conn, kanban_task_id)
    finally:
        kanban_conn.close()
    assert kanban_task is not None
    assert kanban_task.status == "done"
    rollback_response = client.post(
        f"/api/plugins/control-tower/external-write-runs/{write_run['external_write_execution_id']}/rollback",
        json={
            "expected_command_execution_digest": write_run["command_execution_digest"],
            "actor_id": "charles",
            "actor_role": "admin",
            "reason": "rollback approved kanban write-through",
        },
    )
    assert rollback_response.status_code == 200, rollback_response.text
    rollback = rollback_response.json()
    assert rollback["rollback_status"] == "rollback_completed"
    assert rollback["adapter_name"] == "kanban"
    assert rollback["operation"] == "reopen"
    assert rollback["rollback_reopen_enabled"] is True
    kanban_conn = kb.connect()
    try:
        kanban_task = kb.get_task(kanban_conn, kanban_task_id)
    finally:
        kanban_conn.close()
    assert kanban_task is not None
    assert kanban_task.status == "ready"
    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_intents").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_intent_reviews").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_authorizations").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_execution_preflights").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_execution_runs").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_rollback_runs").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0] == 2
