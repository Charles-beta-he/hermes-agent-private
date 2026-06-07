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
