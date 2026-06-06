import json

from agent.continuation import (
    MAX_ACTIVE_CONTINUATIONS,
    ContinuationPacket,
    build_continuation_packet,
    build_clean_session_handoff,
    continuation_status,
    evaluate_continuation_eligibility,
    normalize_continuation_reason,
    write_continuation_packet,
    write_legacy_relay_packet,
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


def test_goal_bound_continuation_requires_explicit_opt_in_and_machine_boundary():
    packet = build_continuation_packet(
        scope="conversation",
        reason="max_iterations_reached(3/3)",
        summary="candidate summary only",
        task_id="task-1",
        remaining_work=["finish linked Lark tasks"],
        evidence=[{"id": "E-1", "supports": ["remaining_work"]}],
        goal_boundary={"type": "lark_task_ids", "ids": ["lark-1"], "closure_criteria": "all_done"},
        freshness_requirements={"lark": {"max_age_seconds": 300, "last_checked_at": "fresh"}},
        limits={"refresh_count": 1, "max_refreshes_per_task": 3, "global_refresh_count": 1, "global_hard_cap": 5},
    )

    disabled = evaluate_continuation_eligibility(packet, goal_boundary_enabled=False)
    assert disabled["decision"] == "recommend_only"
    assert "goal_bound_disabled" in disabled["reasons"]

    allowed = evaluate_continuation_eligibility(packet, goal_boundary_enabled=True)
    assert allowed["decision"] == "continue"
    assert allowed["boundary_type"] == "lark_task_ids"


def test_open_ended_and_summary_only_continuations_are_refused():
    packet = build_continuation_packet(
        scope="conversation",
        reason="budget_exhausted",
        summary="we should keep going",
        remaining_work=[],
        evidence=[],
        goal_boundary={"type": "until_satisfied", "closure_criteria": ""},
        limits={"refresh_count": 0, "max_refreshes_per_task": 3, "global_refresh_count": 0, "global_hard_cap": 5},
    )

    decision = evaluate_continuation_eligibility(packet, goal_boundary_enabled=True)

    assert decision["decision"] == "refuse"
    assert "open_ended_goal_boundary" in decision["reasons"]
    assert "missing_remaining_work" in decision["reasons"]
    assert "missing_evidence" in decision["reasons"]


def test_clean_session_handoff_requires_valid_packet_and_inherits_limits_not_summary_authority():
    packet = build_continuation_packet(
        scope="conversation",
        reason="max_iterations_reached(3/3)",
        summary="natural language summary is advisory",
        session_id="old-session",
        task_id="task-1",
        remaining_work=["run acceptance command"],
        evidence=[{"id": "E-1", "path": "proof.json"}],
        completed_work=[{"id": "C-1", "evidence_id": "E-1"}],
        goal_boundary={"type": "acceptance_commands", "commands": ["pytest"], "closure_criteria": "all_pass"},
        side_effect_ledger=[{"key": "comment:lark-1", "status": "done"}],
        limits={"refresh_count": 2, "max_refreshes_per_task": 3, "global_refresh_count": 2, "global_hard_cap": 5},
        metadata={"compression_attempts": 3},
    )

    handoff = build_clean_session_handoff(packet, compression_cap=3)
    payload = handoff.to_dict()

    assert payload["scope"] == "clean_session_handoff"
    assert payload["parent_packet_id"] == packet.packet_id
    assert payload["remaining_work"] == ["run acceptance command"]
    assert payload["evidence"] == [{"id": "E-1", "path": "proof.json"}]
    assert payload["metadata"]["handoff_summary"] == "natural language summary is advisory"
    assert payload["metadata"]["summary_authority"] == "non_authoritative"
    assert payload["side_effect_ledger"] == [{"key": "comment:lark-1", "status": "done"}]
    assert payload["limits"]["global_hard_cap"] == 5


def test_active_backlog_cap_archives_overflow_without_deleting_existing_packets(tmp_path):
    active_dir = tmp_path / "active"
    active_dir.mkdir()
    for idx in range(MAX_ACTIVE_CONTINUATIONS):
        (active_dir / f"existing-{idx}.json").write_text("{}")

    packet = build_continuation_packet(
        scope="conversation",
        reason="budget_exhausted",
        summary="overflow candidate",
    )

    path = write_continuation_packet(packet, root=tmp_path, state="active")
    payload = json.loads(path.read_text())

    assert path.parent == tmp_path / "archived"
    assert payload["metadata"]["overflow_archived"] is True
    assert len(list(active_dir.glob("*.json"))) == MAX_ACTIVE_CONTINUATIONS


def test_packet_filename_slugging_keeps_malicious_identifiers_under_root(tmp_path):
    packet = build_continuation_packet(
        scope="../../escape scope",
        reason="budget_exhausted",
        summary="safe write",
        metadata={"note": "path-like identifiers are data only"},
    )
    payload = packet.to_dict()
    payload["packet_id"] = "../../evil"

    path = write_continuation_packet(payload, root=tmp_path, state="active")

    assert path.resolve().is_relative_to((tmp_path / "active").resolve())
    assert path.name == "escape_scope-evil.json"


def test_legacy_relay_packet_sanitizes_payload_derived_relay_index(tmp_path):
    packet = build_continuation_packet(
        scope="conversation",
        reason="budget_exhausted",
        summary="safe legacy relay write",
        session_id="session/with spaces",
        limits={"relay_index": "../../evil/absolute"},
    )

    path = write_legacy_relay_packet(packet, legacy_dir=tmp_path)

    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert path.name == "relay-session_with_spaces-evil_absolute.json"
