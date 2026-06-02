import json

from agent.abstraction_governance import (
    classify_durable_write_path,
    review_durable_write,
    review_filesystem_tool_call,
    review_memory_write,
    review_skill_mutation,
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


def test_skill_gate_blocks_session_progress_in_skill_content():
    content = """---
name: bad-progress
description: Bad skill with task progress.
---

# Bad Progress

PR #789 was merged today and phase 2 is done in this session.
"""

    decision = review_skill_mutation(action="create", name="bad-progress", content=content)

    assert decision.ok is False
    assert decision.reason == "task_progress_not_skill"
    assert decision.recommended_landing_zone == "session_log_or_workpad"


def test_skill_gate_blocks_project_specific_entities_without_scope_marker():
    content = """---
name: uvisa-branch-rule
description: Badly globalized project rule.
---

# Rule

Always treat feat/uvisa-2.0 as the integration branch before merge.
"""

    decision = review_skill_mutation(action="create", name="uvisa-branch-rule", content=content)

    assert decision.ok is False
    assert decision.reason == "context_coupled_skill"
    assert decision.recommended_landing_zone == "project_profile_or_scoped_reference"


def test_skill_gate_allows_scoped_project_reference():
    content = """---
name: uvisa-reference
description: Scoped reference for a project-specific branch convention.
---

# UVISA Reference

Scope: project uvisa-app-2.0 only.
Project fact: feat/uvisa-2.0 is the integration branch.
Anti-conditions: do not apply to other repositories.
"""

    decision = review_skill_mutation(action="write_file", name="uvisa-reference", content=content)

    assert decision.ok is True
    assert decision.reason == "accepted"


def test_durable_write_routes_skill_and_memory_reviewers():
    memory_decision = review_durable_write(
        kind="memory",
        target="memory",
        content="PR #123 was closed and this task is complete.",
    )
    skill_decision = review_durable_write(
        kind="skill",
        target="bad-skill",
        action="create",
        content="PR #123 was closed and this task is complete.",
    )

    assert memory_decision.reason == "task_progress_not_memory"
    assert skill_decision.reason == "task_progress_not_skill"


def test_durable_write_blocks_task_progress_in_profile_adr_and_experience():
    for kind in ("project_profile", "adr", "experience"):
        decision = review_durable_write(
            kind=kind,
            target="target",
            content="PR #123 was merged and this session completed phase 2.",
        )

        assert decision.ok is False
        assert decision.reason == f"task_progress_not_{kind}"
        assert decision.recommended_landing_zone == "session_log_or_workpad"


def test_durable_write_blocks_context_coupled_adr_without_scope_marker():
    decision = review_durable_write(
        kind="adr",
        target="docs/adr/branching.md",
        content="Always use feat/uvisa-2.0 as integration branch.",
    )

    assert decision.ok is False
    assert decision.reason == "context_coupled_adr"


def test_durable_write_allows_scoped_adr_with_project_entities():
    decision = review_durable_write(
        kind="adr",
        target="docs/adr/branching.md",
        content="""Scope: project uvisa-app-2.0 only.
Project fact: feat/uvisa-2.0 is the integration branch.
Anti-conditions: do not apply outside this repository.
""",
    )

    assert decision.ok is True
    assert decision.reason == "accepted"


def test_classify_durable_write_path_recognizes_governance_surfaces(tmp_path):
    assert classify_durable_write_path(
        str(tmp_path / "project-profiles" / "demo.yaml"),
        hermes_home=tmp_path,
    ) == "project_profile"
    assert classify_durable_write_path(
        str(tmp_path / "docs" / "adrs" / "branching.md"),
        hermes_home=tmp_path,
    ) == "adr"
    assert classify_durable_write_path(
        str(tmp_path / "experience-ratchet" / "atoms" / "one.md"),
        hermes_home=tmp_path,
    ) == "experience"
    assert classify_durable_write_path(
        str(tmp_path / "ordinary-project" / "note.md"),
        hermes_home=tmp_path,
    ) is None


def test_mcp_filesystem_write_blocks_task_progress_in_project_profile(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    decision = review_filesystem_tool_call(
        tool_name="mcp_filesystem_write_file",
        args={
            "path": str(tmp_path / "project-profiles" / "demo.json"),
            "content": '{"note":"PR #123 was merged and this task is complete"}',
        },
    )

    assert decision.ok is False
    assert decision.reason == "task_progress_not_project_profile"


def test_mcp_filesystem_edit_blocks_context_coupled_adr(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    decision = review_filesystem_tool_call(
        tool_name="mcp_filesystem_edit_file",
        args={
            "path": str(tmp_path / "docs" / "adr" / "branching.md"),
            "edits": [
                {
                    "oldText": "# Branching\n",
                    "newText": "# Branching\nAlways use feat/uvisa-2.0 as integration branch.\n",
                }
            ],
        },
    )

    assert decision.ok is False
    assert decision.reason == "context_coupled_adr"


def test_mcp_filesystem_move_into_governed_surface_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path)
    decision = review_filesystem_tool_call(
        tool_name="mcp_filesystem_move_file",
        args={
            "source": str(tmp_path / "scratch.json"),
            "destination": str(tmp_path / "project-profiles" / "demo.json"),
        },
    )

    assert decision.ok is False
    assert decision.reason == "opaque_move_into_project_profile"


def test_mcp_filesystem_write_ignores_ordinary_project_files(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: tmp_path / "hermes-home")
    decision = review_filesystem_tool_call(
        tool_name="mcp_filesystem_write_file",
        args={"path": str(tmp_path / "ordinary.md"), "content": "PR #123 was merged"},
    )

    assert decision.ok is True
    assert decision.reason == "unclassified_path"
