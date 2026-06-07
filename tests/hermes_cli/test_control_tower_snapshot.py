from __future__ import annotations

import hashlib
import json
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
            "read_only_executor_runs",
            "closeout_execution_runs",
            "external_adapter_write_intents",
            "external_adapter_write_intent_reviews",
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

def test_execute_read_only_executor_runs_whitelisted_policy_once_without_task_or_adapter_mutation(tmp_path, monkeypatch):
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
        payload={"acceptance_refs": ["AC-1"]},
        idempotency_key="phase7-read-only-proof",
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

    run = control_tower.execute_read_only_executor_plan(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    repeated = control_tower.execute_read_only_executor_plan(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    assert run["created"] is True
    assert run["executor_status"] == "read_only_completed"
    assert run["execution"] == "read_only_completed"
    assert run["writes_enabled"] is False
    assert run["adapter_writes"] == []
    assert run["policy_review_id"] == review["policy_review_id"]
    assert run["result"]["check"] == "proof_check"
    assert run["result"]["mutation_allowed"] is False
    assert repeated["created"] is False
    assert repeated["executor_run_id"] == run["executor_run_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        task_rows = ledger.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        adapter_rows = ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0]
        plan_row = ledger.execute(
            "SELECT status, execution, writes_enabled FROM executor_plans WHERE plan_id = ?",
            (plan["plan_id"],),
        ).fetchone()
        command_status = ledger.execute(
            "SELECT status FROM commands WHERE command_id = ?",
            (proposed["command_id"],),
        ).fetchone()[0]
        run_rows = ledger.execute("SELECT COUNT(*) FROM read_only_executor_runs").fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert task_rows == 0
    assert adapter_rows == 0
    assert plan_row == ("executed", "read_only_completed", 0)
    assert command_status == "executed"
    assert run_rows == 1
    assert event_types == [
        "CommandProposed",
        "ExecutorPlanGenerated",
        "ExecutionPolicyProjectionRecorded",
        "ReadOnlyExecutorRunCompleted",
    ]


def test_execute_read_only_executor_fails_closed_without_allowed_policy_or_after_policy_stale(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RunReadOnlySelfCheck",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="system",
        target_id="control-tower",
        risk_level="L1",
        payload={},
        idempotency_key="phase7-stale-policy",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    try:
        control_tower.execute_read_only_executor_plan(
            plan_id=plan["plan_id"],
            actor_type="human",
            actor_id="charles",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "allowed execution policy review is required" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("missing policy review should fail closed")

    control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            "UPDATE commands SET risk_level = 'L2' WHERE command_id = ?",
            (proposed["command_id"],),
        )
        ledger.commit()
    try:
        control_tower.execute_read_only_executor_plan(
            plan_id=plan["plan_id"],
            actor_type="human",
            actor_id="charles",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "read-only executor policy is stale" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("stale policy review should fail closed")


def test_read_only_executor_snapshot_reports_runs_without_write_authority(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="CopyCliCommand",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="command",
        target_id="copyable",
        risk_level="L0",
        payload={},
        idempotency_key="phase7-snapshot",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    run = control_tower.execute_read_only_executor_plan(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )

    snapshot = control_tower.build_snapshot()

    assert snapshot["read_only_executor"]["mode"] == "read_only_executor_consumer"
    assert snapshot["read_only_executor"]["writes_enabled"] is False
    assert snapshot["read_only_executor"]["adapter_write_through_enabled"] is False
    assert snapshot["read_only_executor"]["run_count"] == 1
    assert snapshot["read_only_executor"]["recent_runs"] == [
        {
            "executor_run_id": run["executor_run_id"],
            "plan_id": plan["plan_id"],
            "command_id": proposed["command_id"],
            "command_type": "CopyCliCommand",
            "executor_status": "read_only_completed",
            "writes_enabled": False,
        }
    ]


def test_execute_read_only_executor_rejects_reviewed_plan_when_command_tuple_changes_within_whitelist(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    proposed = control_tower.propose_command(
        command_type="RunReadOnlySelfCheck",
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
        target_type="system",
        target_id="control-tower",
        risk_level="L1",
        payload={"check": "self"},
        idempotency_key="phase7-exact-policy-freshness",
    )
    plan = control_tower.generate_executor_plan(
        command_id=proposed["command_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    control_tower.review_executor_plan_policy(
        plan_id=plan["plan_id"],
        actor_type="human",
        actor_id="charles",
        source_surface="dashboard",
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            """
            UPDATE commands
            SET command_type = 'RefreshProjection', payload_json = ?
            WHERE command_id = ?
            """,
            (json.dumps({"projection": "overview"}), proposed["command_id"]),
        )
        ledger.commit()

    try:
        control_tower.execute_read_only_executor_plan(
            plan_id=plan["plan_id"],
            actor_type="human",
            actor_id="charles",
            source_surface="dashboard",
        )
    except ValueError as exc:
        assert "read-only executor policy is stale" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("changed command tuple should fail closed even if still whitelisted")

    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM read_only_executor_runs").fetchone()[0] == 0
        assert ledger.execute("SELECT status FROM commands WHERE command_id = ?", (proposed["command_id"],)).fetchone()[0] == "proposed"


def _insert_control_tower_task(
    home,
    task_id: str,
    status: str = "review",
    external_ref: str | None = None,
) -> None:
    now = "2026-06-07T00:00:00+00:00"
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            """
            INSERT INTO tasks(task_id, external_ref, title, status, risk_class, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, external_ref, f"Closeout target {task_id}", status, "L2", now, now),
        )
        ledger.commit()


def test_execute_closeout_authorization_closes_control_tower_task_without_adapter_write(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    task_id = "task:phase8-closeout"
    review = _record_verified_closeout_evidence(task_id)
    _insert_control_tower_task(home, task_id)
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    decision = control_tower.decide_closeout_authorization(
        closeout_authorization_id=request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
        reason="approved for bounded ledger closeout",
    )

    execution = control_tower.execute_closeout_authorization(
        closeout_authorization_id=decision["closeout_authorization_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
        reason="execute bounded ledger closeout",
    )
    repeated = control_tower.execute_closeout_authorization(
        closeout_authorization_id=decision["closeout_authorization_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
        reason="retry bounded ledger closeout",
    )

    assert execution["created"] is True
    assert execution["execution_status"] == "closeout_completed"
    assert execution["task_id"] == task_id
    assert execution["task_status"] == "closed"
    assert execution["adapter_writes"] == []
    assert execution["external_adapter_write_through_enabled"] is False
    assert repeated["created"] is False
    assert repeated["closeout_execution_id"] == execution["closeout_execution_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        task_row = ledger.execute(
            "SELECT status, blocked_reason, next_action FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        adapter_rows = ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0]
        run_rows = ledger.execute("SELECT COUNT(*) FROM closeout_execution_runs").fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert task_row == ("closed", None, None)
    assert adapter_rows == 0
    assert run_rows == 1
    assert event_types[-1] == "CloseoutExecutionCompleted"
    snapshot = control_tower.build_snapshot()
    assert snapshot["closeout_execution"]["mode"] == "control_tower_ledger_closeout_consumer"
    assert snapshot["closeout_execution"]["external_adapter_write_through_enabled"] is False
    assert snapshot["closeout_execution"]["run_count"] == 1


def test_execute_closeout_authorization_fails_closed_when_not_approved_or_evidence_stale(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    task_id = "task:phase8-stale-closeout"
    review = _record_verified_closeout_evidence(task_id)
    _insert_control_tower_task(home, task_id)
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    try:
        control_tower.execute_closeout_authorization(
            closeout_authorization_id=request["closeout_authorization_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "not approved" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("unapproved closeout authorization must fail closed")

    approved = control_tower.decide_closeout_authorization(
        closeout_authorization_id=request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    control_tower.record_evidence(
        task_id=task_id,
        evidence_type="review",
        maturity="verified",
        source_type="pytest",
        summary="Later conflicting evidence before execution",
        producer_type="human",
        producer_id="reviewer",
        acceptance_refs=["AC-1"],
        stale_status="conflicting",
    )

    try:
        control_tower.execute_closeout_authorization(
            closeout_authorization_id=approved["closeout_authorization_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "evidence state changed after closeout authorization" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("stale evidence after approval must fail closed")

    with sqlite3.connect(home / "control_tower.db") as ledger:
        task_status = ledger.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[0]
        run_rows = ledger.execute("SELECT COUNT(*) FROM closeout_execution_runs").fetchone()[0]
    assert task_status == "review"
    assert run_rows == 0


def test_execute_closeout_authorization_rejects_invalid_actor_and_role_boundaries(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    task_id = "task:phase8-role-boundary"
    review = _record_verified_closeout_evidence(task_id)
    _insert_control_tower_task(home, task_id)
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    approved = control_tower.decide_closeout_authorization(
        closeout_authorization_id=request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )

    invalid_cases = [
        {"closeout_authorization_id": "", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "closeout_authorization_id is required"},
        {"closeout_authorization_id": approved["closeout_authorization_id"], "actor_type": "", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "actor_type is required"},
        {"closeout_authorization_id": approved["closeout_authorization_id"], "actor_type": "human", "actor_id": "", "actor_role": "owner", "source_surface": "pytest", "expected": "actor_id is required"},
        {"closeout_authorization_id": approved["closeout_authorization_id"], "actor_type": "human", "actor_id": "charles", "actor_role": "invalid", "source_surface": "pytest", "expected": "unsupported actor_role"},
        {"closeout_authorization_id": approved["closeout_authorization_id"], "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "", "expected": "source_surface is required"},
        {"closeout_authorization_id": approved["closeout_authorization_id"], "actor_type": "human", "actor_id": "charles", "actor_role": "operator", "source_surface": "pytest", "expected": "cannot execute"},
    ]
    for case in invalid_cases:
        expected = case.pop("expected")
        try:
            control_tower.execute_closeout_authorization(**case)
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError(f"expected ValueError containing {expected!r}")


def test_execute_closeout_authorization_rejects_expired_missing_task_and_closed_task(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    expired_task = "task:phase8-expired"
    expired_review = _record_verified_closeout_evidence(expired_task)
    _insert_control_tower_task(home, expired_task)
    expired_request = control_tower.request_closeout_authorization(
        evidence_review_id=expired_review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    expired = control_tower.decide_closeout_authorization(
        closeout_authorization_id=expired_request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    with sqlite3.connect(home / "control_tower.db") as ledger:
        ledger.execute(
            "UPDATE closeout_authorizations SET expires_at = ? WHERE closeout_authorization_id = ?",
            ("2000-01-01T00:00:00+00:00", expired["closeout_authorization_id"]),
        )
        ledger.commit()
    try:
        control_tower.execute_closeout_authorization(
            closeout_authorization_id=expired["closeout_authorization_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "expired" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expired closeout authorization must fail closed")

    missing_task = "task:phase8-missing-task"
    missing_review = _record_verified_closeout_evidence(missing_task)
    missing_request = control_tower.request_closeout_authorization(
        evidence_review_id=missing_review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    missing_approved = control_tower.decide_closeout_authorization(
        closeout_authorization_id=missing_request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    try:
        control_tower.execute_closeout_authorization(
            closeout_authorization_id=missing_approved["closeout_authorization_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "Control Tower task row is required" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("missing task row must fail closed")

    closed_task = "task:phase8-closed-without-run"
    closed_review = _record_verified_closeout_evidence(closed_task)
    _insert_control_tower_task(home, closed_task, status="closed")
    closed_request = control_tower.request_closeout_authorization(
        evidence_review_id=closed_review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    closed_approved = control_tower.decide_closeout_authorization(
        closeout_authorization_id=closed_request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    try:
        control_tower.execute_closeout_authorization(
            closeout_authorization_id=closed_approved["closeout_authorization_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "task is already closed" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("closed task without execution row must fail closed")


def _execute_ledger_closeout_for_adapter_intent(home, task_id: str = "task:phase9-closeout") -> dict[str, object]:
    review = _record_verified_closeout_evidence(task_id)
    _insert_control_tower_task(home, task_id, external_ref=f"taskpool:{task_id}")
    request = control_tower.request_closeout_authorization(
        evidence_review_id=review["evidence_review_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    decision = control_tower.decide_closeout_authorization(
        closeout_authorization_id=request["closeout_authorization_id"],
        decision="approved",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )
    return control_tower.execute_closeout_authorization(
        closeout_authorization_id=decision["closeout_authorization_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )


def test_prepare_external_adapter_write_intent_records_digest_without_write_through(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    execution = _execute_ledger_closeout_for_adapter_intent(home)

    intent = control_tower.prepare_external_adapter_write_intent(
        closeout_execution_id=execution["closeout_execution_id"],
        adapter_name="taskpool",
        external_ref="taskpool:task:phase9-closeout",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
        reason="prepare exact external write intent but do not execute",
    )
    repeated = control_tower.prepare_external_adapter_write_intent(
        closeout_execution_id=execution["closeout_execution_id"],
        adapter_name="taskpool",
        external_ref="taskpool:task:phase9-closeout",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )

    assert intent["created"] is True
    assert intent["intent_status"] == "blocked_pending_adapter_contract"
    assert intent["writes_enabled"] is False
    assert intent["external_adapter_write_through_enabled"] is False
    assert intent["adapter_name"] == "taskpool"
    assert intent["operation"] == "closeout"
    assert intent["mutation_payload"]["target_status"] == "closed"
    expected_digest = hashlib.sha256(
        json.dumps(intent["mutation_payload"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert intent["mutation_digest"] == expected_digest
    assert intent["rollback_required"] is True
    assert intent["rollback_available"] is False
    assert repeated["created"] is False
    assert repeated["external_write_intent_id"] == intent["external_write_intent_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        rows = ledger.execute("SELECT COUNT(*) FROM external_adapter_write_intents").fetchone()[0]
        adapter_rows = ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0]
        task_status = ledger.execute("SELECT status FROM tasks WHERE task_id = ?", (execution["task_id"],)).fetchone()[0]
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert rows == 1
    assert adapter_rows == 0
    assert task_status == "closed"
    assert event_types[-1] == "ExternalAdapterWriteIntentPrepared"
    snapshot = control_tower.build_snapshot()
    assert snapshot["external_adapter_write_intents"]["intent_count"] == 1
    assert snapshot["external_adapter_write_intents"]["external_adapter_write_through_enabled"] is False
    assert snapshot["external_adapter_write_intents"]["blocked_count"] == 1


def test_prepare_external_adapter_write_intent_fails_closed_before_adapter_boundary(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    execution = _execute_ledger_closeout_for_adapter_intent(home, "task:phase9-fail")

    invalid_cases = [
        {"closeout_execution_id": "", "adapter_name": "taskpool", "external_ref": "taskpool:1", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "closeout_execution_id is required"},
        {"closeout_execution_id": execution["closeout_execution_id"], "adapter_name": "", "external_ref": "taskpool:1", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "adapter_name is required"},
        {"closeout_execution_id": execution["closeout_execution_id"], "adapter_name": "unknown", "external_ref": "taskpool:1", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "adapter is not enabled"},
        {"closeout_execution_id": execution["closeout_execution_id"], "adapter_name": "taskpool", "external_ref": "", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "external_ref is required"},
        {"closeout_execution_id": "closeexec_missing", "adapter_name": "taskpool", "external_ref": "taskpool:1", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "closeout execution not found"},
        {"closeout_execution_id": execution["closeout_execution_id"], "adapter_name": "taskpool", "external_ref": "taskpool:wrong-ref", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "external_ref does not match"},
        {"closeout_execution_id": execution["closeout_execution_id"], "adapter_name": "taskpool", "external_ref": "taskpool:1", "actor_type": "human", "actor_id": "charles", "actor_role": "operator", "source_surface": "pytest", "expected": "cannot prepare"},
    ]
    for case in invalid_cases:
        expected = case.pop("expected")
        try:
            control_tower.prepare_external_adapter_write_intent(**case)
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError(f"expected ValueError containing {expected!r}")

    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_intents").fetchone()[0] == 0
        assert ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0] == 0


def _prepare_external_write_intent_for_review(home, task_id: str = "task:phase10-review") -> dict[str, object]:
    execution = _execute_ledger_closeout_for_adapter_intent(home, task_id)
    return control_tower.prepare_external_adapter_write_intent(
        closeout_execution_id=execution["closeout_execution_id"],
        adapter_name="taskpool",
        external_ref=f"taskpool:{task_id}",
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )


def test_review_external_adapter_write_intent_blocks_execution_until_contract_and_rollback(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    intent = _prepare_external_write_intent_for_review(home)

    review = control_tower.review_external_adapter_write_intent(
        external_write_intent_id=intent["external_write_intent_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
        reason="review readiness without external write-through",
    )
    repeated = control_tower.review_external_adapter_write_intent(
        external_write_intent_id=intent["external_write_intent_id"],
        actor_type="human",
        actor_id="charles",
        actor_role="owner",
        source_surface="pytest",
    )

    assert review["created"] is True
    assert review["review_status"] == "blocked"
    assert review["write_through_allowed"] is False
    assert review["writes_enabled"] is False
    assert review["external_adapter_write_through_enabled"] is False
    assert review["mutation_digest"] == intent["mutation_digest"]
    assert review["adapter_contract_version"] == "taskpool.closeout.v0"
    assert "external_write_through_disabled" in review["blocked_reasons"]
    assert "rollback_unavailable" in review["blocked_reasons"]
    assert repeated["created"] is False
    assert repeated["external_write_review_id"] == review["external_write_review_id"]
    with sqlite3.connect(home / "control_tower.db") as ledger:
        assert ledger.execute("SELECT COUNT(*) FROM external_adapter_write_intent_reviews").fetchone()[0] == 1
        assert ledger.execute("SELECT COUNT(*) FROM adapter_observations").fetchone()[0] == 0
        event_types = [row[0] for row in ledger.execute("SELECT event_type FROM events ORDER BY sequence")]
    assert event_types[-1] == "ExternalAdapterWriteIntentReviewed"
    snapshot = control_tower.build_snapshot()
    assert snapshot["external_adapter_write_intent_reviews"]["review_count"] == 1
    assert snapshot["external_adapter_write_intent_reviews"]["blocked_count"] == 1
    assert snapshot["external_adapter_write_intent_reviews"]["write_through_allowed_count"] == 0
    assert snapshot["external_adapter_write_intent_reviews"]["external_adapter_write_through_enabled"] is False


def test_review_external_adapter_write_intent_fails_closed_on_drift_and_bad_actor(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    intent = _prepare_external_write_intent_for_review(home, "task:phase10-drift")

    invalid_cases = [
        {"external_write_intent_id": "", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "external_write_intent_id is required"},
        {"external_write_intent_id": intent["external_write_intent_id"], "actor_type": "", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "actor_type is required"},
        {"external_write_intent_id": intent["external_write_intent_id"], "actor_type": "human", "actor_id": "", "actor_role": "owner", "source_surface": "pytest", "expected": "actor_id is required"},
        {"external_write_intent_id": intent["external_write_intent_id"], "actor_type": "human", "actor_id": "charles", "actor_role": "invalid", "source_surface": "pytest", "expected": "unsupported actor_role"},
        {"external_write_intent_id": intent["external_write_intent_id"], "actor_type": "human", "actor_id": "charles", "actor_role": "operator", "source_surface": "pytest", "expected": "cannot review"},
        {"external_write_intent_id": intent["external_write_intent_id"], "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "", "expected": "source_surface is required"},
        {"external_write_intent_id": "extintent_missing", "actor_type": "human", "actor_id": "charles", "actor_role": "owner", "source_surface": "pytest", "expected": "external write intent not found"},
    ]
    for case in invalid_cases:
        expected = case.pop("expected")
        try:
            control_tower.review_external_adapter_write_intent(**case)
        except ValueError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError(f"expected ValueError containing {expected!r}")

    with sqlite3.connect(home / "control_tower.db") as ledger:
        payload = json.loads(ledger.execute(
            "SELECT mutation_payload_json FROM external_adapter_write_intents WHERE external_write_intent_id = ?",
            (intent["external_write_intent_id"],),
        ).fetchone()[0])
        payload["target_status"] = "archived"
        ledger.execute(
            "UPDATE external_adapter_write_intents SET mutation_payload_json = ? WHERE external_write_intent_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), intent["external_write_intent_id"]),
        )
        ledger.commit()
    try:
        control_tower.review_external_adapter_write_intent(
            external_write_intent_id=intent["external_write_intent_id"],
            actor_type="human",
            actor_id="charles",
            actor_role="owner",
            source_surface="pytest",
        )
    except ValueError as exc:
        assert "mutation digest mismatch" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("mutation payload drift must fail closed")
