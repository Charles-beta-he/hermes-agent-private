"""Agent Workbench local projection helpers.

These helpers intentionally build inert, reviewable data structures only.  They
model Superset-style workbench task state and review packets without activating
runtime routing, invoking tools, mutating gateway/scheduler/taskpool lifecycle,
or sending anything externally.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TASK_STATE_SCHEMA = "AgentWorkbenchTaskStateProjection.v0"
REVIEW_PACKET_SCHEMA = "AgentWorkbenchReviewPacketTemplate.v0"
_ALLOWED_TASK_STATUSES = {
    "pending",
    "in_progress",
    "blocked_by_authority",
    "ready_for_review",
    "local_verified",
    "landed_unverified",
}
_ALLOWED_LOCAL_EFFECTS = {"local_source_write", "local_test", "local_doc_write"}
_BASELINE_HEX_LENGTHS = {7, 8, 12, 40, 64}


@dataclass(frozen=True)
class TaskStateProjectionError(ValueError):
    """Fail-closed error for invalid task-state projection input."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class ReviewPacketTemplateError(ValueError):
    """Fail-closed error for invalid review-packet template input."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _stable_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_nonempty_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskStateProjectionError("invalid_task_field", f"{key} must be a non-empty string")
    return value.strip()


def _validate_baseline(value: str) -> None:
    if len(value) not in _BASELINE_HEX_LENGTHS or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise TaskStateProjectionError("invalid_baseline_commit", "baseline_commit must be a hex git object digest")


def _normalize_string_list(item: dict[str, Any], key: str) -> list[str]:
    values = item.get(key)
    if not isinstance(values, list) or not values:
        raise TaskStateProjectionError("invalid_task_field", f"{key} must be a non-empty list")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise TaskStateProjectionError("invalid_task_field", f"{key} entries must be non-empty strings")
        normalized.append(value.strip())
    return sorted(dict.fromkeys(normalized))


def _normalize_task(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise TaskStateProjectionError("invalid_task", "task entries must be objects")
    task_id = _require_nonempty_string(item, "task_id")
    title = _require_nonempty_string(item, "title")
    status = _require_nonempty_string(item, "status")
    if status not in _ALLOWED_TASK_STATUSES:
        raise TaskStateProjectionError("invalid_task_status", f"unsupported task status: {status}")
    worktree = _require_nonempty_string(item, "worktree")
    branch = _require_nonempty_string(item, "branch")
    baseline_commit = _require_nonempty_string(item, "baseline_commit")
    _validate_baseline(baseline_commit)
    evidence_refs = _normalize_string_list(item, "evidence_refs")
    allowed_effects = _normalize_string_list(item, "allowed_effects")
    disallowed = sorted(set(allowed_effects) - _ALLOWED_LOCAL_EFFECTS)
    if disallowed:
        raise TaskStateProjectionError("high_side_effect_not_allowed", f"disallowed effects: {', '.join(disallowed)}")
    return {
        "task_id": task_id,
        "title": title,
        "status": status,
        "worktree": worktree,
        "branch": branch,
        "baseline_commit": baseline_commit,
        "evidence_refs": evidence_refs,
        "allowed_effects": allowed_effects,
        "runtime_activation": False,
        "would_execute": False,
    }


def build_task_state_projection(tasks: list[dict[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    """Build an inert Agent Workbench task-state projection.

    The projection is a local artifact/template: it does not read or write the
    worktree, start agents, mutate taskpool state, or activate routing.  Invalid
    tasks fail closed instead of being silently coerced into reviewable state.
    """
    if not isinstance(tasks, list) or not tasks:
        raise TaskStateProjectionError("invalid_tasks", "tasks must be a non-empty list")
    normalized = sorted((_normalize_task(item) for item in tasks), key=lambda task: task["task_id"])
    by_status: dict[str, int] = {}
    for task in normalized:
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
    return {
        "schema": TASK_STATE_SCHEMA,
        "mode": "local_projection_only",
        "generated_at": generated_at or _now_iso(),
        "runtime_activation": False,
        "would_execute": False,
        "external_side_effects_executed": False,
        "gateway_scheduler_taskpool_lifecycle_mutation": False,
        "tasks": normalized,
        "summary": {
            "task_count": len(normalized),
            "by_status": dict(sorted(by_status.items())),
            "all_tasks_bound_to_worktree": all(bool(task["worktree"]) for task in normalized),
            "all_tasks_have_baseline": all(bool(task["baseline_commit"]) for task in normalized),
            "all_tasks_have_evidence": all(bool(task["evidence_refs"]) for task in normalized),
        },
    }


def build_review_packet_template(
    task_projection: dict[str, Any],
    *,
    candidate_digest: str,
    changed_files: list[str],
    required_verification: list[str],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a review-packet template bound to a local task-state projection."""
    if not isinstance(task_projection, dict) or task_projection.get("schema") != TASK_STATE_SCHEMA:
        raise ReviewPacketTemplateError("invalid_task_projection", "expected AgentWorkbenchTaskStateProjection.v0")
    if task_projection.get("ok") is False or not isinstance(task_projection.get("tasks"), list):
        raise ReviewPacketTemplateError("invalid_task_projection", "projection must contain successful task entries")
    if task_projection.get("runtime_activation") is not False or task_projection.get("would_execute") is not False:
        raise ReviewPacketTemplateError("task_projection_runtime_gate_open", "projection must remain inert")
    if (
        task_projection.get("external_side_effects_executed") is not False
        or task_projection.get("gateway_scheduler_taskpool_lifecycle_mutation") is not False
    ):
        raise ReviewPacketTemplateError("task_projection_runtime_gate_open", "projection must not record runtime or external side effects")
    for task in task_projection["tasks"]:
        if not isinstance(task, dict):
            raise ReviewPacketTemplateError("invalid_task_projection", "projection tasks must be objects")
        if task.get("runtime_activation") is not False or task.get("would_execute") is not False:
            raise ReviewPacketTemplateError("task_runtime_gate_open", "projection task must remain inert")
    digest_value = candidate_digest[len("sha256:"):] if isinstance(candidate_digest, str) and candidate_digest.startswith("sha256:") else ""
    if len(digest_value) not in {64} or any(ch not in "0123456789abcdef" for ch in digest_value.lower()):
        raise ReviewPacketTemplateError("invalid_candidate_digest", "candidate_digest must be sha256:<64 hex>")
    if not changed_files or not all(isinstance(path, str) and path.strip() for path in changed_files):
        raise ReviewPacketTemplateError("invalid_changed_files", "changed_files must be a non-empty list of paths")
    if not required_verification or not all(isinstance(cmd, str) and cmd.strip() for cmd in required_verification):
        raise ReviewPacketTemplateError("invalid_required_verification", "required_verification must be a non-empty list")
    negative_boundaries = {
        "runtime_activation": False,
        "would_execute": False,
        "gateway_scheduler_taskpool_lifecycle_mutation": False,
        "external_dispatch": False,
        "sudo": False,
        "push_deploy_release": False,
    }
    return {
        "schema": REVIEW_PACKET_SCHEMA,
        "mode": "local_review_template_only",
        "generated_at": generated_at or _now_iso(),
        "status": "READY_FOR_LOCAL_REVIEW",
        "candidate_digest": candidate_digest,
        "task_projection_digest": _stable_sha256(task_projection),
        "changed_files": sorted(dict.fromkeys(path.strip() for path in changed_files)),
        "required_verification": [cmd.strip() for cmd in required_verification],
        "negative_boundaries": negative_boundaries,
        "review_questions": [
            {
                "id": "candidate_identity_bound",
                "question": "Does the candidate digest match the exact changed files and baseline under review?",
                "required_evidence": ["git diff --name-only", "per-file sha256 or candidate digest"],
            },
            {
                "id": "runtime_gates_closed",
                "question": "Do runtime_activation and would_execute remain false with no gateway/scheduler/taskpool lifecycle mutation?",
                "required_evidence": ["unit tests", "hermes agent-os status --json readback"],
            },
            {
                "id": "verification_current_slice",
                "question": "Did the required local verification commands run on the exact candidate after cleanup?",
                "required_evidence": ["fresh command output", "clean scoped git status"],
            },
        ],
        "runtime_activation": False,
        "would_execute": False,
        "external_side_effects_executed": False,
    }


def load_tasks_from_file(path: str | Path) -> list[dict[str, Any]]:
    """Load task-state input from JSON list or {"tasks": [...]} object."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(tasks, list):
        raise TaskStateProjectionError("invalid_tasks", "input must be a task list or object with tasks")
    return tasks


def load_projection_from_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReviewPacketTemplateError("invalid_task_projection", "projection input must be an object")
    return payload
