from __future__ import annotations

import sqlite3
from pathlib import Path

from hermes_cli import control_tower
from hermes_cli import kanban_db as kb


def test_init_ledger_creates_single_fact_source_schema(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    path = control_tower.init_ledger()

    assert path == home / "control_tower.db"
    assert path.exists()
    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "ledger_meta",
            "commands",
            "events",
            "tasks",
            "workflow_runs",
            "workflow_steps",
            "worker_runs",
            "evidence",
            "policy_decisions",
            "human_approvals",
            "executor_plans",
            "execution_policy_reviews",
            "evidence_gate_reviews",
            "closeout_authorizations",
            "adapter_observations",
            "projection_checkpoints",
        }.issubset(tables)
        meta = dict(conn.execute("SELECT key, value FROM ledger_meta"))
    assert meta["schema_version"] == "1"
    assert meta["active_profile"] == "default"
    assert meta["ledger_id"]


def test_control_tower_snapshot_is_read_only_and_labels_adapters(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    snapshot = control_tower.build_snapshot()

    assert snapshot["schema_version"] == 1
    assert snapshot["ledger"]["path"] == str(home / "control_tower.db")
    assert snapshot["ledger"]["canonical_fact_source"] == "Control Tower Ledger"
    assert snapshot["ledger"]["mode"] == "read_only_projection"
    assert snapshot["taskpool_health"] == {
        "active": 0,
        "ready": 0,
        "blocked": 0,
        "claimed": 0,
        "running": 0,
        "review": 0,
        "stale_leases": 0,
    }
    assert snapshot["next_dispatchable"] is None
    assert snapshot["top_blockers"] == []
    assert snapshot["adapter_conflicts"] == []
    assert snapshot["ui_host"]["selected"] == "dashboard_plugin"
    assert snapshot["ui_host"]["plugin_name"] == "control-tower"
    assert "kanban" in snapshot["adapters"]
    assert snapshot["adapters"]["kanban"]["role"] == "projection_adapter"


def test_record_adapter_observation_dedupes_by_hash(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    first = control_tower.record_adapter_observation(
        adapter_name="kanban",
        external_ref="task:1",
        observed_type="task",
        payload={"id": 1, "status": "ready"},
    )
    second = control_tower.record_adapter_observation(
        adapter_name="kanban",
        external_ref="task:1",
        observed_type="task",
        payload={"status": "ready", "id": 1},
    )

    assert first["created"] is True
    assert second["created"] is False
    with sqlite3.connect(home / "control_tower.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 1
    assert event_count == 1


def test_snapshot_observes_kanban_as_projection_adapter(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    kb.init_db()
    conn = kb.connect()
    try:
        task_id = kb.create_task(
            conn,
            title="Ship Control Tower read-only slice",
            assignee="default",
            priority=7,
        )
    finally:
        conn.close()

    snapshot = control_tower.build_snapshot()

    assert snapshot["adapters"]["kanban"]["available"] is True
    assert snapshot["adapters"]["kanban"]["canonical"] is False
    assert snapshot["adapters"]["kanban"]["observed_tasks"] == 1
    assert snapshot["taskpool_health"]["active"] == 1
    assert snapshot["taskpool_health"]["ready"] == 1
    assert snapshot["next_dispatchable"] == {
        "adapter": "kanban",
        "external_ref": f"task:{task_id}",
        "title": "Ship Control Tower read-only slice",
        "priority": 7,
        "assignee": "default",
        "gate": "read_only_observed_ready",
    }
    with sqlite3.connect(home / "control_tower.db") as ledger:
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events")]
    assert event_types == ["TaskObservedFromAdapter"]


def test_propose_command_records_command_and_audit_event_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    proposed = control_tower.propose_command(
        command_type="RunProofCheck",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:abc",
        risk_level="L1",
        payload={"reason": "operator requested proof check"},
        idempotency_key="proof-check-task-abc",
    )

    assert proposed["status"] == "proposed"
    assert proposed["execution"] == "not_executed"
    assert proposed["requires_human"] is False
    assert proposed["command_id"].startswith("cmd_")
    with sqlite3.connect(home / "control_tower.db") as ledger:
        command_row = ledger.execute(
            "SELECT command_type, status, risk_level, idempotency_key FROM commands"
        ).fetchone()
        event_row = ledger.execute(
            "SELECT event_type, aggregate_type, aggregate_id, command_id FROM events"
        ).fetchone()
    assert command_row == ("RunProofCheck", "proposed", "L1", "proof-check-task-abc")
    assert event_row == (
        "CommandProposed",
        "command",
        proposed["command_id"],
        proposed["command_id"],
    )


def test_propose_command_dedupes_by_idempotency_key(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    first = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-overview",
    )
    second = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-overview",
    )

    assert second["created"] is False
    assert second["command_id"] == first["command_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_high_risk_proposed_command_creates_human_approval_request(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    proposed = control_tower.propose_command(
        command_type="ReleaseLease",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:danger",
        risk_level="L2",
        payload={"lease_owner": "worker-1"},
        idempotency_key="release-task-danger",
    )

    assert proposed["status"] == "proposed"
    assert proposed["execution"] == "not_executed"
    assert proposed["requires_human"] is True
    assert proposed["approval_id"].startswith("appr_")
    with sqlite3.connect(home / "control_tower.db") as ledger:
        approval = ledger.execute(
            "SELECT approval_id, command_id, requested_action, risk_level, status FROM human_approvals"
        ).fetchone()
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert approval == (
        proposed["approval_id"],
        proposed["command_id"],
        "ReleaseLease",
        "L2",
        "requested",
    )
    assert event_types == ["CommandProposed", "HumanApprovalRequested"]


def test_snapshot_includes_proposed_commands_and_pending_human_approvals(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    low = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-overview",
    )
    high = control_tower.propose_command(
        command_type="CancelWorkflowRun",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="workflow_run",
        target_id="run-1",
        risk_level="L3",
        payload={},
        idempotency_key="cancel-run-1",
    )

    snapshot = control_tower.build_snapshot()

    assert snapshot["command_registry"]["proposed_count"] == 2
    assert [cmd["command_id"] for cmd in snapshot["command_registry"]["recent_proposed"]] == [
        low["command_id"],
        high["command_id"],
    ]
    assert snapshot["human_inbox"]["pending_count"] == 1
    assert snapshot["human_inbox"]["pending"][0]["approval_id"] == high["approval_id"]


def test_decide_human_approval_approves_once_without_executing_command(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="ReleaseLease",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:danger",
        risk_level="L2",
        payload={"lease_owner": "worker-1"},
        idempotency_key="release-task-danger",
    )

    decided = control_tower.decide_human_approval(
        approval_id=proposed["approval_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="operator reviewed stale lease evidence",
    )
    repeated = control_tower.decide_human_approval(
        approval_id=proposed["approval_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="same approval submitted twice",
    )

    assert decided["created"] is True
    assert decided["approval_id"] == proposed["approval_id"]
    assert decided["status"] == "approved"
    assert decided["execution"] == "not_executed"
    assert repeated["created"] is False
    assert repeated["status"] == "approved"
    with sqlite3.connect(home / "control_tower.db") as ledger:
        approval = ledger.execute(
            "SELECT status, decided_by, decided_at FROM human_approvals WHERE approval_id = ?",
            (proposed["approval_id"],),
        ).fetchone()
        command_status = ledger.execute(
            "SELECT status FROM commands WHERE command_id = ?",
            (proposed["command_id"],),
        ).fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert approval[0] == "approved"
    assert approval[1] == "charles"
    assert approval[2]
    assert command_status == "proposed"
    assert event_types == ["CommandProposed", "HumanApprovalRequested", "HumanApprovalGranted"]


def test_decide_human_approval_rejects_and_snapshot_moves_out_of_pending(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="CancelWorkflowRun",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="workflow_run",
        target_id="run-1",
        risk_level="L3",
        payload={},
        idempotency_key="cancel-run-1",
    )

    decided = control_tower.decide_human_approval(
        approval_id=proposed["approval_id"],
        decision="rejected",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="insufficient rollback evidence",
    )
    snapshot = control_tower.build_snapshot()

    assert decided["status"] == "rejected"
    assert decided["execution"] == "not_executed"
    assert snapshot["human_inbox"]["pending_count"] == 0
    assert snapshot["human_inbox"]["pending"] == []
    assert snapshot["human_inbox"]["decided_count"] == 1
    assert snapshot["human_inbox"]["recent_decided"][0]["approval_id"] == proposed["approval_id"]
    assert snapshot["human_inbox"]["recent_decided"][0]["status"] == "rejected"


def test_validate_workflow_schema_rejects_duplicates_missing_dependencies_and_cycles():
    duplicate = {
        "schema_id": "bad-duplicate",
        "version": 1,
        "nodes": [
            {"id": "a", "requires": []},
            {"id": "a", "requires": []},
        ],
    }
    missing = {
        "schema_id": "bad-missing",
        "version": 1,
        "nodes": [{"id": "a", "requires": ["missing"]}],
    }
    cyclic = {
        "schema_id": "bad-cycle",
        "version": 1,
        "nodes": [
            {"id": "a", "requires": ["b"]},
            {"id": "b", "requires": ["a"]},
        ],
    }

    for schema, expected in [
        (duplicate, "duplicate workflow node id"),
        (missing, "missing dependency"),
        (cyclic, "workflow cycle detected"),
    ]:
        try:
            control_tower.validate_workflow_schema(schema)
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError(f"expected {expected}")


def test_snapshot_includes_ledger_backed_workflow_map_without_executor_ready(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="ReleaseLease",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:danger",
        risk_level="L2",
        payload={"lease_owner": "worker-1"},
        idempotency_key="release-task-danger",
    )

    snapshot = control_tower.build_snapshot()

    assert snapshot["workflow_map"]["schema_id"] == "control-tower-command-lifecycle"
    assert snapshot["workflow_map"]["source"] == "ledger_projection"
    assert [node["id"] for node in snapshot["workflow_map"]["nodes"]] == [
        "command_proposed",
        "approval_requested",
        "approval_decided",
        "executor_eligible",
    ]
    assert snapshot["workflow_map"]["ready"] == [
        {
            "node_id": "approval_requested",
            "approval_id": proposed["approval_id"],
            "command_id": proposed["command_id"],
            "action": "ReleaseLease",
            "risk_level": "L2",
        }
    ]
    assert snapshot["workflow_map"]["blocked"] == [
        {
            "node_id": "executor_eligible",
            "reason": "executor_disabled_phase_boundary",
            "required_human_decisions": 1,
        }
    ]

    control_tower.decide_human_approval(
        approval_id=proposed["approval_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="operator approved for future executor phase",
    )
    decided_snapshot = control_tower.build_snapshot()

    assert decided_snapshot["workflow_map"]["ready"] == []
    assert decided_snapshot["workflow_map"]["blocked"] == [
        {
            "node_id": "executor_eligible",
            "reason": "executor_disabled_phase_boundary",
            "approved_commands_waiting": 1,
        }
    ]


def test_generate_executor_plan_for_approved_command_is_dry_run_and_idempotent(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="ReleaseLease",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:danger",
        risk_level="L2",
        payload={"lease_owner": "worker-1"},
        idempotency_key="release-task-danger",
    )
    control_tower.decide_human_approval(
        approval_id=proposed["approval_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="operator approved dry-run planning only",
    )

    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    repeated = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert plan["created"] is True
    assert plan["status"] == "planned"
    assert plan["mode"] == "dry_run_copy_command_only"
    assert plan["execution"] == "not_executed"
    assert plan["writes_enabled"] is False
    assert plan["copy_command"] == f"hermes control-tower executor-plan --command-id {proposed['command_id']} --dry-run"
    assert repeated["created"] is False
    assert repeated["plan_id"] == plan["plan_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        command_status = ledger.execute(
            "SELECT status FROM commands WHERE command_id = ?",
            (proposed["command_id"],),
        ).fetchone()[0]
        plan_rows = ledger.execute("SELECT COUNT(*) FROM executor_plans").fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert command_status == "proposed"
    assert plan_rows == 1
    assert event_types == [
        "CommandProposed",
        "HumanApprovalRequested",
        "HumanApprovalGranted",
        "ExecutorPlanGenerated",
    ]


def test_existing_executor_plan_revalidates_command_eligibility(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-existing-plan",
    )
    control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            "UPDATE commands SET status = 'executed' WHERE command_id = ?",
            (proposed["command_id"],),
        )
        ledger.commit()

    try:
        control_tower.generate_executor_plan(
            command_id=proposed["command_id"],
            actor_type="human",
            actor_id="charles",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "command is not proposed" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expected stale existing plan to fail closed")


def test_generate_executor_plan_fails_closed_for_pending_rejected_and_unapproved_high_risk(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    pending = control_tower.propose_command(
        command_type="ReleaseLease",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:pending",
        risk_level="L2",
        payload={},
        idempotency_key="release-task-pending",
    )
    rejected = control_tower.propose_command(
        command_type="CancelWorkflowRun",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="workflow_run",
        target_id="run-rejected",
        risk_level="L3",
        payload={},
        idempotency_key="cancel-run-rejected",
    )
    control_tower.decide_human_approval(
        approval_id=rejected["approval_id"],
        decision="rejected",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="insufficient evidence",
    )

    for command_id, expected in [
        (pending["command_id"], "requires approved human approval"),
        (rejected["command_id"], "approval rejected"),
    ]:
        try:
            control_tower.generate_executor_plan(
                command_id=command_id,
                actor_type="human",
                actor_id="charles",
                source_surface="dashboard",
            )
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError(f"expected {expected}")


def test_snapshot_includes_executor_dry_run_plan_and_no_write_boundary(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={"reason": "operator requested copy command"},
        idempotency_key="refresh-overview-plan",
    )

    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    snapshot = control_tower.build_snapshot()

    assert snapshot["executor_plans"]["mode"] == "dry_run_copy_command_only"
    assert snapshot["executor_plans"]["writes_enabled"] is False
    assert snapshot["executor_plans"]["planned_count"] == 1
    assert snapshot["executor_plans"]["recent_plans"] == [
        {
            "plan_id": plan["plan_id"],
            "command_id": proposed["command_id"],
            "command_type": "RefreshProjection",
            "mode": "dry_run_copy_command_only",
            "status": "planned",
            "execution": "not_executed",
            "copy_command": plan["copy_command"],
        }
    ]


def test_review_executor_plan_records_policy_projection_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={"reason": "policy preview"},
        idempotency_key="refresh-policy-review",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    review = control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    repeated = control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert review["created"] is True
    assert review["plan_id"] == plan["plan_id"]
    assert review["policy_status"] == "read_only_eligible"
    assert review["execution"] == "not_executed"
    assert review["writes_enabled"] is False
    assert review["allowed_to_execute"] is True
    assert review["blocked_reason"] == "read_only_executor_whitelisted"
    assert review["rollback_preview"] == {
        "available": False,
        "reason": "read-only executor action has no lifecycle mutation to roll back",
    }
    assert review["adapter_writes"] == []
    assert repeated["created"] is False
    assert repeated["policy_review_id"] == review["policy_review_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        command_status = ledger.execute(
            "SELECT status FROM commands WHERE command_id = ?",
            (proposed["command_id"],),
        ).fetchone()[0]
        review_rows = ledger.execute("SELECT COUNT(*) FROM execution_policy_reviews").fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert command_status == "proposed"
    assert review_rows == 1
    assert event_types == [
        "CommandProposed",
        "ExecutorPlanGenerated",
        "ExecutionPolicyProjectionRecorded",
    ]


def test_review_executor_plan_policy_fails_closed_for_non_planned_or_missing_plan(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-non-planned-policy-review",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            "UPDATE executor_plans SET status = 'executed' WHERE plan_id = ?",
            (plan["plan_id"],),
        )
        ledger.commit()

    for plan_id, expected in [
        ("plan_missing", "executor plan not found"),
        (plan["plan_id"], "executor plan is not planned"),
    ]:
        try:
            control_tower.review_executor_plan_policy(
                plan_id=plan_id,
                actor_type="human",
                actor_id="charles",
                source_surface="dashboard",
            )
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError(f"expected {expected}")


def test_snapshot_includes_execution_policy_projection_without_write_authority(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-policy-snapshot",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    review = control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    snapshot = control_tower.build_snapshot()

    assert snapshot["execution_policy"]["writes_enabled"] is False
    assert snapshot["execution_policy"]["real_executor_enabled"] is False
    assert snapshot["execution_policy"]["review_count"] == 1
    assert snapshot["execution_policy"]["recent_reviews"] == [
        {
            "policy_review_id": review["policy_review_id"],
            "plan_id": plan["plan_id"],
            "command_id": proposed["command_id"],
            "policy_status": "read_only_eligible",
            "allowed_to_execute": True,
            "blocked_reason": "read_only_executor_whitelisted",
            "rollback_available": False,
        }
    ]


def test_record_evidence_validates_maturity_and_records_audit_event(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    evidence = control_tower.record_evidence(
        task_id="task:evidence",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Focused proof passed",
        producer_type="agent",
        producer_id="hermes",
        acceptance_refs=["AC-1"],
        artifact_uri="command:pytest tests/hermes_cli/test_control_tower_snapshot.py",
        artifact_hash="sha256:abc123",
    )

    assert evidence["status"] == "recorded"
    assert evidence["execution"] == "not_executed"
    assert evidence["evidence_id"].startswith("ev_")
    with sqlite3.connect(home / "control_tower.db") as ledger:
        row = ledger.execute(
            "SELECT task_id, evidence_type, maturity, stale_status FROM evidence"
        ).fetchone()
        event_type = ledger.execute("SELECT event_type FROM events").fetchone()[0]
    assert row == ("task:evidence", "proof", "verified", "fresh")
    assert event_type == "EvidenceRecorded"

    try:
        control_tower.record_evidence(
            task_id="task:evidence",
            evidence_type="proof",
            maturity="rumor",
            source_type="pytest",
            summary="invalid",
            producer_type="agent",
            producer_id="hermes",
        )
    except ValueError as exc:
        assert "invalid evidence maturity" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("invalid maturity should fail closed")


def test_evidence_gate_blocks_advisory_or_stale_evidence_without_closeout(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    control_tower.record_evidence(
        task_id="task:evidence-gate",
        evidence_type="claim",
        maturity="advisory",
        source_type="agent_summary",
        summary="Agent says it is done",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-1"],
    )
    control_tower.record_evidence(
        task_id="task:evidence-gate",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Old proof is stale",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-2"],
        stale_status="stale",
    )

    review = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-gate",
        risk_level="L2",
        acceptance_refs=["AC-1", "AC-2"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert review["evidence_status"] == "insufficient"
    assert review["required_maturity"] == "verified"
    assert review["satisfied_refs"] == []
    assert review["missing_refs"] == ["AC-1", "AC-2"]
    assert review["stale_or_conflicting_count"] == 1
    assert review["closeout_allowed"] is False
    assert review["execution"] == "not_executed"
    assert review["blocked_reason"] == "insufficient_verified_evidence"


def test_evidence_gate_marks_satisfied_but_still_requires_human_closeout_boundary(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    control_tower.record_evidence(
        task_id="task:evidence-satisfied",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Acceptance AC-1 verified",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-1"],
    )
    control_tower.record_evidence(
        task_id="task:evidence-satisfied",
        evidence_type="proof",
        maturity="certified",
        source_type="reviewer",
        summary="Acceptance AC-2 certified",
        producer_type="model_panel",
        producer_id="reviewer-quorum",
        acceptance_refs=["AC-2"],
    )

    review = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-satisfied",
        risk_level="L2",
        acceptance_refs=["AC-1", "AC-2"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    repeated = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-satisfied",
        risk_level="L2",
        acceptance_refs=["AC-1", "AC-2"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert review["created"] is True
    assert review["evidence_status"] == "satisfied"
    assert review["required_maturity"] == "verified"
    assert review["satisfied_refs"] == ["AC-1", "AC-2"]
    assert review["missing_refs"] == []
    assert review["closeout_allowed"] is False
    assert review["blocked_reason"] == "closeout_requires_human_boundary"
    assert repeated["created"] is False
    assert repeated["evidence_review_id"] == review["evidence_review_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        task_rows = ledger.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert task_rows == 0
    assert event_types == [
        "EvidenceRecorded",
        "EvidenceRecorded",
        "EvidenceGateEvaluated",
    ]


def test_evidence_gate_recomputes_when_evidence_state_changes(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    first = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-late",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    control_tower.record_evidence(
        task_id="task:evidence-late",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Late proof now satisfies AC-1",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-1"],
    )
    second = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-late",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert first["evidence_status"] == "insufficient"
    assert second["created"] is True
    assert second["evidence_status"] == "satisfied"
    assert second["missing_refs"] == []
    assert second["evidence_review_id"] != first["evidence_review_id"]


def test_evidence_gate_recomputes_when_conflicting_evidence_is_added(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    control_tower.record_evidence(
        task_id="task:evidence-conflict",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Initial proof",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-1"],
    )
    first = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-conflict",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    control_tower.record_evidence(
        task_id="task:evidence-conflict",
        evidence_type="counterexample",
        maturity="verified",
        source_type="reviewer",
        summary="Reviewer found conflicting signal",
        producer_type="human",
        producer_id="charles",
        acceptance_refs=["AC-1"],
        stale_status="conflicting",
    )
    second = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-conflict",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert first["evidence_status"] == "satisfied"
    assert second["created"] is True
    assert second["evidence_status"] == "satisfied"
    assert second["stale_or_conflicting_count"] == 1
    assert second["evidence_review_id"] != first["evidence_review_id"]


def test_snapshot_includes_evidence_policy_projection(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    control_tower.record_evidence(
        task_id="task:evidence-snapshot",
        evidence_type="proof",
        maturity="observed",
        source_type="smoke",
        summary="Observed proof",
        producer_type="agent",
        producer_id="hermes",
        acceptance_refs=["AC-1"],
    )
    review = control_tower.evaluate_evidence_gate(
        task_id="task:evidence-snapshot",
        risk_level="L1",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    snapshot = control_tower.build_snapshot()

    assert snapshot["evidence_summary"]["observed"] == 1
    assert snapshot["evidence_policy"]["closeout_execution_enabled"] is False
    assert snapshot["evidence_policy"]["review_count"] == 1
    assert snapshot["evidence_policy"]["recent_reviews"] == [
        {
            "evidence_review_id": review["evidence_review_id"],
            "task_id": "task:evidence-snapshot",
            "risk_level": "L1",
            "evidence_status": "satisfied",
            "required_maturity": "observed",
            "closeout_allowed": False,
            "blocked_reason": "closeout_requires_human_boundary",
        }
    ]


def _record_verified_closeout_evidence(task_id: str = "task:closeout") -> dict[str, object]:
    control_tower.record_evidence(
        task_id=task_id,
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Acceptance verified",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-1"],
    )
    return control_tower.evaluate_evidence_gate(
        task_id=task_id,
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )


def test_request_closeout_authorization_binds_satisfied_evidence_digest_without_writes(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    review = _record_verified_closeout_evidence()

    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="dashboard",
        reason="ready for human closeout review",
    )
    repeated = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="dashboard",
        reason="same request",
    )

    assert request["created"] is True
    assert request["status"] == "requested"
    assert request["evidence_review_id"] == review["evidence_review_id"]
    assert request["evidence_state_hash"]
    assert request["required_role"] == "owner"
    assert request["closeout_allowed"] is False
    assert request["execution"] == "not_executed"
    assert request["expires_at"] > request["requested_at"]
    assert repeated["created"] is False
    assert repeated["closeout_authorization_id"] == request["closeout_authorization_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        task_rows = ledger.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        auth_rows = ledger.execute("SELECT COUNT(*) FROM closeout_authorizations").fetchone()[0]
    assert task_rows == 0
    assert auth_rows == 1


def test_request_closeout_authorization_fails_closed_for_insufficient_or_conflicting_evidence(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    insufficient = control_tower.evaluate_evidence_gate(
        task_id="task:insufficient-closeout",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    try:
        control_tower.request_closeout_authorization(
            evidence_review_id=insufficient["evidence_review_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "evidence gate is not satisfied" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("insufficient evidence must not request closeout")

    control_tower.record_evidence(
        task_id="task:conflicting-closeout",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Verified but later conflicted",
        producer_type="agent",
        producer_id="worker-1",
        acceptance_refs=["AC-1"],
    )
    control_tower.record_evidence(
        task_id="task:conflicting-closeout",
        evidence_type="counterexample",
        maturity="verified",
        source_type="reviewer",
        summary="Conflicting evidence exists",
        producer_type="human",
        producer_id="charles",
        acceptance_refs=["AC-1"],
        stale_status="conflicting",
    )
    conflicting = control_tower.evaluate_evidence_gate(
        task_id="task:conflicting-closeout",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    try:
        control_tower.request_closeout_authorization(
            evidence_review_id=conflicting["evidence_review_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "conflicting evidence requires remediation" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("conflicting evidence must block closeout authorization")


def test_decide_closeout_authorization_checks_role_ttl_and_digest_without_closing_task(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    review = _record_verified_closeout_evidence("task:closeout-decision")
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="dashboard",
    )

    try:
        control_tower.decide_closeout_authorization(
            closeout_authorization_id=request["closeout_authorization_id"],
            decision="approved",
            actor_type="human",
            actor_id="reviewer",
            actor_role="operator",
            source_surface="dashboard",
            reason="operator is too weak for L2",
        )
    except ValueError as exc:
        assert "actor_role operator cannot decide L2 closeout" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("weak role should fail closed")

    decided = control_tower.decide_closeout_authorization(
        closeout_authorization_id=request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="dashboard",
        reason="human reviewed bound evidence digest",
    )
    repeated = control_tower.decide_closeout_authorization(
        closeout_authorization_id=request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="dashboard",
        reason="same decision retry",
    )

    assert decided["created"] is True
    assert decided["status"] == "approved"
    assert decided["closeout_allowed"] is False
    assert decided["execution"] == "not_executed"
    assert repeated["created"] is False
    with sqlite3.connect(home / "control_tower.db") as ledger:
        task_rows = ledger.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        status = ledger.execute(
            "SELECT status FROM closeout_authorizations WHERE closeout_authorization_id = ?",
            (request["closeout_authorization_id"],),
        ).fetchone()[0]
    assert task_rows == 0
    assert status == "approved"


def test_closeout_authorization_expires_and_snapshot_reports_boundary(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    review = _record_verified_closeout_evidence("task:closeout-expiry")
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="dashboard",
        ttl_hours=1,
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            "UPDATE closeout_authorizations SET expires_at = '2000-01-01T00:00:00+00:00' WHERE closeout_authorization_id = ?",
            (request["closeout_authorization_id"],),
        )
        ledger.commit()

    try:
        control_tower.decide_closeout_authorization(
            closeout_authorization_id=request["closeout_authorization_id"],
            decision="approved",
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "closeout authorization expired" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expired authorization should fail closed")

    snapshot = control_tower.build_snapshot()
    assert snapshot["closeout_authorizations"]["mode"] == "human_authorization_projection_only"
    assert snapshot["closeout_authorizations"]["closeout_execution_enabled"] is False
    assert snapshot["closeout_authorizations"]["pending_count"] == 0
    assert snapshot["closeout_authorizations"]["expired_count"] == 1
    assert snapshot["closeout_authorizations"]["decided_count"] == 0


def test_closeout_authorization_l3_requires_admin_role(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    control_tower.record_evidence(
        task_id="task:l3-closeout",
        evidence_type="proof",
        maturity="certified",
        source_type="pytest",
        summary="Certified proof for L3",
        producer_type="human",
        producer_id="charles",
        acceptance_refs=["AC-1"],
    )
    review = control_tower.evaluate_evidence_gate(
        task_id="task:l3-closeout",
        risk_level="L3",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="pytest",
    )

    try:
        control_tower.request_closeout_authorization(
            evidence_review_id=review["evidence_review_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "requires admin" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("L3 closeout authorization must require admin")

    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="admin",
        source_surface="pytest",
    )
    assert request["required_role"] == "admin"
    assert request["status"] == "requested"


def test_closeout_authorization_rejects_obsolete_evidence_review_after_new_conflict(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    control_tower.record_evidence(
        task_id="task:obsolete-review",
        evidence_type="proof",
        maturity="verified",
        source_type="pytest",
        summary="Verified proof before conflict",
        producer_type="human",
        producer_id="charles",
        acceptance_refs=["AC-1"],
    )
    review = control_tower.evaluate_evidence_gate(
        task_id="task:obsolete-review",
        risk_level="L2",
        acceptance_refs=["AC-1"],
        actor_type="human",
        actor_id="charles",
        source_surface="pytest",
    )
    assert review["evidence_status"] == "satisfied"

    control_tower.record_evidence(
        task_id="task:obsolete-review",
        evidence_type="review",
        maturity="verified",
        source_type="pytest",
        summary="Later conflicting evidence",
        producer_type="human",
        producer_id="reviewer",
        acceptance_refs=["AC-1"],
        stale_status="conflicting",
    )

    try:
        control_tower.request_closeout_authorization(
            evidence_review_id=review["evidence_review_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "evidence state changed after review" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("obsolete evidence review must not authorize closeout")


def test_expired_closeout_authorization_can_be_replaced_by_fresh_request(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    review = _record_verified_closeout_evidence("task:closeout-fresh-after-expiry")
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            "UPDATE closeout_authorizations SET expires_at = '2000-01-01T00:00:00+00:00' WHERE closeout_authorization_id = ?",
            (request["closeout_authorization_id"],),
        )
        ledger.commit()

    fresh = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )

    assert fresh["created"] is True
    assert fresh["status"] == "requested"
    assert fresh["closeout_authorization_id"] != request["closeout_authorization_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        rows = ledger.execute(
            "SELECT status, closeout_allowed FROM closeout_authorizations ORDER BY rowid"
        ).fetchall()
    assert rows == [("expired", 0), ("requested", 0)]


def test_decide_closeout_authorization_rejects_stale_evidence_after_request(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    review = _record_verified_closeout_evidence("task:decision-stale-after-request")
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    control_tower.record_evidence(
        task_id="task:decision-stale-after-request",
        evidence_type="review",
        maturity="verified",
        source_type="pytest",
        summary="Later conflicting evidence before decision",
        producer_type="human",
        producer_id="reviewer",
        acceptance_refs=["AC-1"],
        stale_status="conflicting",
    )

    try:
        control_tower.decide_closeout_authorization(
            closeout_authorization_id=request["closeout_authorization_id"],
            decision="approved",
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "evidence state changed after authorization request" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("stale evidence-bound authorization must not be decided")

    with sqlite3.connect(home / "control_tower.db") as ledger:
        rows = ledger.execute("SELECT status, closeout_allowed FROM closeout_authorizations").fetchall()
    assert rows == [("requested", 0)]


def test_read_only_whitelisted_executor_policy_marks_l1_eligible_without_execution(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RunReadOnlyProofCheck",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:proof",
        risk_level="L1",
        payload={"reason": "read-only proof check"},
        idempotency_key="proof-check-read-only",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    review = control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert review["policy_status"] == "read_only_eligible"
    assert review["allowed_to_execute"] is True
    assert review["writes_enabled"] is False
    assert review["execution"] == "not_executed"
    assert review["blocked_reason"] == "read_only_executor_whitelisted"
    assert review["adapter_writes"] == []
    assert review["rollback_preview"] == {
        "available": False,
        "reason": "read-only executor action has no lifecycle mutation to roll back",
    }
    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        command_status = ledger.execute(
            "SELECT status FROM commands WHERE command_id = ?",
            (proposed["command_id"],),
        ).fetchone()[0]
    assert command_status == "proposed"


def test_executor_policy_blocks_non_whitelisted_or_high_risk_even_after_approval(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    non_whitelisted = control_tower.propose_command(
        command_type="ReleaseLease",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:l1-release",
        risk_level="L1",
        payload={},
        idempotency_key="l1-release-not-whitelisted",
    )
    l2 = control_tower.propose_command(
        command_type="RunReadOnlyProofCheck",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="task",
        target_id="task:l2-proof",
        risk_level="L2",
        payload={},
        idempotency_key="l2-proof-approved-but-blocked",
    )
    control_tower.decide_human_approval(
        approval_id=l2["approval_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        reason="approved for policy preview only",
    )

    blocked_plan = control_tower.generate_executor_plan(
        command_id=non_whitelisted["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    high_risk_plan = control_tower.generate_executor_plan(
        command_id=l2["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    blocked_review = control_tower.review_executor_plan_policy(
        plan_id=blocked_plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    high_risk_review = control_tower.review_executor_plan_policy(
        plan_id=high_risk_plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert blocked_review["allowed_to_execute"] is False
    assert blocked_review["blocked_reason"] == "command_type_not_in_read_only_executor_whitelist"
    assert high_risk_review["allowed_to_execute"] is False
    assert high_risk_review["blocked_reason"] == "risk_level_exceeds_read_only_executor_whitelist"
    assert high_risk_review["execution"] == "not_executed"
    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_execution_policy_snapshot_reports_read_only_eligibility_boundary(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RefreshProjection",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="projection",
        target_id="overview",
        risk_level="L0",
        payload={},
        idempotency_key="refresh-read-only-policy-snapshot",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    review = control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    snapshot = control_tower.build_snapshot()

    assert snapshot["execution_policy"]["mode"] == "read_only_executor_policy_projection"
    assert snapshot["execution_policy"]["real_executor_enabled"] is False
    assert snapshot["execution_policy"]["writes_enabled"] is False
    assert snapshot["execution_policy"]["read_only_eligible_count"] == 1
    assert snapshot["execution_policy"]["blocked_count"] == 0
    assert snapshot["execution_policy"]["read_only_whitelist"] == [
        "CopyCliCommand",
        "RefreshProjection",
        "RunReadOnlyProofCheck",
        "RunReadOnlySelfCheck",
    ]
    assert snapshot["execution_policy"]["recent_reviews"] == [
        {
            "policy_review_id": review["policy_review_id"],
            "plan_id": plan["plan_id"],
            "command_id": proposed["command_id"],
            "policy_status": "read_only_eligible",
            "allowed_to_execute": True,
            "blocked_reason": "read_only_executor_whitelisted",
            "rollback_available": False,
        }
    ]
