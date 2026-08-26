"""Fresh-session executor for durable task-runtime slices.

A worker receives exactly one immutable ContextPacket, runs in a newly-created
AIAgent session, and may only return a structured WorkerResult proposal.  The
control-plane store validates and records the proposal; integration remains a
separate parent/integrator action.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import threading
import uuid
from typing import Any, Callable

from agent.task_runtime import TaskRuntimeStore

try:
    from tools.delegate_tool import _get_max_concurrent_children

    _POOL_SIZE = max(1, int(_get_max_concurrent_children()))
except Exception:
    _POOL_SIZE = 3

_WORKER_SLOTS = threading.BoundedSemaphore(_POOL_SIZE)


class WorkerOutputError(ValueError):
    pass


def _worker_prompt(packet: dict[str, Any]) -> str:
    packet_json = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Execute only the immutable task slice in the ContextPacket below. "
        "Do not modify paths outside owned_paths and do not perform authority above "
        "the packet ceiling. Do not claim the whole task is complete. Your final "
        "response MUST be one JSON object and no prose, with fields: slice_id, "
        "input_digest, claims, changed_artifacts, evidence_refs, verification_results, "
        "risks, unknowns, state_delta_proposal, candidate_digest.\n\n"
        f"CONTEXT_PACKET_JSON={packet_json}"
    )


def _parse_worker_result(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, str) or not summary.strip():
        raise WorkerOutputError("worker_result_not_json")
    text = summary.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise WorkerOutputError("worker_result_not_json") from exc
    if not isinstance(payload, dict):
        raise WorkerOutputError("worker_result_not_object")
    return payload


def _git_paths(workspace: str, *args: str) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", workspace, *args],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerOutputError("workspace_git_readback_failed") from exc
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    }


def _workspace_changed_paths(workspace: str) -> set[str]:
    tracked = _git_paths(workspace, "diff", "--name-only", "-z", "HEAD", "--")
    untracked = _git_paths(workspace, "ls-files", "--others", "--exclude-standard", "-z", "--")
    return tracked | untracked


def _workspace_head(workspace: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", workspace, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerOutputError("workspace_base_commit_readback_failed") from exc


def _validated_workspace(packet: dict[str, Any]) -> str:
    """Return an isolated linked-worktree path or fail closed for write slices."""
    owned_paths = list(packet.get("owned_paths") or [])
    workspace_raw = str(packet.get("workspace_path") or "").strip()
    if not owned_paths and not workspace_raw:
        return ""
    if not workspace_raw:
        raise WorkerOutputError("isolated_workspace_required")
    workspace = Path(workspace_raw).expanduser()
    if not workspace.is_absolute() or not workspace.is_dir():
        raise WorkerOutputError("isolated_workspace_invalid")
    workspace = workspace.resolve()
    if not (workspace / ".git").is_file():
        raise WorkerOutputError("linked_git_worktree_required")
    try:
        top_level = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerOutputError("linked_git_worktree_invalid") from exc
    if Path(top_level).resolve() != workspace:
        raise WorkerOutputError("linked_git_worktree_invalid")
    expected_base = str(packet.get("workspace_base_commit") or "").strip()
    if not expected_base:
        raise WorkerOutputError("workspace_base_commit_required")
    actual_base = _workspace_head(str(workspace))
    if actual_base != expected_base:
        raise WorkerOutputError("workspace_base_commit_mismatch")
    for owned_path in owned_paths:
        candidate = (workspace / str(owned_path)).resolve()
        if candidate != workspace and workspace not in candidate.parents:
            raise WorkerOutputError("owned_path_outside_workspace")
    if _workspace_changed_paths(str(workspace)):
        raise WorkerOutputError("isolated_workspace_not_clean")
    return str(workspace)


def _default_child_builder(*, worker_session_id: str, packet: dict[str, Any], parent_agent: Any):
    from tools.delegate_tool import _build_child_agent

    workspace_path = _validated_workspace(packet)
    return _build_child_agent(
        task_index=0,
        goal=str(packet.get("objective") or "execute task slice"),
        context=_worker_prompt(packet),
        toolsets=getattr(parent_agent, "enabled_toolsets", None),
        model=None,
        max_iterations=50,
        task_count=1,
        parent_agent=parent_agent,
        role="leaf",
        forced_session_id=worker_session_id,
        forced_workspace_path=workspace_path or None,
    )


def _default_child_runner(*, child: Any, goal: str, parent_agent: Any) -> dict[str, Any]:
    from tools.delegate_tool import _run_single_child

    return _run_single_child(0, goal, child, parent_agent)


def dispatch_next_slice(
    parent_agent: Any,
    *,
    task_id: str,
    lease_seconds: int = 900,
    store: TaskRuntimeStore | None = None,
    child_builder: Callable[..., Any] | None = None,
    child_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Claim one ready slice, execute it in a fresh bounded worker, submit result."""
    runtime_store = store or TaskRuntimeStore(TaskRuntimeStore.default_path())
    worker_session_id = f"task-worker-{uuid.uuid4().hex}"
    packet = runtime_store.claim_next_slice(
        task_id,
        worker_session_id=worker_session_id,
        lease_seconds=int(lease_seconds),
    )
    if packet is None:
        return {"success": False, "error": "no_ready_slice", "task_id": task_id}

    builder = child_builder or _default_child_builder
    runner = child_runner or _default_child_runner
    execution: dict[str, Any] | None = None
    runner_started = False
    try:
        child = builder(
            worker_session_id=worker_session_id,
            packet=packet,
            parent_agent=parent_agent,
        )
        actual_session_id = str(getattr(child, "session_id", "") or "")
        if actual_session_id != worker_session_id:
            raise WorkerOutputError("fresh_worker_session_identity_mismatch")

        goal = _worker_prompt(packet)
        with _WORKER_SLOTS:
            runner_started = True
            execution = runner(child=child, goal=goal, parent_agent=parent_agent)
        execution_status = str(execution.get("status") or "").strip().lower()
        if execution_status == "timeout":
            raise WorkerOutputError("worker_timeout")
        if execution_status in {"cancelled", "canceled"}:
            raise WorkerOutputError("worker_cancelled")
        if execution_status in {"error", "failed", "failure"}:
            raise WorkerOutputError("worker_execution_failed")
        worker_result = _parse_worker_result(execution.get("summary"))
        if child_builder is None and packet.get("workspace_path"):
            actual_base = _workspace_head(str(packet["workspace_path"]))
            expected_base = str(packet.get("workspace_base_commit") or "")
            actual_paths = _workspace_changed_paths(str(packet["workspace_path"]))
            declared_paths = {str(item) for item in worker_result.get("changed_artifacts") or []}
            execution["workspace_readback"] = {
                "workspace_path": str(packet["workspace_path"]),
                "expected_base_commit": expected_base,
                "actual_base_commit": actual_base,
                "actual_changed_paths": sorted(actual_paths),
                "declared_changed_paths": sorted(declared_paths),
            }
            if actual_base != expected_base:
                raise WorkerOutputError("worker_workspace_base_changed")
            if actual_paths != declared_paths:
                raise WorkerOutputError("worker_write_set_mismatch")
    except WorkerOutputError as exc:
        error = str(exc)
        termination_proven = bool(
            execution and execution.get("worker_terminated") is True
        )
        if error == "worker_timeout" and not termination_proven:
            claim_receipt = runtime_store.quarantine_claim(
                task_id,
                worker_session_id=worker_session_id,
                packet_id=packet["packet_id"],
                reason="worker_timeout_termination_unproven",
            )
        else:
            claim_receipt = runtime_store.release_claim(
                task_id,
                worker_session_id=worker_session_id,
                packet_id=packet["packet_id"],
                reason=error,
            )
        return {
            "success": False,
            "error": error,
            "task_id": task_id,
            "worker_session_id": worker_session_id,
            "context_packet": packet,
            "worker_execution": execution,
            "claim_receipt": claim_receipt,
        }
    except KeyboardInterrupt:
        if runner_started:
            runtime_store.quarantine_claim(
                task_id,
                worker_session_id=worker_session_id,
                packet_id=packet["packet_id"],
                reason="worker_interrupted_termination_unproven",
            )
        else:
            runtime_store.release_claim(
                task_id,
                worker_session_id=worker_session_id,
                packet_id=packet["packet_id"],
                reason="worker_interrupted_before_start",
            )
        raise
    except Exception as exc:
        if runner_started:
            claim_receipt = runtime_store.quarantine_claim(
                task_id,
                worker_session_id=worker_session_id,
                packet_id=packet["packet_id"],
                reason=f"worker_execution_termination_unproven:{type(exc).__name__}",
            )
        else:
            claim_receipt = runtime_store.release_claim(
                task_id,
                worker_session_id=worker_session_id,
                packet_id=packet["packet_id"],
                reason=f"worker_build_failed:{type(exc).__name__}",
            )
        return {
            "success": False,
            "error": "worker_execution_failed",
            "error_type": type(exc).__name__,
            "task_id": task_id,
            "worker_session_id": worker_session_id,
            "context_packet": packet,
            "worker_execution": execution,
            "claim_receipt": claim_receipt,
        }

    receipt = runtime_store.submit_result(
        task_id,
        worker_session_id=worker_session_id,
        packet_id=packet["packet_id"],
        result=worker_result,
    )
    return {
        "success": receipt.get("disposition") == "accepted_for_integration",
        "task_id": task_id,
        "worker_session_id": worker_session_id,
        "context_packet": packet,
        "worker_execution": execution,
        "worker_result": receipt,
    }
