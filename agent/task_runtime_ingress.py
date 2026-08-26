"""Ingress adapter for task-centric multi-session turns.

The durable task store owns long-lived truth.  This module contributes only a
bounded, API-facing projection for the currently bound entrypoint and records
the exact inbound user text as a source event.  It never auto-creates tasks or
changes task bindings.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.task_runtime import TaskRuntimeStore

logger = logging.getLogger(__name__)

MAX_CONTEXT_BYTES = 12_000


def _platform(agent: Any) -> str:
    return str(getattr(agent, "platform", None) or "cli").strip().lower()


def _projection(task: dict[str, Any], *, entrypoint: str, session_id: str) -> dict[str, Any]:
    """Return indices and state only; original source bodies stay in the SSOT."""
    slices = [
        {
            "slice_id": item.get("slice_id"),
            "status": item.get("status"),
            "depends_on": item.get("depends_on", []),
            "lease_owner": item.get("lease_owner"),
            "result_id": item.get("result_id"),
        }
        for item in task.get("slices", [])[:40]
    ]
    evidence = [
        {
            "claim_id": item.get("claim_id"),
            "status": item.get("status"),
            "candidate_digest": item.get("candidate_digest"),
        }
        for item in task.get("evidence_graph", [])[:40]
        if isinstance(item, dict)
    ]
    return {
        "schema": "hermes.task-runtime-turn-context.v1",
        "task_id": task["task_id"],
        "title": task.get("title"),
        "status": task.get("status"),
        "binding": {"entrypoint": entrypoint, "session_id": session_id},
        "revisions": {
            "spec": task.get("spec_revision"),
            "plan": task.get("plan_revision"),
            "state": task.get("state_revision"),
        },
        "candidate_digest": task.get("candidate_digest") or None,
        "source_index": {
            "original_count": len(task.get("original_sources", [])),
            "event_count": len(task.get("source_events", [])),
        },
        "slices": slices,
        "evidence": evidence,
        "closure": {
            "status": task.get("closure", {}).get("status"),
            "closure_session_id": task.get("closure", {}).get("closure_session_id"),
        },
        "instructions": [
            "Treat this as a bounded index; inspect exact sources through task_runtime status before implementation decisions.",
            "Use task_runtime for deterministic task state transitions; do not infer completion from conversation text.",
        ],
    }


def _serialize_bounded(payload: dict[str, Any]) -> str:
    prefix = "[TASK_RUNTIME_CONTEXT v1]\n"
    suffix = "\n[/TASK_RUNTIME_CONTEXT]"
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    rendered = prefix + body + suffix
    if len(rendered.encode("utf-8")) <= MAX_CONTEXT_BYTES:
        return rendered

    # Deterministic degradation keeps identity/revisions while trimming lists.
    payload = dict(payload)
    payload["slices"] = payload.get("slices", [])[:10]
    payload["evidence"] = payload.get("evidence", [])[:10]
    payload["projection_truncated"] = True
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    rendered = prefix + body + suffix
    if len(rendered.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("task_runtime_context_exceeds_bound")
    return rendered


def hydrate_bound_task_context(
    agent: Any,
    *,
    user_message: str,
    turn_id: str,
) -> str | None:
    """Record a bound inbound turn and return its bounded runtime projection.

    Failures are logged and fail open so an unavailable advisory/control-plane
    database cannot take down an otherwise valid CLI or gateway turn.
    """
    entrypoint = _platform(agent)
    session_id = str(getattr(agent, "session_id", None) or "").strip()
    if not session_id:
        return None
    try:
        store = TaskRuntimeStore(TaskRuntimeStore.default_path())
        task = store.resolve_entrypoint(entrypoint, session_id)
        if task is None:
            return None
        task = store.append_source_event(
            task["task_id"],
            {
                "event_id": f"{entrypoint}:{session_id}:{turn_id}",
                "source_id": f"{entrypoint}:{session_id}:{turn_id}",
                "kind": "user_message",
                "content": user_message,
                "entrypoint": entrypoint,
                "session_id": session_id,
                "turn_id": turn_id,
            },
        )
        return _serialize_bounded(
            _projection(task, entrypoint=entrypoint, session_id=session_id)
        )
    except Exception as exc:
        logger.warning(
            "task runtime ingress projection skipped (platform=%s session=%s): %s",
            entrypoint,
            session_id,
            exc,
        )
        return None
