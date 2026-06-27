"""Goal/workflow stop-review packet compiler.

Default-off helper inspired by bounded Stop Hook review workflows. This module does
not invoke external reviewers or block process exit by itself; it only compiles a
structured packet that callers can persist, inspect, or later wire into a
separately-authorized hook.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

_GOAL_MARKERS = ("/goal", "yd-ai-wf")
_REVIEW_COMMAND = ["codex", "review", "--json", "--wait", "--scope", "working-tree"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_goal_workflow(transcript: str) -> bool:
    text = (transcript or "").lower()
    return any(marker in text for marker in _GOAL_MARKERS)


def _normalize_changed_files(changed_files: Iterable[str] | None) -> list[str]:
    return [str(path) for path in (changed_files or []) if str(path)]


def compile_goal_stop_review_packet(
    *,
    transcript: str,
    worktree: str | Path,
    changed_files: Iterable[str] | None,
    review_status: str | None = None,
    review_error: str | None = None,
    p0_findings: Iterable[Mapping[str, Any]] | None = None,
    review_artifact_dir: str = "codex-review",
    fast_hook_timeout_seconds: int = 5,
    review_timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Compile a default-off stop review packet for goal workflow runs.

    Decision semantics:
    - non-goal transcript: allow silently;
    - goal transcript + clean worktree: allow;
    - dirty worktree: request bounded review packet;
    - review infrastructure error: fail open with error evidence;
    - P0 findings: block, scoped to p0-only repair.
    """

    worktree_path = Path(worktree).expanduser().resolve()
    changed = _normalize_changed_files(changed_files)
    findings = [dict(item) for item in (p0_findings or [])]

    packet: dict[str, Any] = {
        "schema": "goal_stop_review_packet_v1",
        "created_at": _utc_now(),
        "worktree": str(worktree_path),
        "is_goal_workflow": _is_goal_workflow(transcript),
        "changed_files": changed,
        "fast_hook_timeout_seconds": int(fast_hook_timeout_seconds),
        "review_timeout_seconds": int(review_timeout_seconds),
        "review_command": list(_REVIEW_COMMAND),
        "review_artifact_dir": str(worktree_path / review_artifact_dir),
        "review_requested": False,
        "review_status": review_status or "not_run",
        "review_error": review_error or "",
        "p0_findings": findings,
        "blocking": False,
        "external_side_effects": [],
    }

    if not packet["is_goal_workflow"]:
        packet.update({"decision": "allow", "reason": "not_goal_workflow"})
        return packet

    if not changed:
        packet.update({"decision": "allow", "reason": "clean_worktree"})
        return packet

    packet["review_requested"] = True

    if review_status == "error":
        packet.update({"decision": "allow", "reason": "review_error_fail_open"})
        return packet

    if review_status == "p0_found" or findings:
        packet.update(
            {
                "decision": "block",
                "reason": "p0_findings",
                "blocking": True,
                "fix_boundary": "p0_only",
            }
        )
        return packet

    packet.update({"decision": "review_requested", "reason": "goal_workflow_dirty_worktree"})
    return packet
