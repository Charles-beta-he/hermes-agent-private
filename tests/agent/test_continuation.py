import json

from agent.continuation import (
    ContinuationPacket,
    build_continuation_packet,
    continuation_status,
    normalize_continuation_reason,
    write_continuation_packet,
)


def test_build_continuation_packet_has_global_schema_and_resume_policy():
    packet = build_continuation_packet(
        scope="conversation",
        reason="max_iterations_reached(2/2)",
        summary="partial summary",
        session_id="session-1",
        task_id="task-1",
        remaining_work=["finish verification"],
        evidence=[{"kind": "test", "status": "pending"}],
        resume={"mode": "immediate", "executor": "conversation_loop", "max_retries": 3},
        limits={"relay_index": 1, "max_relay_count": 3},
    )

    assert isinstance(packet, ContinuationPacket)
    payload = packet.to_dict()
    assert payload["schema"] == "hermes_continuation_packet_v1"
    assert payload["scope"] == "conversation"
    assert payload["reason"] == "max_iterations"
    assert payload["raw_reason"] == "max_iterations_reached(2/2)"
    assert payload["summary"] == "partial summary"
    assert payload["resume"]["mode"] == "immediate"
    assert payload["resume"]["executor"] == "conversation_loop"
    assert payload["limits"]["relay_index"] == 1
    assert payload["remaining_work"] == ["finish verification"]


def test_write_continuation_packet_indexes_active_state(tmp_path):
    packet = build_continuation_packet(
        scope="delegate",
        reason="budget_exhausted",
        summary="child stopped",
        session_id="child-session",
        task_id="task-2",
        resume={"mode": "immediate_then_queue", "executor": "delegate_task", "max_retries": 2},
    )

    path = write_continuation_packet(packet, root=tmp_path, state="active")
    payload = json.loads(path.read_text())

    assert path.parent == tmp_path / "active"
    assert payload["schema"] == "hermes_continuation_packet_v1"
    assert payload["packet_id"] == packet.packet_id
    assert payload["scope"] == "delegate"

    status = continuation_status(root=tmp_path, backlog_warning_threshold=0)
    assert status["schema"] == "hermes_continuation_status_v1"
    assert status["active_count"] == 1
    assert status["by_scope"]["delegate"] == 1
    assert status["backlog_warning"] == "continuation_backlog_high"


def test_normalize_continuation_reason_keeps_raw_budget_variants():
    assert normalize_continuation_reason("budget_exhausted") == "budget_exhausted"
    assert normalize_continuation_reason("max_iterations_reached(8/8)") == "max_iterations"
    assert normalize_continuation_reason("provider timeout") == "provider_timeout"
