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
    assert data["ledger"]["canonical_fact_source"] == "brain_taskpool_markdown"
    assert data["ledger"]["control_tower_role"] == "read_only_governance_projection"
    assert data["ssot_boundary"]["canonical"] == "brain_taskpool_markdown"
    assert data["ssot_boundary"]["rule"] == "dashboard_console_must_not_advance_task_lifecycle"
    assert data["ui_host"] == {
        "selected": "dashboard_plugin",
        "plugin_name": "control-tower",
        "canonical": False,
        "role": "operator_projection_surface",
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


def test_control_tower_dashboard_renders_mission_flow_operator_panel():
    repo_root = Path(__file__).resolve().parents[2]
    index_js = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "index.js"
    style_css = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "style.css"

    script = index_js.read_text(encoding="utf-8")
    style = style_css.read_text(encoding="utf-8")

    assert "control-tower-mission-flow" in script
    assert "Mission event flow" in script
    assert "renderMissionFlow(data)" in script
    assert "mission_flow_signals" in script
    assert "mission_taskpool_bridge" in script
    assert "Pending signals:" in script
    assert "Taskpool bridge" in script
    assert "control-tower-mission-flow-card" in style


def test_control_tower_dashboard_renders_operator_overview_as_first_screen():
    repo_root = Path(__file__).resolve().parents[2]
    index_js = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "index.js"
    style_css = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "style.css"

    script = index_js.read_text(encoding="utf-8")
    style = style_css.read_text(encoding="utf-8")

    assert "control-tower-operator-overview" in script
    assert "Operator Overview" in script
    assert "renderOperatorOverview(data)" in script
    assert "operator_overview" in script
    assert "Recommended next action" in script
    assert "Critical alerts" in script
    assert "Next safe actions" in script
    assert "next_safe_actions" in script
    assert "Runtime taskpool" in script
    assert "taskpool_runtime" in script
    assert "Projection governance" in script
    assert "projection_governance" in script
    assert "Brain taskpool markdown is the task-lifecycle SSOT" in script
    assert "reports projection drift instead of advancing lifecycle state" in script
    assert "escapeHtml(taskpoolRuntime.next_task_id)" in script
    assert "control-tower-next-actions" in style
    assert "control-tower-operator-overview-card" in style


def test_control_tower_dashboard_renders_workflow_kernel_projection_panel():
    repo_root = Path(__file__).resolve().parents[2]
    index_js = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "index.js"
    style_css = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "style.css"

    script = index_js.read_text(encoding="utf-8")
    style = style_css.read_text(encoding="utf-8")

    assert "control-tower-workflow-kernel" in script
    assert "Workflow Kernel" in script
    assert "renderWorkflowKernel(data)" in script
    assert "workflow_kernel_control_tower" in script
    assert "unattended_dry_run_scheduler" in script
    assert "policy_editor" in script
    assert "data-workflow-kernel-decision" in script
    assert "Approve dry-run only" in script
    assert "workflow-kernel/policy-editor/decision" in script
    assert "dry_run_cron_rollout" in script
    assert "guarded_send_runner_activation" in script
    assert "Dry-run cron rollout" in script
    assert "Guarded send activation" in script
    assert "guarded_manual_send_runner" in script
    assert "one_shot_guarded_send" in script
    assert "Manual-send runner" in script
    assert "One-shot guarded send" in script
    assert "post_send_correlation_watch" in script
    assert "unattended_dispatch_enablement_packet" in script
    assert "Post-send correlation" in script
    assert "Enablement packet" in script
    assert "unattended_decision_record" in script
    assert "manual_only_closure" in script
    assert "Decision record" in script
    assert "Manual-only closure" in script
    assert "manual_only_monitoring" in script
    assert "future_unattended_reopen_packet" in script
    assert "Manual-only monitoring" in script
    assert "Future reopen packet" in script
    assert "single_point_reopen_choice" in script
    assert "manual_only_governance_archive" in script
    assert "Single-point reopen choice" in script
    assert "Governance archive" in script
    assert "periodic_closure_health_report" in script
    assert "archive_publication_packet" in script
    assert "Closure health report" in script
    assert "Archive publication packet" in script
    assert "private_archive_retention_watch" in script
    assert "publication_exact_target_gate" in script
    assert "Private archive retention" in script
    assert "Publication exact-target gate" in script
    assert "exact_target_apply_dry_run_compiler" in script
    assert "publication_apply_authorization_packet" in script
    assert "Exact-target dry-run compiler" in script
    assert "Publication authorization packet" in script
    assert "private_export_artifact_compiler" in script
    assert "publication_apply_implementation_choice" in script
    assert "Private export artifact" in script
    assert "Publication apply choice" in script
    assert "private_export_retention_watch" in script
    assert "publication_apply_reopen_packet" in script
    assert "Private export retention" in script
    assert "Publication apply reopen packet" in script
    assert "publication_closure_health_report" in script
    assert "publication_governance_archive" in script
    assert "Publication closure health" in script
    assert "Publication governance archive" in script
    assert "Pack, guarded send, and dispatch policy" in script
    assert "Latest guarded send" not in script  # keep wording in static Phase52 page only
    assert "control-tower-workflow-kernel-card" in style


def test_control_tower_dashboard_keeps_debug_actions_and_raw_snapshot_collapsed():
    repo_root = Path(__file__).resolve().parents[2]
    index_js = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "index.js"
    style_css = repo_root / "plugins" / "control-tower" / "dashboard" / "dist" / "style.css"

    script = index_js.read_text(encoding="utf-8")
    style = style_css.read_text(encoding="utf-8")

    assert "control-tower-primary-actions" in script
    assert "control-tower-debug-actions" in script
    assert "Operator/debug actions" in script
    assert "control-tower-debug-snapshot" in script
    assert "Raw snapshot JSON" in script
    assert "Record sample evidence" in script
    assert "control-tower-debug-actions" in style
    assert "control-tower-debug-snapshot" in style


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
