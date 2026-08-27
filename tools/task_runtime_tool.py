"""Task-centric multi-session runtime tool.

This is the narrow control-plane surface shared by CLI and messaging agents.
It persists facts in :mod:`agent.task_runtime`; it does not execute workers,
change canonical task lifecycle outside that store, or send external messages.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.task_runtime import RevisionConflict, TaskRuntimeStore
from tools.registry import registry, tool_error, tool_result


def _store(db_path: str | Path | None) -> TaskRuntimeStore:
    return TaskRuntimeStore(db_path or TaskRuntimeStore.default_path())


def _projection(task: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded task projection suitable for an entry conversation."""
    slices = list(task.get("slices") or [])
    return {
        "schema": task.get("schema"),
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "spec_revision": task.get("spec_revision"),
        "plan_revision": task.get("plan_revision"),
        "state_revision": task.get("state_revision"),
        "candidate_digest": task.get("candidate_digest"),
        "bindings": list(task.get("bindings") or []),
        "slice_counts": {
            status: sum(1 for item in slices if item.get("status") == status)
            for status in ("pending", "claimed", "submitted", "completed", "quarantined", "cancelled")
        },
        "slices": [
            {
                "slice_id": item.get("slice_id"),
                "objective": item.get("objective"),
                "status": item.get("status"),
                "dependencies": list(item.get("dependencies") or []),
                "result_id": item.get("result_id"),
            }
            for item in slices[:64]
        ],
        "closure": dict(task.get("closure") or {}),
    }


def _resolve_task(
    store: TaskRuntimeStore,
    *,
    control_task_id: str | None,
    entrypoint: str,
    session_id: str,
) -> dict[str, Any]:
    if control_task_id:
        return store.get_task(control_task_id)
    task = store.resolve_entrypoint(entrypoint, session_id)
    if task is None:
        raise KeyError("no_task_bound_to_entrypoint")
    return task


def task_runtime_tool(
    *,
    action: str,
    task_id: str | None = None,
    entrypoint: str = "cli",
    session_id: str = "",
    target_session_id: str = "",
    title: str = "",
    original_sources: Sequence[Mapping[str, Any]] | None = None,
    spec_bundle: Mapping[str, Any] | None = None,
    review_verdict: str = "",
    review_findings: Sequence[Mapping[str, Any] | str] | None = None,
    slices: Sequence[Mapping[str, Any]] | None = None,
    expected_state_revision: int | None = None,
    worker_session_id: str = "",
    lease_seconds: int = 300,
    packet_id: str = "",
    worker_result: Mapping[str, Any] | None = None,
    result_id: str = "",
    integrator_session_id: str = "",
    candidate_digest: str = "",
    verifier_session_id: str = "",
    claims: Mapping[str, str] | None = None,
    closure_packet: Mapping[str, Any] | None = None,
    closure_evidence_refs: Sequence[Mapping[str, Any]] | None = None,
    closure_verification_results: Sequence[Mapping[str, Any]] | None = None,
    source_event: Mapping[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> str:
    """Execute one deterministic task-runtime control-plane transition."""
    try:
        store = _store(db_path)
        operation = str(action or "").strip().lower()
        surface = str(entrypoint or "cli").strip().lower()
        current_session = str(session_id or "").strip()

        if operation == "create":
            task = store.create_task(
                title=title,
                original_sources=list(original_sources or []),
                task_id=task_id,
            )
            if current_session:
                task = store.bind_entrypoint(task["task_id"], entrypoint=surface, session_id=current_session)
            return tool_result(success=True, task=_projection(task))

        if operation == "bind":
            if not task_id:
                raise ValueError("task_id_required")
            bind_session = str(target_session_id or current_session).strip()
            if not bind_session:
                raise ValueError("session_id_required")
            task = store.bind_entrypoint(task_id, entrypoint=surface, session_id=bind_session)
            return tool_result(success=True, task=_projection(task))

        task = _resolve_task(
            store,
            control_task_id=task_id,
            entrypoint=surface,
            session_id=current_session,
        )
        control_task_id = str(task["task_id"])

        if operation == "status":
            return tool_result(success=True, task=_projection(task))
        if operation == "record_source":
            task = store.append_source_event(control_task_id, dict(source_event or {}))
            return tool_result(success=True, task=_projection(task))
        if operation == "review_spec":
            if expected_state_revision is None:
                raise ValueError("expected_state_revision_required")
            if not current_session:
                raise ValueError("spec_reviewer_session_id_required")
            review = store.record_spec_review(
                control_task_id,
                dict(spec_bundle or {}),
                reviewer_session_id=current_session,
                verdict=review_verdict,
                findings=list(review_findings or []),
                expected_state_revision=int(expected_state_revision),
            )
            return tool_result(success=True, spec_review=review)
        if operation == "freeze_spec":
            if expected_state_revision is None:
                raise ValueError("expected_state_revision_required")
            if not current_session:
                raise ValueError("spec_compiler_session_id_required")
            task = store.freeze_spec(
                control_task_id,
                dict(spec_bundle or {}),
                compiler_session_id=current_session,
                expected_state_revision=int(expected_state_revision),
            )
            return tool_result(success=True, task=_projection(task))
        if operation == "set_plan":
            if expected_state_revision is None:
                raise ValueError("expected_state_revision_required")
            task = store.set_plan(
                control_task_id,
                list(slices or []),
                expected_state_revision=int(expected_state_revision),
            )
            return tool_result(success=True, task=_projection(task))
        if operation == "claim":
            packet = store.claim_next_slice(
                control_task_id,
                worker_session_id=worker_session_id or current_session,
                lease_seconds=int(lease_seconds),
            )
            return tool_result(success=True, context_packet=packet)
        if operation == "submit":
            if not packet_id:
                raise ValueError("packet_id_required")
            receipt = store.submit_result(
                control_task_id,
                worker_session_id=worker_session_id or current_session,
                packet_id=packet_id,
                result=dict(worker_result or {}),
            )
            return tool_result(success=True, worker_result=receipt)
        if operation == "integrate":
            if not result_id:
                raise ValueError("result_id_required")
            if not current_session:
                raise ValueError("integrator_session_id_required")
            if integrator_session_id and integrator_session_id != current_session:
                raise ValueError("integrator_session_override_forbidden")
            receipt = store.integrate_result(
                control_task_id,
                result_id,
                integrator_session_id=current_session,
            )
            return tool_result(success=True, integration=receipt)
        if operation == "closure_context":
            if not current_session:
                raise ValueError("verifier_session_id_required")
            if verifier_session_id and verifier_session_id != current_session:
                raise ValueError("verifier_session_override_forbidden")
            packet = store.build_closure_packet(
                control_task_id,
                verifier_session_id=current_session,
            )
            return tool_result(success=True, closure_packet=packet)
        if operation == "close":
            if not current_session:
                raise ValueError("verifier_session_id_required")
            if verifier_session_id and verifier_session_id != current_session:
                raise ValueError("verifier_session_override_forbidden")
            task = store.close_task(
                control_task_id,
                candidate_digest=candidate_digest,
                verifier_session_id=current_session,
                claims=dict(claims or {}),
                closure_packet=dict(closure_packet or {}),
                evidence_refs=list(closure_evidence_refs or []),
                verification_results=list(closure_verification_results or []),
            )
            return tool_result(success=True, task=_projection(task))
        raise ValueError(f"unsupported_action:{operation}")
    except RevisionConflict as exc:
        return tool_error(str(exc), code="revision_conflict")
    except KeyError as exc:
        return tool_error(str(exc).strip("'"), code="not_found")
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc), code="invalid_request")
    except Exception as exc:
        return tool_error(f"task_runtime_failed:{type(exc).__name__}", code="internal_error")


TASK_RUNTIME_SCHEMA = {
    "name": "task_runtime",
    "description": (
        "Persistent task-centric multi-session control plane for complex work. "
        "Use it when a task must survive context compression/session replacement, "
        "continue across CLI and Telegram, dispatch immutable ContextPackets to fresh "
        "workers, reject stale/expired results, integrate evidence deterministically, "
        "or close against an independent verifier. Conversation summaries are not task "
        "authority. Typical flow: create → independent review_spec → freeze_spec → "
        "set_plan → claim → delegate a "
        "fresh worker with the returned packet → submit → integrate → independent close."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "bind", "status", "record_source", "review_spec", "freeze_spec", "set_plan", "claim", "submit", "integrate", "closure_context", "close"],
            },
            "task_id": {"type": "string", "description": "Persistent control-plane task ID. Omit for status on the currently bound entrypoint."},
            "entrypoint": {"type": "string", "description": "Entrypoint identity such as cli or telegram.", "default": "cli"},
            "target_session_id": {"type": "string", "description": "Explicit session to bind. Bind only; otherwise the current runtime session is used."},
            "title": {"type": "string"},
            "original_sources": {"type": "array", "items": {"type": "object"}},
            "spec_bundle": {"type": "object"},
            "review_verdict": {"type": "string", "enum": ["approved", "rejected"]},
            "review_findings": {"type": "array", "items": {}},
            "slices": {"type": "array", "items": {"type": "object"}},
            "expected_state_revision": {"type": "integer"},
            "worker_session_id": {"type": "string"},
            "lease_seconds": {"type": "integer", "minimum": 1, "maximum": 86400, "default": 300},
            "packet_id": {"type": "string"},
            "worker_result": {"type": "object"},
            "result_id": {"type": "string"},
            "candidate_digest": {"type": "string"},
            "claims": {"type": "object", "additionalProperties": {"type": "string"}},
            "closure_packet": {"type": "object"},
            "closure_evidence_refs": {"type": "array", "items": {"type": "object"}},
            "closure_verification_results": {"type": "array", "items": {"type": "object"}},
            "source_event": {"type": "object"},
        },
        "required": ["action"],
    },
}


registry.register(
    name="task_runtime",
    toolset="task_runtime",
    schema=TASK_RUNTIME_SCHEMA,
    handler=lambda args, **kw: task_runtime_tool(
        action=args.get("action", ""),
        task_id=args.get("task_id"),
        entrypoint=args.get("entrypoint") or kw.get("platform") or "cli",
        session_id=kw.get("session_id") or "",
        target_session_id=args.get("target_session_id") or "",
        title=args.get("title", ""),
        original_sources=args.get("original_sources"),
        spec_bundle=args.get("spec_bundle"),
        review_verdict=args.get("review_verdict", ""),
        review_findings=args.get("review_findings"),
        slices=args.get("slices"),
        expected_state_revision=args.get("expected_state_revision"),
        worker_session_id=args.get("worker_session_id", ""),
        lease_seconds=args.get("lease_seconds", 300),
        packet_id=args.get("packet_id", ""),
        worker_result=args.get("worker_result"),
        result_id=args.get("result_id", ""),
        candidate_digest=args.get("candidate_digest", ""),
        claims=args.get("claims"),
        closure_packet=args.get("closure_packet"),
        closure_evidence_refs=args.get("closure_evidence_refs"),
        closure_verification_results=args.get("closure_verification_results"),
        source_event=args.get("source_event"),
    ),
    emoji="🧭",
)
