"""
Session sidecar producers — bridge Hermes entrypoints to the sidecar layer.

Each producer function accepts the raw parameters available at that entrypoint
and returns a populated ``SessionSidecar`` that the caller can write with
``session_sidecar.write_sidecar()``.

Entrypoints covered:
  - CLI agent session end (cli_producer)
  - Cron job completion (cron_producer)
  - Browser / computer-use session (browser_producer)
  - Script run (script_producer)
  - Gateway conversation (gateway_producer)
  - Kanban worker (kanban_worker_producer)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from session_sidecar import (
    ENTRYPOINT_BROWSER,
    ENTRYPOINT_CLI,
    ENTRYPOINT_CRON,
    ENTRYPOINT_GATEWAY,
    ENTRYPOINT_KANBAN_WORKER,
    ENTRYPOINT_SCRIPT,
    ENTRYPOINT_ACP,
    ArtifactRef,
    SessionSidecar,
    sidecar_sha256,
    write_sidecar,
    _generate_run_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI producer
# ---------------------------------------------------------------------------
def cli_producer(
    *,
    session_id: str = "",
    task_id: str = "",
    model: str = "",
    provider: str = "",
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    artifacts: Optional[List[ArtifactRef]] = None,
    evidence_refs: Optional[List[str]] = None,
    output_log_path: str = "",
) -> SessionSidecar:
    """Build a sidecar for a CLI agent session.

    Call at the end of ``HermesCLI._handle_conversation_end()`` or similar.
    """
    sc = SessionSidecar(
        entrypoint=ENTRYPOINT_CLI,
        session_id=session_id,
        task_id=task_id,
        model=model,
        provider=provider,
        platform="cli",
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
    )
    sc.evidence_refs = evidence_refs or []
    if output_log_path:
        sc.artifacts.append(ArtifactRef(
            path=os.path.abspath(output_log_path),
            kind="log",
            description="CLI agent session log",
        ))
    if artifacts:
        sc.artifacts.extend(artifacts)
    return sc


# ---------------------------------------------------------------------------
# Cron producer
# ---------------------------------------------------------------------------
def cron_producer(
    *,
    job_id: str = "",
    job_name: str = "",
    schedule: str = "",
    model: str = "",
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    output_text: str = "",
    output_log_path: str = "",
    evidence_refs: Optional[List[str]] = None,
) -> SessionSidecar:
    """Build a sidecar for a cron job execution.

    Call from ``cron/scheduler.py run_job()`` after ``subprocess.run()``
    completes.
    """
    run_id = _generate_run_id()
    sc = SessionSidecar(
        run_id=run_id,
        entrypoint=ENTRYPOINT_CRON,
        session_id=f"cron:{job_id}",
        task_id=job_id if job_id else "",
        model=model,
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
        platform="cron",
    )
    sc.extra["job_name"] = job_name
    sc.extra["schedule"] = schedule
    sc.evidence_refs = evidence_refs or []

    if output_log_path:
        sc.artifacts.append(ArtifactRef(
            path=os.path.abspath(output_log_path),
            kind="log",
            description="cron job output log",
        ))
    if output_text:
        # Summarise long output — store as short evidence ref
        n_lines = len(output_text.strip().splitlines())
        truncated = n_lines > 100
        sc.extra["output_line_count"] = n_lines
        sc.extra["output_truncated"] = truncated

    return sc


# ---------------------------------------------------------------------------
# Browser / automation producer
# ---------------------------------------------------------------------------
def browser_producer(
    *,
    session_key: str = "",
    task_id: str = "",
    url_count: int = 0,
    screenshot_count: int = 0,
    click_count: int = 0,
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    model: str = "",
    provider: str = "",
    screenshot_paths: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
) -> SessionSidecar:
    """Build a sidecar for a browser / computer-use automation session.

    Call from ``browser_tool.py`` / ``computer_use`` tool after a session
    ends (either normally or with an error).
    """
    run_id = _generate_run_id()
    sc = SessionSidecar(
        run_id=run_id,
        entrypoint=ENTRYPOINT_BROWSER,
        session_id=session_key or "",
        task_id=task_id or "",
        model=model,
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
        platform="automation",
    )
    sc.extra["url_count"] = url_count
    sc.extra["screenshot_count"] = screenshot_count
    sc.extra["click_count"] = click_count
    sc.evidence_refs = evidence_refs or []

    for sp in (screenshot_paths or []):
        sp_abs = os.path.abspath(sp)
        try:
            sz = os.path.getsize(sp_abs)
        except OSError:
            sz = 0
        sc.artifacts.append(ArtifactRef(
            path=sp_abs,
            kind="screenshot",
            size_bytes=sz,
            description="automation screenshot",
        ))

    return sc


# ---------------------------------------------------------------------------
# Script producer
# ---------------------------------------------------------------------------
def script_producer(
    *,
    script_path: str = "",
    args: Optional[List[str]] = None,
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    output_paths: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
) -> SessionSidecar:
    """Build a sidecar for a standalone script run.

    The script runner calls this after the script exits.
    """
    run_id = _generate_run_id()
    sc = SessionSidecar(
        run_id=run_id,
        entrypoint=ENTRYPOINT_SCRIPT,
        session_id=f"script:{Path(script_path).name}",
        task_id="",
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
        platform="script",
    )
    sc.extra["script_path"] = script_path
    sc.extra["args"] = args or []
    sc.evidence_refs = evidence_refs or []

    for op in (output_paths or []):
        op_abs = os.path.abspath(op)
        try:
            sz = os.path.getsize(op_abs)
        except OSError:
            sz = 0
        sc.artifacts.append(ArtifactRef(
            path=op_abs,
            kind="file",
            size_bytes=sz,
            description="script output",
        ))

    return sc


# ---------------------------------------------------------------------------
# Gateway producer
# ---------------------------------------------------------------------------
def gateway_producer(
    *,
    session_id: str = "",
    platform: str = "telegram",
    model: str = "",
    message_count: int = 0,
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
) -> SessionSidecar:
    """Build a sidecar for a gateway conversation."""
    run_id = _generate_run_id()
    sc = SessionSidecar(
        run_id=run_id,
        entrypoint=ENTRYPOINT_GATEWAY,
        session_id=session_id,
        model=model,
        platform=platform,
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
    )
    sc.extra["message_count"] = message_count
    sc.evidence_refs = evidence_refs or []
    return sc


# ---------------------------------------------------------------------------
# Kanban worker producer
# ---------------------------------------------------------------------------
def kanban_worker_producer(
    *,
    task_id: str = "",
    session_id: str = "",
    workspace_path: str = "",
    assignee: str = "",
    model: str = "",
    provider: str = "",
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    output_paths: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    lifecycle_transitions: Optional[List[Dict[str, str]]] = None,
) -> SessionSidecar:
    """Build a sidecar for a kanban worker execution."""
    run_id = _generate_run_id()
    sc = SessionSidecar(
        run_id=run_id,
        entrypoint=ENTRYPOINT_KANBAN_WORKER,
        session_id=session_id or (f"kanban:{task_id}" if task_id else "kanban"),
        task_id=task_id,
        model=model,
        provider=provider,
        platform="kanban",
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
        lifecycle_transitions=lifecycle_transitions or [],
    )
    if assignee:
        sc.extra["assignee"] = assignee
    if workspace_path:
        sc.extra["workspace_path"] = os.path.abspath(workspace_path)
    sc.evidence_refs = evidence_refs or []

    for op in (output_paths or []):
        op_abs = os.path.abspath(op)
        try:
            sz = os.path.getsize(op_abs)
        except OSError:
            sz = 0
        sc.artifacts.append(ArtifactRef(
            path=op_abs,
            kind="file",
            size_bytes=sz,
            description="kanban worker output",
        ))

    return sc


# ---------------------------------------------------------------------------
# ACP adapter producer
# ---------------------------------------------------------------------------
def acp_producer(
    *,
    session_id: str = "",
    client_name: str = "",
    command: str = "",
    model: str = "",
    provider: str = "",
    duration_s: float = 0.0,
    status: str = "succeeded",
    error_class: str = "",
    error_message: str = "",
    degraded_flags: Optional[List[str]] = None,
    output_log_path: str = "",
    evidence_refs: Optional[List[str]] = None,
) -> SessionSidecar:
    """Build a sidecar for an ACP adapter session."""
    run_id = _generate_run_id()
    sc = SessionSidecar(
        run_id=run_id,
        entrypoint=ENTRYPOINT_ACP,
        session_id=session_id or "acp",
        model=model,
        provider=provider,
        platform="acp",
        duration_s=duration_s,
        status=status,
        error_class=error_class,
        error_message=error_message,
        degraded_flags=degraded_flags or [],
    )
    if client_name:
        sc.extra["client_name"] = client_name
    if command:
        sc.extra["command"] = command
    sc.evidence_refs = evidence_refs or []
    if output_log_path:
        sc.artifacts.append(ArtifactRef(
            path=os.path.abspath(output_log_path),
            kind="log",
            description="ACP adapter session log",
        ))
    return sc


# ---------------------------------------------------------------------------
# Convenience: one‑shot write from any producer
# ---------------------------------------------------------------------------
def produce_and_write(*, producer_kwargs: Dict[str, Any],
                      producer: str = "cli",
                      strip_sensitive: bool = True) -> str:
    """Build a sidecar from a named producer and write it to disk.

    Returns the ``run_id`` so callers can reference it immediately.
    """
    producers = {
        "cli": cli_producer,
        "cron": cron_producer,
        "browser": browser_producer,
        "automation": browser_producer,
        "script": script_producer,
        "gateway": gateway_producer,
        "kanban_worker": kanban_worker_producer,
        "kanban-worker": kanban_worker_producer,
        "kanban": kanban_worker_producer,
        "acp": acp_producer,
        "acp_adapter": acp_producer,
        "acp-adapter": acp_producer,
    }
    fn = producers.get(producer)
    if fn is None:
        raise ValueError(f"Unknown producer {producer!r}; choices: {list(producers)}")
    sc = fn(**producer_kwargs)
    write_sidecar(sc, strip_sensitive=strip_sensitive)
    return sc.run_id
