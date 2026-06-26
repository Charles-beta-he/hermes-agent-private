from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermes_cli.agent_os_control_plane import (
    authorize_local_harness,
    doctor,
    execute_local_harness,
    generate_replay_quality_report,
    replay_recent_user_intents,
    route_shadow,
    status,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _seed_agent_os(home: Path, *, runtime_mutation: bool = False, shadow_executes: bool = False) -> None:
    project = "hermes-agent-os-control-plane"
    root = home / "workflows" / "projects" / project
    _write_json(
        home / "project-profiles" / f"{project}.json",
        {
            "project": project,
            "status": "p0-read-only-projection-validated+p1-sample-shadow-validated",
            "ssot_root": str(root),
            "progress_ledger": str(root / "progress-ledger.md"),
            "current_components": {
                "runtime_implementation": "advisory_readback_only/default-off",
                "capability_graph_projection": "validated-local-read-only-slice/2-nodes",
                "capability_intent": "shadow-only",
            },
        },
    )
    _write_json(
        root / "06-projection" / "capability_graph_projection.json",
        {
            "project": project,
            "runtime_mutation": runtime_mutation,
            "nodes": [
                {
                    "id": "tool:send_message",
                    "kind": "tool",
                    "responsibility": "Send messages to connected platforms",
                    "side_effects": "S4",
                    "source_ref": "test",
                },
                {
                    "id": "skill:hermes-agent",
                    "kind": "skill",
                    "responsibility": "Operate Hermes Agent control plane",
                    "side_effects": "S0",
                    "source_ref": "test",
                },
            ],
        },
    )
    _write_json(root / "06-projection" / "doctor_report.json", {"ok": True, "findings": []})
    _write_json(
        root / "07-shadow" / "router-shadow-report.json",
        {"ok": True, "records": 1, "blocked_by_policy": 0, "would_execute_anything": shadow_executes},
    )


def test_agent_os_status_reads_projection_without_runtime_mutation(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_advisory_guidance": True, "agent_os_runtime_routing": True, "agent_os_multimodel_review": True, "agent_os_local_harness_executor": True, "agent_os_scheduler_cron_write": True, "agent_os_taskpool_lifecycle_write": True}})
    payload = status(tmp_path)
    assert payload["ok"] is True
    assert payload["mode"] == "advisory_readback_only"
    assert payload["projection"]["node_count"] == 2
    assert payload["config"]["agent_os_advisory_guidance"] is True
    assert payload["config"]["real_runtime_routing"] is True
    assert payload["config"]["agent_os_multimodel_review"] is True
    assert payload["config"]["agent_os_local_harness_executor"] is True
    assert payload["config"]["agent_os_scheduler_cron_write"] is True
    assert payload["config"]["tool_invocation_from_agent_os"] is True
    assert payload["config"]["scheduler_cron_write"] is True
    assert payload["config"]["agent_os_taskpool_lifecycle_write"] is True
    assert payload["config"]["taskpool_mission_lifecycle_write"] is True
    assert payload["runtime_effect"].startswith("local taskpool lifecycle write enabled")


def test_agent_os_status_defaults_multimodel_review_and_execution_gates_closed(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {}})

    payload = status(tmp_path)

    assert payload["config"]["agent_os_multimodel_review"] is False
    assert payload["config"]["real_runtime_routing"] is False
    assert payload["config"]["tool_invocation_from_agent_os"] is False
    assert payload["config"]["scheduler_cron_write"] is False
    assert payload["config"]["taskpool_mission_lifecycle_write"] is False
    assert payload["config"]["external_dispatch"] is False
    assert payload["runtime_effect"].startswith("none")


def test_agent_os_doctor_fails_closed_on_runtime_mutation(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path, runtime_mutation=True)
    payload = doctor(tmp_path)
    assert payload["ok"] is False
    assert any(f["check"] == "runtime_mutation_default_off" for f in payload["findings"])


def test_agent_os_doctor_fails_closed_if_shadow_report_would_execute(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path, shadow_executes=True)
    payload = doctor(tmp_path)
    assert payload["ok"] is False
    assert any(f["check"] == "shadow_no_execute" for f in payload["findings"])


def test_agent_os_route_shadow_blocks_external_side_effects(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    payload = route_shadow("send a Telegram message", tmp_path)
    assert payload["ok"] is True
    assert payload["would_execute"] is False
    assert payload["intent"]["risk_class"] == "high"
    assert any(f["gate"] == "default_off_external_side_effects" for f in payload["policy_findings"])


def test_agent_os_replay_recent_user_intents_is_read_only_and_redacted(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INTEGER)"
    )
    con.execute("INSERT INTO sessions (id, source) VALUES ('s1', 'cli')")
    con.execute(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) VALUES (1, 's1', 'user', ?, 1.0, 1)",
        ("send telegram token=abc123 to ops@example.com",),
    )
    con.commit()
    con.close()

    payload = replay_recent_user_intents(tmp_path, limit=5)
    assert payload["ok"] is True
    assert payload["would_execute_anything"] is False
    assert payload["record_count"] == 1
    preview = payload["records"][0]["redacted_intent_preview"]
    assert "abc123" not in preview
    assert "ops@example.com" not in preview
    assert "[REDACTED]" in preview


def test_agent_os_report_writes_authority_packet_without_opening_gates(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INTEGER)"
    )
    con.execute("INSERT INTO sessions (id, source) VALUES ('s1', 'cli')")
    con.execute(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) VALUES (1, 's1', 'user', ?, 1.0, 1)",
        ("把这个 PR 合并并发布到生产",),
    )
    con.commit()
    con.close()

    payload = generate_replay_quality_report(tmp_path, limit=5, write=True)
    assert payload["ok"] is True
    assert payload["would_execute_anything"] is False
    packet = payload["authority_packet"]
    assert packet["status"] == "HUMAN_AUTHORIZATION_REQUIRED"
    assert packet["requested_authority"]["runtime_advisory_prompt_injection"] is True
    assert packet["requested_authority"]["real_runtime_routing"] is False
    assert packet["requested_authority"]["external_dispatch"] is False
    assert Path(payload["written"]["report"]).exists()
    assert Path(payload["written"]["authority_packet"]).exists()


def test_agent_os_local_harness_executor_writes_and_receipts(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True
    assert auth["executed"] is False

    authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        expected_previous_status=None,
        home=tmp_path,
    )

    assert payload["ok"] is True
    assert payload["executed"] is True
    assert payload["external_side_effects_executed"] is False
    assert payload["real_taskpool_write_executed"] is False
    state = json.loads(Path(payload["adapter_state_path"]).read_text())
    assert state["tasks"]["task:example"]["status"] == "done"
    receipts = Path(payload["receipt_path"]).read_text().splitlines()
    assert len(receipts) == 1

    duplicate = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert duplicate["ok"] is True
    assert duplicate["executed"] is False
    assert duplicate["idempotent"] is True
    assert Path(payload["receipt_path"]).read_text().splitlines() == receipts

    mismatched_evidence = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:different"],
        home=tmp_path,
    )
    assert mismatched_evidence["ok"] is False
    assert mismatched_evidence["blocked_reason"] == "authorization_record_missing_or_mismatched"


def test_agent_os_local_harness_executor_rejects_when_disabled_or_missing_evidence(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {}})

    disabled = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert disabled["ok"] is False
    assert disabled["blocked_reason"] == "agent_os_local_harness_executor_disabled"

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    missing_evidence = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=[],
        home=tmp_path,
    )
    assert missing_evidence["ok"] is False
    assert missing_evidence["blocked_reason"] == "missing_evidence_refs"


def test_agent_os_local_harness_executor_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        adapter_state_path=tmp_path / "outside.json",
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"



def test_agent_os_local_harness_executor_rejects_without_authorization_record(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    payload = execute_local_harness(
        authorization_id="auth:missing",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "authorization_record_missing_or_mismatched"


def test_agent_os_local_harness_rejects_state_and_receipt_symlinks(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    executor_dir = tmp_path / "workflows" / "projects" / "hermes-agent-os-control-plane" / "08-executor"
    executor_dir.mkdir(parents=True, exist_ok=True)
    outside_state = tmp_path / "outside-state.json"
    state_link = executor_dir / "local-harness-state.json"
    state_link.symlink_to(outside_state)

    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is False
    assert auth["blocked_reason"] == "invalid_executor_path"
    assert not outside_state.exists()

    state_link.unlink()
    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True
    outside_receipts = tmp_path / "outside-receipts.jsonl"
    receipts_link = executor_dir / "local-harness-receipts.jsonl"
    receipts_link.symlink_to(outside_receipts)

    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"
    assert not outside_receipts.exists()


def test_agent_os_local_harness_rejects_subdirectory_even_if_symlink_points_inside(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    executor_dir = tmp_path / "workflows" / "projects" / "hermes-agent-os-control-plane" / "08-executor"
    inside = executor_dir / "inside"
    inside.mkdir(parents=True, exist_ok=True)
    link = executor_dir / "link-inside"
    link.symlink_to(inside)

    payload = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        adapter_state_path=link / "state.json",
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"


def test_agent_os_local_harness_rolls_back_state_if_receipt_append_fails(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True

    def fail_append(path, payload):
        raise ValueError("forced receipt failure")

    monkeypatch.setattr("hermes_cli.agent_os_control_plane._append_jsonl", fail_append)
    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["executed"] is False
    assert payload["blocked_reason"] == "receipt_append_failed"
    state = json.loads(Path(auth["adapter_state_path"]).read_text())
    assert state.get("tasks", {}) == {}


def test_agent_os_local_harness_rejects_parent_traversal_state_path(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    payload = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        adapter_state_path="foo/../state.json",
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"


def test_agent_os_local_harness_reports_rollback_failure(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True

    calls = {"write": 0}

    def fail_append(path, payload):
        raise OSError("forced receipt failure")

    def fail_second_write(path, payload):
        calls["write"] += 1
        if calls["write"] >= 2:
            raise OSError("forced rollback failure")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    monkeypatch.setattr("hermes_cli.agent_os_control_plane._append_jsonl", fail_append)
    monkeypatch.setattr("hermes_cli.agent_os_control_plane._safe_write_json", fail_second_write)
    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["executed"] is True
    assert payload["rollback_failed"] is True
    assert payload["blocked_reason"] == "receipt_append_failed_rollback_failed"
