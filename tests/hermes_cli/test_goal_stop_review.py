from __future__ import annotations

from pathlib import Path

from hermes_cli.goal_stop_review import compile_goal_stop_review_packet


def test_non_goal_transcript_allows_without_git_review(tmp_path: Path) -> None:
    packet = compile_goal_stop_review_packet(
        transcript="ordinary chat about weather",
        worktree=tmp_path,
        changed_files=["foo.py"],
    )

    assert packet["schema"] == "goal_stop_review_packet_v1"
    assert packet["decision"] == "allow"
    assert packet["reason"] == "not_goal_workflow"
    assert packet["review_requested"] is False
    assert packet["blocking"] is False


def test_goal_transcript_clean_tree_allows_without_review(tmp_path: Path) -> None:
    packet = compile_goal_stop_review_packet(
        transcript="/goal workflow execute",
        worktree=tmp_path,
        changed_files=[],
    )

    assert packet["decision"] == "allow"
    assert packet["reason"] == "clean_worktree"
    assert packet["review_requested"] is False
    assert packet["changed_files"] == []


def test_goal_transcript_dirty_tree_requests_bounded_review_packet(tmp_path: Path) -> None:
    packet = compile_goal_stop_review_packet(
        transcript="yd-ai-wf(projectDir, feature)",
        worktree=tmp_path,
        changed_files=["hermes_cli/goals.py", "tests/hermes_cli/test_goals.py"],
        review_artifact_dir="codex-review",
    )

    assert packet["decision"] == "review_requested"
    assert packet["reason"] == "goal_workflow_dirty_worktree"
    assert packet["review_requested"] is True
    assert packet["review_command"] == [
        "codex",
        "review",
        "--json",
        "--wait",
        "--scope",
        "working-tree",
    ]
    assert packet["review_timeout_seconds"] == 900
    assert packet["fast_hook_timeout_seconds"] == 5
    assert packet["review_artifact_dir"] == str(tmp_path / "codex-review")
    assert packet["changed_files"] == ["hermes_cli/goals.py", "tests/hermes_cli/test_goals.py"]
    assert packet["blocking"] is False


def test_review_error_allows_but_records_error(tmp_path: Path) -> None:
    packet = compile_goal_stop_review_packet(
        transcript="/goal workflow execute",
        worktree=tmp_path,
        changed_files=["hermes_cli/goals.py"],
        review_status="error",
        review_error="codex unavailable",
    )

    assert packet["decision"] == "allow"
    assert packet["reason"] == "review_error_fail_open"
    assert packet["review_requested"] is True
    assert packet["review_status"] == "error"
    assert packet["review_error"] == "codex unavailable"
    assert packet["blocking"] is False


def test_p0_findings_block_with_exact_fix_boundary(tmp_path: Path) -> None:
    packet = compile_goal_stop_review_packet(
        transcript="/goal workflow execute",
        worktree=tmp_path,
        changed_files=["hermes_cli/goals.py"],
        review_status="p0_found",
        p0_findings=[{"id": "P0-1", "file": "hermes_cli/goals.py", "summary": "critical lifecycle bypass"}],
    )

    assert packet["decision"] == "block"
    assert packet["reason"] == "p0_findings"
    assert packet["blocking"] is True
    assert packet["fix_boundary"] == "p0_only"
    assert packet["p0_findings"][0]["id"] == "P0-1"
