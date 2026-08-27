"""Fresh-session executor for durable task-runtime slices.

A worker receives exactly one immutable ContextPacket, runs in a newly-created
AIAgent session, and may only return a structured WorkerResult proposal.  The
control-plane store validates and records the proposal; integration remains a
separate parent/integrator action.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import threading
import uuid
from typing import Any, Callable, Mapping

from agent.task_runtime import TaskRuntimeStore
from agent.task_runtime_workspace import (
    WorkspaceLease,
    allocate_workspace,
    freeze_workspace_candidate,
    reap_unstarted_workspace,
    reap_workspace,
    renew_workspace_lease,
    snapshot_workspace_candidate,
)
from agent.task_runtime_process import (
    TaskCleanupBinding,
    run_owned_task_process,
)

try:
    from tools.delegate_tool import _get_max_concurrent_children

    _POOL_SIZE = max(1, int(_get_max_concurrent_children()))
except Exception:
    _POOL_SIZE = 3

_WORKER_SLOTS = threading.BoundedSemaphore(_POOL_SIZE)


class WorkerOutputError(ValueError):
    pass


def _managed_workspace_root() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser().resolve()
    return hermes_home / "task-runtime-workspaces"


def _termination_proof(
    worker_session_id: str, packet: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "kind": "runner_not_started",
        "worker_session_id": worker_session_id,
        "packet_id": str(packet["packet_id"]),
        "lease_token": str(packet["lease"]["token"]),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _worker_prompt(packet: dict[str, Any]) -> str:
    canonical_packet = {
        key: value for key, value in packet.items() if not key.startswith("_execution_")
    }
    packet_json = json.dumps(canonical_packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
    workspace_raw = str(
        packet.get("_execution_workspace_path") or packet.get("workspace_path") or ""
    ).strip()
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
    _WORKER_SLOTS.acquire()
    slot_released = False

    def _finish(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal slot_released
        if not slot_released:
            _WORKER_SLOTS.release()
            slot_released = True
        return payload

    worker_session_id = f"task-worker-{uuid.uuid4().hex}"
    try:
        packet = runtime_store.claim_next_slice(
            task_id,
            worker_session_id=worker_session_id,
            lease_seconds=int(lease_seconds),
        )
    except BaseException:
        _finish({})
        raise
    if packet is None:
        return _finish({"success": False, "error": "no_ready_slice", "task_id": task_id})

    builder = child_builder or _default_child_builder
    runner = child_runner or _default_child_runner
    execution: dict[str, Any] | None = None
    execution_packet = dict(packet)
    runner_started = False
    workspace_lease: WorkspaceLease | None = None
    process_task_result = None
    process_cleanup = None
    workspace_candidate_receipt = None
    workspace_root = _managed_workspace_root()

    def _release_before_start(reason: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        cleanup_receipt = None
        if workspace_lease is not None:
            try:
                cleanup_receipt = reap_unstarted_workspace(
                    workspace_lease, managed_root=workspace_root
                ).to_dict()
            except Exception as cleanup_error:
                quarantined = runtime_store.quarantine_claim(
                    task_id,
                    worker_session_id=worker_session_id,
                    packet_id=packet["packet_id"],
                    reason=(
                        f"{reason}_workspace_cleanup_unproven:"
                        f"{type(cleanup_error).__name__}"
                    ),
                )
                return quarantined, None
        released = runtime_store.release_claim(
            task_id,
            worker_session_id=worker_session_id,
            packet_id=packet["packet_id"],
            reason=reason,
            termination_proof=_termination_proof(worker_session_id, packet),
        )
        return released, cleanup_receipt

    def _recover_after_controlled_stop(
        reason: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if process_cleanup is None:
            raise ValueError("verified_process_cleanup_required")
        quarantine = runtime_store.quarantine_claim(
            task_id,
            worker_session_id=worker_session_id,
            packet_id=packet["packet_id"],
            reason=f"{reason}_controlled_process_stopped",
        )
        workspace_receipt = None
        if workspace_lease is not None:
            workspace_receipt = reap_workspace(
                workspace_lease,
                managed_root=workspace_root,
                process_receipt=process_cleanup.receipt,
            ).to_dict()
        recovered = runtime_store.recover_quarantined_claim(
            task_id,
            cleanup=process_cleanup,
            workspace_reap_receipt=workspace_receipt,
        )
        recovered["quarantine_receipt"] = quarantine
        return recovered, workspace_receipt

    try:
        if (
            child_builder is None
            and packet.get("owned_paths")
            and not str(packet.get("workspace_path") or "").strip()
        ):
            source_repo = str(packet.get("workspace_source_repo") or "").strip()
            base_commit = str(packet.get("workspace_base_commit") or "").strip()
            if not source_repo:
                raise WorkerOutputError("workspace_source_repo_required")
            if not base_commit:
                raise WorkerOutputError("workspace_base_commit_required")
            workspace_lease = allocate_workspace(
                managed_root=workspace_root,
                source_repo=source_repo,
                task_id=task_id,
                slice_id=str(packet["slice_id"]),
                packet_id=str(packet["packet_id"]),
                lease_token=str(packet["lease"]["token"]),
                base_commit=base_commit,
                lease_seconds=int(lease_seconds),
            )
            execution_packet["_execution_workspace_path"] = workspace_lease.workspace_path
        goal = _worker_prompt(execution_packet)
        worker_command = packet.get("worker_command") or []
        child = None
        if not worker_command:
            child = builder(
                worker_session_id=worker_session_id,
                packet=execution_packet,
                parent_agent=parent_agent,
            )
            actual_session_id = str(getattr(child, "session_id", "") or "")
            if actual_session_id != worker_session_id:
                raise WorkerOutputError("fresh_worker_session_identity_mismatch")

        heartbeat_stop = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat_interval = max(0.1, min(30.0, float(lease_seconds) / 3.0))

        def _heartbeat_loop() -> None:
            nonlocal workspace_lease
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    runtime_store.heartbeat_claim(
                        task_id,
                        worker_session_id=worker_session_id,
                        packet_id=str(packet["packet_id"]),
                        lease_seconds=int(lease_seconds),
                    )
                    if workspace_lease is not None:
                        workspace_lease = renew_workspace_lease(
                            workspace_lease,
                            managed_root=workspace_root,
                            lease_seconds=int(lease_seconds),
                        )
                except Exception as heartbeat_error:
                    heartbeat_errors.append(heartbeat_error)
                    heartbeat_stop.set()

        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"task-heartbeat-{packet['packet_id']}",
            daemon=True,
        )
        runner_started = True
        heartbeat_thread.start()
        try:
            if worker_command:
                process_task_result = run_owned_task_process(
                    [str(item) for item in worker_command],
                    binding=TaskCleanupBinding(
                        task_id=task_id,
                        slice_id=str(packet["slice_id"]),
                        worker_session_id=worker_session_id,
                        packet_id=str(packet["packet_id"]),
                        lease_token=str(packet["lease"]["token"]),
                    ),
                    stdin_bytes=goal.encode("utf-8"),
                    cwd=(
                        execution_packet.get("_execution_workspace_path")
                        or execution_packet.get("workspace_path")
                        or os.getcwd()
                    ),
                    timeout_seconds=float(
                        packet.get("worker_timeout_seconds") or 900
                    ),
                )
                process_cleanup = process_task_result.cleanup
                execution = {
                    "status": (
                        "completed"
                        if process_task_result.receipt.exit_code == 0
                        and not process_task_result.receipt.timed_out
                        else "timeout"
                        if process_task_result.receipt.timed_out
                        else "error"
                    ),
                    "summary": process_task_result.stdout,
                    "stderr": process_task_result.stderr,
                    "process_receipt": process_task_result.receipt.to_dict(),
                }
            else:
                execution = runner(child=child, goal=goal, parent_agent=parent_agent)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, heartbeat_interval + 0.5))
        if heartbeat_thread.is_alive() or heartbeat_errors:
            raise WorkerOutputError("worker_heartbeat_failed")
        execution_status = str(execution.get("status") or "").strip().lower()
        if execution_status == "timeout":
            raise WorkerOutputError("worker_timeout")
        if execution_status in {"cancelled", "canceled"}:
            raise WorkerOutputError("worker_cancelled")
        if execution_status in {"error", "failed", "failure"}:
            raise WorkerOutputError("worker_execution_failed")
        worker_result = _parse_worker_result(execution.get("summary"))
        execution_workspace = str(
            execution_packet.get("_execution_workspace_path")
            or execution_packet.get("workspace_path")
            or ""
        )
        if child_builder is None and execution_workspace:
            actual_base = _workspace_head(execution_workspace)
            expected_base = str(packet.get("workspace_base_commit") or "")
            actual_paths = _workspace_changed_paths(execution_workspace)
            declared_paths = {str(item) for item in worker_result.get("changed_artifacts") or []}
            execution["workspace_readback"] = {
                "workspace_path": execution_workspace,
                "expected_base_commit": expected_base,
                "actual_base_commit": actual_base,
                "actual_changed_paths": sorted(actual_paths),
                "declared_changed_paths": sorted(declared_paths),
            }
            if actual_base != expected_base:
                raise WorkerOutputError("worker_workspace_base_changed")
            if actual_paths != declared_paths:
                raise WorkerOutputError("worker_write_set_mismatch")
            if workspace_lease is not None:
                candidate_digest = snapshot_workspace_candidate(
                    workspace_lease,
                    sorted(declared_paths),
                )
                execution["workspace_readback"]["candidate_digest"] = candidate_digest
                worker_result["candidate_digest"] = candidate_digest
    except WorkerOutputError as exc:
        error = str(exc)
        workspace_cleanup_receipt = None
        if runner_started:
            if process_cleanup is not None:
                try:
                    claim_receipt, workspace_cleanup_receipt = (
                        _recover_after_controlled_stop(error)
                    )
                except Exception as recovery_error:
                    claim_receipt = {
                        "status": "quarantined",
                        "recovery_error": type(recovery_error).__name__,
                    }
            else:
                claim_receipt = runtime_store.quarantine_claim(
                    task_id,
                    worker_session_id=worker_session_id,
                    packet_id=packet["packet_id"],
                    reason=f"{error}_termination_unproven",
                )
        else:
            claim_receipt, workspace_cleanup_receipt = _release_before_start(error)
        return _finish({
            "success": False,
            "error": error,
            "task_id": task_id,
            "worker_session_id": worker_session_id,
            "context_packet": packet,
            "worker_execution": execution,
            "claim_receipt": claim_receipt,
            "workspace_lease": workspace_lease.to_dict() if workspace_lease else None,
            "workspace_cleanup_receipt": workspace_cleanup_receipt,
        })
    except KeyboardInterrupt:
        try:
            if runner_started:
                runtime_store.quarantine_claim(
                    task_id,
                    worker_session_id=worker_session_id,
                    packet_id=packet["packet_id"],
                    reason="worker_interrupted_termination_unproven",
                )
            else:
                _release_before_start("worker_interrupted_before_start")
        finally:
            _finish({})
        raise
    except Exception as exc:
        workspace_cleanup_receipt = None
        if runner_started:
            if process_cleanup is not None:
                try:
                    claim_receipt, workspace_cleanup_receipt = (
                        _recover_after_controlled_stop(
                            f"worker_execution_failed:{type(exc).__name__}"
                        )
                    )
                except Exception as recovery_error:
                    claim_receipt = {
                        "status": "quarantined",
                        "recovery_error": type(recovery_error).__name__,
                    }
            else:
                claim_receipt = runtime_store.quarantine_claim(
                    task_id,
                    worker_session_id=worker_session_id,
                    packet_id=packet["packet_id"],
                    reason=f"worker_execution_termination_unproven:{type(exc).__name__}",
                )
        else:
            claim_receipt, workspace_cleanup_receipt = _release_before_start(
                f"worker_build_failed:{type(exc).__name__}"
            )
        return _finish({
            "success": False,
            "error": "worker_execution_failed",
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
            "task_id": task_id,
            "worker_session_id": worker_session_id,
            "context_packet": packet,
            "worker_execution": execution,
            "claim_receipt": claim_receipt,
            "workspace_lease": workspace_lease.to_dict() if workspace_lease else None,
            "workspace_cleanup_receipt": workspace_cleanup_receipt,
        })

    try:
        receipt = runtime_store.submit_result(
            task_id,
            worker_session_id=worker_session_id,
            packet_id=packet["packet_id"],
            result=worker_result,
        )
    except BaseException:
        try:
            if process_cleanup is not None:
                _recover_after_controlled_stop("worker_result_submission_failed")
            else:
                runtime_store.quarantine_claim(
                    task_id,
                    worker_session_id=worker_session_id,
                    packet_id=packet["packet_id"],
                    reason="worker_result_submission_failed_termination_unproven",
                )
        finally:
            _finish({})
        raise
    accepted = receipt.get("disposition") == "accepted_for_integration"
    rejection_claim_receipt = None
    rejection_workspace_receipt = None
    if accepted and workspace_lease is not None and process_cleanup is not None:
        try:
            workspace_candidate_receipt = freeze_workspace_candidate(
                workspace_lease,
                managed_root=workspace_root,
                changed_artifacts=[
                    str(item) for item in worker_result.get("changed_artifacts") or []
                ],
                process_receipt=process_cleanup.receipt,
            )
        except Exception as exc:
            candidate_failure_receipt = runtime_store.quarantine_candidate_finalize_failure(
                task_id,
                str(receipt["result_id"]),
                worker_session_id=worker_session_id,
                packet_id=str(packet["packet_id"]),
                error=f"{type(exc).__name__}:{exc}",
            )
            return _finish({
                "success": False,
                "error": "workspace_candidate_finalize_failed",
                "error_type": type(exc).__name__,
                "error_detail": str(exc),
                "task_id": task_id,
                "worker_session_id": worker_session_id,
                "context_packet": packet,
                "worker_execution": execution,
                "worker_result": receipt,
                "accepted_result": worker_result,
                "workspace_lease": workspace_lease.to_dict(),
                "workspace_candidate_receipt": None,
                "candidate_finalize_receipt": candidate_failure_receipt,
                "process_receipt": (
                    process_task_result.receipt.to_dict()
                    if process_task_result is not None else None
                ),
            })
    elif not accepted and process_cleanup is not None:
        try:
            rejection_claim_receipt, rejection_workspace_receipt = (
                _recover_after_controlled_stop("worker_result_rejected")
            )
        except Exception as exc:
            rejection_claim_receipt = {
                "status": "quarantined",
                "recovery_error": type(exc).__name__,
                "recovery_error_detail": str(exc),
            }
    return _finish({
        "success": accepted,
        "task_id": task_id,
        "worker_session_id": worker_session_id,
        "context_packet": packet,
        "worker_execution": execution,
        "worker_result": receipt,
        "accepted_result": worker_result,
        "workspace_lease": workspace_lease.to_dict() if workspace_lease else None,
        "workspace_candidate_receipt": (
            workspace_candidate_receipt.to_dict()
            if workspace_candidate_receipt is not None
            else None
        ),
        "claim_receipt": rejection_claim_receipt,
        "workspace_cleanup_receipt": rejection_workspace_receipt,
    })
