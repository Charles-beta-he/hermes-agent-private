"""Continuation Control Plane primitives.

This module is intentionally small and dependency-light so conversation turns,
delegated workers, taskpool ticks, cron jobs, and gateway delivery retries can
all describe resumable work with the same durable packet shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any, Mapping

SCHEMA = "hermes_continuation_packet_v1"
STATUS_SCHEMA = "hermes_continuation_status_v1"
STATES = ("active", "completed", "failed", "archived")


def continuation_root() -> Path:
    return Path(
        os.environ.get(
            "HERMES_CONTINUATION_DIR",
            str(Path.home() / ".hermes" / "state" / "continuations"),
        )
    ).expanduser()


def normalize_continuation_reason(reason: str | None) -> str:
    text = str(reason or "").strip()
    if not text:
        return "unknown"
    if text == "budget_exhausted":
        return "budget_exhausted"
    if text.startswith("max_iterations_reached"):
        return "max_iterations"
    lowered = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return lowered or "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))[:96].strip("._-") or "continuation"


@dataclass(frozen=True)
class ContinuationPacket:
    scope: str
    reason: str
    raw_reason: str
    summary: str
    packet_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_utc_now)
    parent_packet_id: str | None = None
    session_id: str | None = None
    parent_session_id: str | None = None
    task_id: str | None = None
    model: str | None = None
    provider: str | None = None
    remaining_work: list[Any] = field(default_factory=list)
    evidence: list[Any] = field(default_factory=list)
    resume: dict[str, Any] = field(default_factory=dict)
    delivery: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "packet_id": self.packet_id,
            "scope": self.scope,
            "reason": self.reason,
            "raw_reason": self.raw_reason,
            "summary": self.summary,
            "created_at": self.created_at,
            "parent_packet_id": self.parent_packet_id,
            "session_id": self.session_id,
            "parent_session_id": self.parent_session_id,
            "task_id": self.task_id,
            "model": self.model,
            "provider": self.provider,
            "remaining_work": list(self.remaining_work),
            "evidence": list(self.evidence),
            "resume": dict(self.resume),
            "delivery": dict(self.delivery),
            "limits": dict(self.limits),
            "metadata": dict(self.metadata),
        }


def build_continuation_packet(
    *,
    scope: str,
    reason: str | None,
    summary: str,
    session_id: str | None = None,
    parent_session_id: str | None = None,
    task_id: str | None = None,
    parent_packet_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    remaining_work: list[Any] | None = None,
    evidence: list[Any] | None = None,
    resume: Mapping[str, Any] | None = None,
    delivery: Mapping[str, Any] | None = None,
    limits: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ContinuationPacket:
    raw_reason = str(reason or "unknown")
    return ContinuationPacket(
        scope=str(scope or "unknown"),
        reason=normalize_continuation_reason(raw_reason),
        raw_reason=raw_reason,
        summary=str(summary or ""),
        parent_packet_id=parent_packet_id,
        session_id=session_id,
        parent_session_id=parent_session_id,
        task_id=task_id,
        model=model,
        provider=provider,
        remaining_work=list(remaining_work or []),
        evidence=list(evidence or []),
        resume=dict(resume or {}),
        delivery=dict(delivery or {}),
        limits=dict(limits or {}),
        metadata=dict(metadata or {}),
    )


def write_continuation_packet(packet: ContinuationPacket | Mapping[str, Any], *, root: Path | str | None = None, state: str = "active") -> Path:
    if state not in STATES:
        raise ValueError(f"invalid continuation state: {state}")
    payload = packet.to_dict() if isinstance(packet, ContinuationPacket) else dict(packet)
    base = Path(root).expanduser() if root is not None else continuation_root()
    out_dir = base / state
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_id = str(payload.get("packet_id") or uuid.uuid4().hex)
    scope = _safe_slug(str(payload.get("scope") or "unknown"))
    path = out_dir / f"{scope}-{_safe_slug(packet_id)}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_legacy_relay_packet(packet: ContinuationPacket | Mapping[str, Any], *, legacy_dir: Path | str | None = None, relay_index: int | None = None) -> Path:
    """Write a relay-compatible packet path while preserving the unified schema.

    Existing debugging workflows look under ~/.hermes/state/relay-packets and
    expect relay-* filenames. The contents now use the global continuation
    schema with legacy relay metadata in `metadata.legacy_relay`.
    """
    payload = packet.to_dict() if isinstance(packet, ContinuationPacket) else dict(packet)
    out_dir = Path(legacy_dir).expanduser() if legacy_dir is not None else Path(os.environ.get("HERMES_RELAY_PACKET_DIR", str(Path.home() / ".hermes" / "state" / "relay-packets"))).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    session_id = _safe_slug(str(payload.get("session_id") or "no-session"))
    idx = relay_index if relay_index is not None else payload.get("limits", {}).get("relay_index", 1)
    path = out_dir / f"relay-{session_id}-{idx}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def continuation_status(*, root: Path | str | None = None, backlog_warning_threshold: int = 100) -> dict[str, Any]:
    base = Path(root).expanduser() if root is not None else continuation_root()
    counts: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    dirs: dict[str, Any] = {}
    active_count = 0
    for state in STATES:
        state_dir = base / state
        files = sorted(state_dir.glob("*.json")) if state_dir.exists() else []
        counts[state] = len(files)
        dirs[state] = {"path": str(state_dir), "count": len(files)}
        if state == "active":
            active_count = len(files)
            for path in files:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    scope = str(payload.get("scope") or "unknown")
                except Exception:
                    scope = "unreadable"
                by_scope[scope] = by_scope.get(scope, 0) + 1
    return {
        "schema": STATUS_SCHEMA,
        "root": str(base),
        "active_count": active_count,
        "counts": counts,
        "by_scope": by_scope,
        "dirs": dirs,
        "backlog_warning_threshold": backlog_warning_threshold,
        "backlog_warning": "continuation_backlog_high" if active_count > backlog_warning_threshold else "",
    }
