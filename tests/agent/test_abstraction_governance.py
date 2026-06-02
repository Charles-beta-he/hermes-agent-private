import json

from agent.abstraction_governance import (
    review_memory_write,
    should_apply_abstraction_gate,
)


def test_memory_gate_blocks_task_progress_from_durable_memory():
    decision = review_memory_write(
        target="memory",
        content="PR #123 fixed and submitted; phase 2 is done in this session.",
    )

    assert decision.ok is False
    assert decision.reason == "task_progress_not_memory"
    assert decision.recommended_landing_zone == "session_log_or_workpad"
    assert "abstraction-governance" in decision.required_skill


def test_memory_gate_blocks_procedure_from_memory_and_recommends_skill():
    decision = review_memory_write(
        target="memory",
        content="Workflow: run pytest, inspect branch roles, then write the closure checklist.",
    )

    assert decision.ok is False
    assert decision.reason == "procedure_belongs_in_skill"
    assert decision.recommended_landing_zone == "skill_or_experience_atom"


def test_memory_gate_allows_stable_user_preference():
    decision = review_memory_write(
        target="user",
        content="User prefers explicit abstraction-level comments when discussing agent norms.",
    )

    assert decision.ok is True
    assert decision.reason == "accepted"


def test_memory_gate_allows_project_fact_when_scoped_declaratively():
    decision = review_memory_write(
        target="memory",
        content="Project arc-dispatch uses pnpm workspaces and Playwright for browser verification.",
    )

    assert decision.ok is True
    assert decision.reason == "accepted"


def test_gate_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("HERMES_ABSTRACTION_GOVERNANCE_GATE", "0")

    assert should_apply_abstraction_gate() is False
