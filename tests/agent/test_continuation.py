import json

from agent.continuation import (
    MAX_ACTIVE_CONTINUATIONS,
    ContinuationPacket,
    build_authority_grant,
    build_continuation_packet,
    build_clean_session_handoff,
    build_decision_packet,
    continuation_status,
    evaluate_continuation_eligibility,
    evaluate_decision_resume,
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
        recurrent_task_state={
            "schema": "hermes_recurrent_task_state_v1",
            "task_id": "task-1",
            "next_bounded_action": "run acceptance command",
        },
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
    assert payload["recurrent_task_state"]["schema"] == "hermes_recurrent_task_state_v1"
    assert payload["recurrent_task_state"]["next_bounded_action"] == "run acceptance command"
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


def test_decision_packet_holds_hard_gate_even_with_model_consensus():
    continuation = build_continuation_packet(
        scope="conversation",
        reason="external_publish_gate",
        summary="Local verification is complete; publish requires human authority.",
        remaining_work=[{"id": "publish", "action": "push_release"}],
        completed_work=[{"id": "test", "result": "passed"}],
        evidence=[{"id": "E-test", "kind": "pytest", "status": "passed"}],
        resume={"mode": "after_authority", "executor": "conversation_loop"},
    )
    packet = build_decision_packet(
        objective="Ship the verified local change without bypassing publish authority.",
        blocker_class="external_publish",
        question="Authorize publishing the verified local change?",
        options=[{"id": "A", "action": "publish"}, {"id": "B", "action": "stop"}],
        recommendation={"option": "A", "reason": "Local evidence is green"},
        continuation=continuation,
        required_authority={"actions": ["push_release"], "scope": "current task"},
        evidence=[{"id": "E-models", "kind": "multi_model_review", "vote": "approve"}],
        model_reviews=[{"model": "planner", "recommendation": "publish"}],
    )

    decision = evaluate_decision_resume(packet)

    assert packet["schema"] == "hermes_decision_packet_v1"
    assert packet["blocker"]["requires_human"] is True
    assert decision["decision"] == "hold_for_human"
    assert decision["hard_human_gate"] is True
    assert "missing_authority_grant" in decision["reasons"]
    assert decision["model_consensus_authority"] == "advisory_only"


def test_decision_packet_resumes_hard_gate_only_with_matching_authority():
    continuation = build_continuation_packet(
        scope="conversation",
        reason="production_gate",
        summary="Ready to resume after scoped authority.",
        remaining_work=["create PR"],
        evidence=[{"id": "E-1", "status": "passed"}],
    )
    packet = build_decision_packet(
        objective="Resume only within granted scope.",
        blocker_class="production",
        question="Allow create_pr only?",
        options=[{"id": "A", "action": "create_pr"}],
        continuation=continuation,
        required_authority={"actions": ["create_pr"]},
        evidence=[{"id": "E-1", "status": "passed"}],
    )
    mismatched_grant = build_authority_grant(
        decision_packet_id="other-packet",
        scope="current task",
        allowed_actions=["create_pr"],
    )
    assert evaluate_decision_resume(packet, authority_grant=mismatched_grant)["decision"] == "hold_for_human"

    grant = build_authority_grant(
        decision_packet_id=packet["packet_id"],
        scope="current task",
        allowed_actions=["create_pr"],
        forbidden_actions=["deploy"],
        evidence=[{"message_id": "human-approval"}],
    )
    decision = evaluate_decision_resume(packet, authority_grant=grant)

    assert grant["schema"] == "hermes_authority_grant_v1"
    assert decision["decision"] == "resume_with_authority"
    assert decision["reasons"] == []


def test_decision_packet_auto_resumes_low_risk_verified_gate():
    continuation = build_continuation_packet(
        scope="conversation",
        reason="verification_followup",
        summary="Continue local verification.",
        remaining_work=["run focused pytest"],
        evidence=[{"id": "E-read", "kind": "readback"}],
    )
    packet = build_decision_packet(
        objective="Finish local reversible verification.",
        blocker_class="local_reversible",
        question="Continue local verification?",
        options=[{"id": "A", "action": "run_pytest"}],
        continuation=continuation,
        evidence=[{"id": "E-read", "kind": "readback"}],
    )

    decision = evaluate_decision_resume(packet)

    assert packet["blocker"]["requires_human"] is False
    assert decision["decision"] == "auto_resume"
    assert decision["hard_human_gate"] is False

