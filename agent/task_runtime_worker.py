"""Fresh-session executor for durable task-runtime slices.

A worker receives exactly one immutable ContextPacket, runs in a newly-created
AIAgent session, and may only return a structured WorkerResult proposal.  The
control-plane store validates and records the proposal; integration remains a
separate parent/integrator action.
"""

from __future__ import annotations

import json
import re
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


def _default_child_builder(*, worker_session_id: str, packet: dict[str, Any], parent_agent: Any):
    from tools.delegate_tool import _build_child_agent

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
            execution = runner(child=child, goal=goal, parent_agent=parent_agent)
        execution_status = str(execution.get("status") or "").strip().lower()
        if execution_status == "timeout":
            raise WorkerOutputError("worker_timeout")
        if execution_status in {"cancelled", "canceled"}:
            raise WorkerOutputError("worker_cancelled")
        if execution_status in {"error", "failed", "failure"}:
            raise WorkerOutputError("worker_execution_failed")
        worker_result = _parse_worker_result(execution.get("summary"))
    except WorkerOutputError as exc:
        runtime_store.release_claim(
            task_id,
            worker_session_id=worker_session_id,
            packet_id=packet["packet_id"],
            reason=str(exc),
        )
        return {
            "success": False,
            "error": str(exc),
            "task_id": task_id,
            "worker_session_id": worker_session_id,
            "context_packet": packet,
            "worker_execution": execution,
        }
    except KeyboardInterrupt:
        runtime_store.release_claim(
            task_id,
            worker_session_id=worker_session_id,
            packet_id=packet["packet_id"],
            reason="worker_interrupted",
        )
        raise
    except Exception as exc:
        runtime_store.release_claim(
            task_id,
            worker_session_id=worker_session_id,
            packet_id=packet["packet_id"],
            reason=f"worker_execution_failed:{type(exc).__name__}",
        )
        return {
            "success": False,
            "error": "worker_execution_failed",
            "error_type": type(exc).__name__,
            "task_id": task_id,
            "worker_session_id": worker_session_id,
            "context_packet": packet,
            "worker_execution": execution,
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
