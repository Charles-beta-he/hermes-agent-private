"""Unified run-control handles for Hermes execution surfaces.

This module gives gateway/API sessions, cron jobs, kanban task runs, and
future dispatch autoruns a shared control vocabulary without replacing their
native lifecycle stores.  Adapters deliberately delegate to the existing
subsystem APIs so evidence gates, task lifecycle transitions, and audit events
remain canonical in their current stores while callers can still reason in terms
of launch/pause/resume/status/cancel/handoff.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol


class RunState(str, Enum):
    """Subsystem-neutral lifecycle states exposed by RunController adapters."""

    QUEUED = "queued"
    SCHEDULED = "scheduled"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunRequest:
    """Input to :meth:`RunController.launch`.

    ``trigger`` names the entry surface or handoff reason.  ``objective`` is the
    human/work objective.  ``payload`` carries subsystem-specific fields such as
    cron schedule or kanban assignee without changing the common interface.
    """

    trigger: str
    objective: str
    payload: Dict[str, Any] = field(default_factory=dict)
    parent_run_id: Optional[str] = None


@dataclass(frozen=True)
class RunHandle:
    """Pollable, traceable handle returned by all run controllers."""

    run_id: str
    surface: str
    native_id: str
    state: RunState
    trace: Dict[str, Any] = field(default_factory=dict)
    handoff_refs: List[str] = field(default_factory=list)


class RunController(Protocol):
    """Uniform control vocabulary for an execution surface."""

    surface: str

    def launch(self, request: RunRequest) -> RunHandle: ...

    def pause(self, run_id: str, reason: Optional[str] = None) -> RunHandle: ...

    def resume(self, run_id: str) -> RunHandle: ...

    def cancel(self, run_id: str, reason: Optional[str] = None) -> RunHandle: ...

    def status(self, run_id: str) -> RunHandle: ...

    def handoff(self, run_id: str, target_surface: str, note: str = "") -> RunHandle: ...


def _coerce_state(value: Any) -> RunState:
    text = str(value or "").strip().lower()
    aliases = {
        "todo": RunState.QUEUED,
        "ready": RunState.READY,
        "claimed": RunState.RUNNING,
        "running": RunState.RUNNING,
        "review": RunState.WAITING,
        "blocked": RunState.PAUSED,
        "paused": RunState.PAUSED,
        "scheduled": RunState.SCHEDULED,
        "done": RunState.COMPLETED,
        "completed": RunState.COMPLETED,
        "failed": RunState.FAILED,
        "crashed": RunState.FAILED,
        "timed_out": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
        "removed": RunState.CANCELLED,
        "stopping": RunState.CANCELLED,
    }
    return aliases.get(text, RunState.UNKNOWN)


class CronJobRunController:
    """RunController adapter for cron job lifecycle operations."""

    surface = "cron"

    def launch(self, request: RunRequest) -> RunHandle:
        from cron.jobs import create_job

        payload = dict(request.payload or {})
        schedule = str(payload.pop("schedule", "every 1h"))
        job = create_job(
            prompt=request.objective,
            schedule=schedule,
            name=payload.pop("name", None),
            repeat=payload.pop("repeat", None),
            deliver=payload.pop("deliver", None),
            skill=payload.pop("skill", None),
            skills=payload.pop("skills", None),
            model=payload.pop("model", None),
            provider=payload.pop("provider", None),
            base_url=payload.pop("base_url", None),
            script=payload.pop("script", None),
            context_from=payload.pop("context_from", None),
            enabled_toolsets=payload.pop("enabled_toolsets", None),
            workdir=payload.pop("workdir", None),
            no_agent=bool(payload.pop("no_agent", False)),
        )
        return self._handle(job, trigger=request.trigger, parent_run_id=request.parent_run_id)

    def pause(self, run_id: str, reason: Optional[str] = None) -> RunHandle:
        from cron.jobs import pause_job

        job = pause_job(self._native_id(run_id), reason=reason)
        if job is None:
            return self._missing(run_id)
        return self._handle(job)

    def resume(self, run_id: str) -> RunHandle:
        from cron.jobs import resume_job

        job = resume_job(self._native_id(run_id))
        if job is None:
            return self._missing(run_id)
        return self._handle(job)

    def cancel(self, run_id: str, reason: Optional[str] = None) -> RunHandle:
        from cron.jobs import get_job, remove_job

        native_id = self._native_id(run_id)
        existing = get_job(native_id)
        removed = remove_job(native_id)
        return RunHandle(
            run_id=f"cron:{native_id}",
            surface=self.surface,
            native_id=native_id,
            state=RunState.CANCELLED if removed else RunState.UNKNOWN,
            trace={
                "native_state": "removed" if removed else "missing",
                "reason": reason,
                "previous_state": (existing or {}).get("state"),
            },
        )

    def status(self, run_id: str) -> RunHandle:
        from cron.jobs import get_job

        job = get_job(self._native_id(run_id))
        if job is None:
            return self._missing(run_id)
        return self._handle(job)

    def handoff(self, run_id: str, target_surface: str, note: str = "") -> RunHandle:
        # Cron jobs do not have a first-class handoff ledger today.  Preserve
        # native state and expose the handoff intent in the returned trace so a
        # higher-level ledger can persist it.
        handle = self.status(run_id)
        return replace(
            handle,
            trace={**handle.trace, "handoff_target": target_surface, "handoff_note": note},
        )

    def _native_id(self, run_id: str) -> str:
        return run_id.split(":", 1)[1] if run_id.startswith("cron:") else run_id

    def _missing(self, run_id: str) -> RunHandle:
        native_id = self._native_id(run_id)
        return RunHandle(
            run_id=f"cron:{native_id}",
            surface=self.surface,
            native_id=native_id,
            state=RunState.UNKNOWN,
            trace={"native_state": "missing"},
        )

    def _handle(self, job: Dict[str, Any], **trace_fields: Any) -> RunHandle:
        native_state = job.get("state") or ("scheduled" if job.get("enabled", True) else "paused")
        trace = {
            "job_id": job.get("id"),
            "native_state": native_state,
            "next_run_at": job.get("next_run_at"),
            "schedule_display": job.get("schedule_display"),
        }
        trace.update({k: v for k, v in trace_fields.items() if v is not None})
        return RunHandle(
            run_id=f"cron:{job['id']}",
            surface=self.surface,
            native_id=str(job["id"]),
            state=_coerce_state(native_state),
            trace=trace,
        )


class KanbanTaskRunController:
    """RunController adapter for kanban task/run lifecycle operations.

    Pause/resume intentionally maps to ``block_task``/``unblock_task``.  That
    preserves existing review-required/evidence-gate semantics instead of
    inventing a parallel pause flag that dashboards and downstream workers would
    not understand.
    """

    surface = "kanban"

    def __init__(self, *, assignee: Optional[str] = None, created_by: str = "run_controller") -> None:
        self.assignee = assignee
        self.created_by = created_by

    def launch(self, request: RunRequest) -> RunHandle:
        from hermes_cli import kanban_db as kb

        payload = dict(request.payload or {})
        with kb.connect() as conn:
            task_id = kb.create_task(
                conn,
                title=payload.pop("title", request.objective[:80] or "RunController task"),
                assignee=payload.pop("assignee", self.assignee),
                body=payload.pop("body", request.objective),
                parents=payload.pop("parents", ()),
                tenant=payload.pop("tenant", None),
                priority=int(payload.pop("priority", 0)),
                workspace_kind=payload.pop("workspace_kind", "scratch"),
                workspace_path=payload.pop("workspace_path", None),
                triage=bool(payload.pop("triage", False)),
                idempotency_key=payload.pop("idempotency_key", None),
                created_by=payload.pop("created_by", self.created_by),
                max_runtime_seconds=payload.pop("max_runtime_seconds", None),
                skills=payload.pop("skills", None),
            )
            task = kb.get_task(conn, task_id)
        return self._handle(task, trigger=request.trigger, parent_run_id=request.parent_run_id)

    def pause(self, run_id: str, reason: Optional[str] = None) -> RunHandle:
        from hermes_cli import kanban_db as kb

        task_id, native_run_id = self._parse(run_id)
        with kb.connect() as conn:
            kb.block_task(conn, task_id, reason=reason, expected_run_id=native_run_id)
            task = kb.get_task(conn, task_id)
        return self._handle(task, pause_reason=reason)

    def resume(self, run_id: str) -> RunHandle:
        from hermes_cli import kanban_db as kb

        task_id, _native_run_id = self._parse(run_id)
        with kb.connect() as conn:
            kb.unblock_task(conn, task_id)
            task = kb.get_task(conn, task_id)
        return self._handle(task)

    def cancel(self, run_id: str, reason: Optional[str] = None) -> RunHandle:
        from hermes_cli import kanban_db as kb

        task_id, native_run_id = self._parse(run_id)
        with kb.connect() as conn:
            # Kanban has no generic "cancel" state in the public lifecycle.  A
            # controller cancel is therefore modeled as a blocked handoff with a
            # precise reason so operators can review or archive it explicitly.
            cancelled = kb.block_task(
                conn,
                task_id,
                reason=reason or "cancel requested via RunController",
                expected_run_id=native_run_id,
            )
            task = kb.get_task(conn, task_id)
        handle = self._handle(task)
        if not cancelled:
            return replace(
                handle,
                trace={
                    **handle.trace,
                    "cancel_failed": True,
                    "requested_run_id": native_run_id,
                    "reason": reason,
                },
            )
        return replace(handle, state=RunState.CANCELLED)

    def status(self, run_id: str) -> RunHandle:
        from hermes_cli import kanban_db as kb

        task_id, native_run_id = self._parse(run_id)
        with kb.connect() as conn:
            task = kb.get_task(conn, task_id)
            if native_run_id is None:
                return self._handle(task)
            run = kb.get_run(conn, native_run_id)
        if run is None or run.task_id != task_id:
            handle = self._handle(task)
            return replace(
                handle,
                trace={
                    **handle.trace,
                    "requested_run_id": native_run_id,
                    "requested_run_missing": True,
                },
            )
        current_run_id = getattr(task, "current_run_id", None) if task is not None else None
        if current_run_id == native_run_id:
            return self._handle(task, requested_run_id=native_run_id)
        return self._handle_run(task, run, requested_run_id=native_run_id)

    def handoff(self, run_id: str, target_surface: str, note: str = "") -> RunHandle:
        from hermes_cli import kanban_db as kb

        task_id, _native_run_id = self._parse(run_id)
        body = f"RunController handoff -> {target_surface}"
        if note:
            body += f": {note}"
        with kb.connect() as conn:
            kb.add_comment(conn, task_id, author="run_controller", body=body)
            task = kb.get_task(conn, task_id)
        handle = self._handle(task)
        return replace(
            handle,
            trace={**handle.trace, "handoff_target": target_surface, "handoff_note": note},
        )

    def _parse(self, run_id: str) -> tuple[str, Optional[int]]:
        parts = run_id.split(":")
        if parts[0] == "kanban":
            parts = parts[1:]
        if not parts or not parts[0]:
            raise ValueError(f"invalid kanban run id: {run_id!r}")
        native_run_id = int(parts[1]) if len(parts) > 1 and parts[1] else None
        return parts[0], native_run_id

    def _handle(self, task: Any, **trace_fields: Any) -> RunHandle:
        if task is None:
            return RunHandle(
                run_id="kanban:unknown",
                surface=self.surface,
                native_id="unknown",
                state=RunState.UNKNOWN,
                trace={"native_state": "missing"},
            )
        native_run_id = getattr(task, "current_run_id", None)
        run_id = f"kanban:{task.id}" + (f":{native_run_id}" if native_run_id else "")
        trace = {
            "task_id": task.id,
            "native_run_id": native_run_id,
            "native_state": task.status,
            "assignee": task.assignee,
        }
        trace.update({k: v for k, v in trace_fields.items() if v is not None})
        return RunHandle(
            run_id=run_id,
            surface=self.surface,
            native_id=task.id,
            state=_coerce_state(task.status),
            trace=trace,
        )

    def _handle_run(self, task: Any, run: Any, **trace_fields: Any) -> RunHandle:
        trace = {
            "task_id": run.task_id,
            "native_run_id": run.id,
            "native_state": run.status,
            "run_outcome": run.outcome,
            "run_ended_at": run.ended_at,
            "assignee": getattr(task, "assignee", None),
            "current_task_state": getattr(task, "status", None),
            "current_run_id": getattr(task, "current_run_id", None),
        }
        trace.update({k: v for k, v in trace_fields.items() if v is not None})
        return RunHandle(
            run_id=f"kanban:{run.task_id}:{run.id}",
            surface=self.surface,
            native_id=run.task_id,
            state=_coerce_state(run.status),
            trace=trace,
        )


class InMemoryRunLedger:
    """Small trace ledger for demos/tests and future durable ledger adapters."""

    def __init__(self) -> None:
        self._handles: Dict[str, RunHandle] = {}
        self._children: Dict[str, List[str]] = {}

    def record(self, handle: RunHandle) -> RunHandle:
        self._handles[handle.run_id] = handle
        self._children.setdefault(handle.run_id, [])
        return handle

    def record_external_run(
        self,
        *,
        surface: str,
        native_id: str,
        state: str | RunState,
        trace: Optional[Dict[str, Any]] = None,
    ) -> RunHandle:
        coerced = state if isinstance(state, RunState) else _coerce_state(state)
        return self.record(
            RunHandle(
                run_id=f"{surface}:{native_id}",
                surface=surface,
                native_id=native_id,
                state=coerced,
                trace=dict(trace or {}),
            )
        )

    def launch_child(
        self,
        *,
        parent_run_id: str,
        controller: RunController,
        request: RunRequest,
        handoff_ref: str = "",
    ) -> RunHandle:
        child = controller.launch(replace(request, parent_run_id=parent_run_id))
        self.record(child)
        self._children.setdefault(parent_run_id, []).append(child.run_id)
        parent = self._handles[parent_run_id]
        refs = list(parent.handoff_refs)
        refs.append(child.run_id)
        note_key = f"handoff_note:{child.run_id}"
        self._handles[parent_run_id] = replace(
            parent,
            handoff_refs=refs,
            trace={**parent.trace, note_key: handoff_ref} if handoff_ref else parent.trace,
        )
        return child

    def status(self, run_id: str) -> Optional[RunHandle]:
        return self._handles.get(run_id)

    def trace(self, root_run_id: str) -> List[RunHandle]:
        ordered: List[RunHandle] = []

        def walk(rid: str) -> None:
            handle = self._handles.get(rid)
            if handle is None:
                return
            ordered.append(handle)
            for child_id in self._children.get(rid, []):
                walk(child_id)

        walk(root_run_id)
        return ordered

    def events(self) -> Iterable[RunHandle]:
        return tuple(self._handles.values())
