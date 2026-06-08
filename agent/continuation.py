"""Continuation Control Plane primitives.

This module is intentionally small and dependency-light so conversation turns,
delegated workers, taskpool ticks, cron jobs, and gateway delivery retries can
all describe resumable work with the same durable packet shape.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any

SCHEMA = "hermes_continuation_packet_v1"
STATUS_SCHEMA = "hermes_continuation_status_v1"
STATES = ("active", "completed", "failed", "archived")

# Backpressure: cap live active packets. New packets that would exceed the cap
# are diverted to archived/ (flagged metadata.overflow_archived) so a burst of
# turns can't unboundedly grow the active backlog before the GC sweep runs.
MAX_ACTIVE_CONTINUATIONS = 100


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
    completed_work: list[Any] = field(default_factory=list)
    evidence: list[Any] = field(default_factory=list)
    goal_boundary: dict[str, Any] = field(default_factory=dict)
    side_effect_ledger: list[Any] = field(default_factory=list)
    refusal_flags: list[str] = field(default_factory=list)
    freshness_requirements: dict[str, Any] = field(default_factory=dict)
    resume: dict[str, Any] = field(default_factory=dict)
    delivery: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    recurrent_task_state: dict[str, Any] = field(default_factory=dict)
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
            "completed_work": list(self.completed_work),
            "evidence": list(self.evidence),
            "goal_boundary": dict(self.goal_boundary),
            "side_effect_ledger": list(self.side_effect_ledger),
            "refusal_flags": list(self.refusal_flags),
            "freshness_requirements": dict(self.freshness_requirements),
            "resume": dict(self.resume),
            "delivery": dict(self.delivery),
            "limits": dict(self.limits),
            "recurrent_task_state": dict(self.recurrent_task_state),
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
    completed_work: list[Any] | None = None,
    evidence: list[Any] | None = None,
    goal_boundary: Mapping[str, Any] | None = None,
    side_effect_ledger: list[Any] | None = None,
    refusal_flags: list[str] | None = None,
    freshness_requirements: Mapping[str, Any] | None = None,
    resume: Mapping[str, Any] | None = None,
    delivery: Mapping[str, Any] | None = None,
    limits: Mapping[str, Any] | None = None,
    recurrent_task_state: Mapping[str, Any] | None = None,
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
        completed_work=list(completed_work or []),
        evidence=list(evidence or []),
        goal_boundary=dict(goal_boundary or {}),
        side_effect_ledger=list(side_effect_ledger or []),
        refusal_flags=[str(flag) for flag in (refusal_flags or [])],
        freshness_requirements=dict(freshness_requirements or {}),
        resume=dict(resume or {}),
        delivery=dict(delivery or {}),
        limits=dict(limits or {}),
        recurrent_task_state=dict(recurrent_task_state or {}),
        metadata=dict(metadata or {}),
    )


ALLOWED_GOAL_BOUNDARY_TYPES = frozenset({
    "lark_task_ids",
    "taskpool_task_ids",
    "checklist_ids",
    "acceptance_commands",
    "requirement_ids",
})
OPEN_ENDED_GOAL_BOUNDARY_TYPES = frozenset({"open_ended", "until_satisfied", "natural_language"})


def _packet_payload(packet: ContinuationPacket | Mapping[str, Any]) -> dict[str, Any]:
    return packet.to_dict() if isinstance(packet, ContinuationPacket) else dict(packet)


def _int_limit(limits: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(limits.get(key, default))
    except Exception:
        return default


def _goal_boundary_has_machine_payload(boundary: Mapping[str, Any]) -> bool:
    boundary_type = str(boundary.get("type") or "")
    if boundary_type in {"lark_task_ids", "taskpool_task_ids"}:
        return bool(boundary.get("ids") or boundary.get("task_ids"))
    if boundary_type == "checklist_ids":
        return bool(boundary.get("ids") or boundary.get("checklist_ids"))
    if boundary_type == "acceptance_commands":
        return bool(boundary.get("commands"))
    if boundary_type == "requirement_ids":
        return bool(boundary.get("ids") or boundary.get("requirement_ids"))
    return False


def evaluate_continuation_eligibility(
    packet: ContinuationPacket | Mapping[str, Any],
    *,
    goal_boundary_enabled: bool = False,
) -> dict[str, Any]:
    """Return a fail-closed continuation decision for relay-refresh gates.

    Natural-language summaries are non-authoritative. Automatic continuation
    needs durable packet fields: remaining work, evidence, bounded limits, and
    for goal-bound multi-refresh a machine-verifiable closure boundary.
    """
    payload = _packet_payload(packet)
    reasons: list[str] = []
    advisory: list[str] = []
    limits = payload.get("limits") if isinstance(payload.get("limits"), Mapping) else {}
    boundary = payload.get("goal_boundary") if isinstance(payload.get("goal_boundary"), Mapping) else {}
    boundary_type = str(boundary.get("type") or "")

    if payload.get("schema") not in (None, SCHEMA):
        reasons.append("invalid_schema")
    if not payload.get("scope"):
        reasons.append("missing_scope")
    if not payload.get("reason"):
        reasons.append("missing_reason")
    if not payload.get("remaining_work"):
        reasons.append("missing_remaining_work")
    if not payload.get("evidence"):
        reasons.append("missing_evidence")
    for flag in payload.get("refusal_flags") or []:
        reasons.append(f"refusal_flag:{flag}")

    refresh_count = _int_limit(limits, "refresh_count", _int_limit(limits, "relay_index", 0))
    max_refreshes = _int_limit(limits, "max_refreshes_per_task", 1)
    global_count = _int_limit(limits, "global_refresh_count", refresh_count)
    global_cap = _int_limit(limits, "global_hard_cap", 3)
    if refresh_count >= max_refreshes:
        reasons.append("task_refresh_cap_exhausted")
    if global_count >= global_cap:
        reasons.append("global_hard_cap_exhausted")

    if boundary:
        if boundary_type in OPEN_ENDED_GOAL_BOUNDARY_TYPES:
            reasons.append("open_ended_goal_boundary")
        elif boundary_type not in ALLOWED_GOAL_BOUNDARY_TYPES:
            reasons.append("unsupported_goal_boundary")
        if not boundary.get("closure_criteria"):
            reasons.append("missing_closure_criteria")
        if not _goal_boundary_has_machine_payload(boundary):
            reasons.append("missing_machine_goal_payload")
        if boundary_type == "lark_task_ids" and not payload.get("freshness_requirements", {}).get("lark"):
            reasons.append("missing_lark_freshness_requirement")
        if max_refreshes > 1 and not goal_boundary_enabled:
            advisory.append("goal_bound_disabled")
    elif max_refreshes > 1:
        reasons.append("missing_goal_boundary")

    if reasons:
        decision = "refuse"
    elif advisory:
        decision = "recommend_only"
    else:
        decision = "continue"
    return {
        "schema": "hermes_continuation_eligibility_v1",
        "decision": decision,
        "reasons": reasons + advisory,
        "boundary_type": boundary_type,
        "refresh_count": refresh_count,
        "max_refreshes_per_task": max_refreshes,
        "global_refresh_count": global_count,
        "global_hard_cap": global_cap,
        "summary_authority": "non_authoritative",
    }


def build_clean_session_handoff(
    packet: ContinuationPacket | Mapping[str, Any],
    *,
    compression_cap: int = 3,
) -> ContinuationPacket:
    """Build a packet-backed clean-session handoff after compression is capped."""
    payload = _packet_payload(packet)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    try:
        compression_attempts = int(metadata.get("compression_attempts", 0) or 0)
    except Exception:
        compression_attempts = 0
    if compression_attempts < int(compression_cap):
        raise ValueError("compression_cap_not_reached")
    decision = evaluate_continuation_eligibility(payload, goal_boundary_enabled=True)
    if decision["decision"] != "continue":
        raise ValueError("continuation_not_eligible:" + ",".join(decision["reasons"]))
    handoff_metadata = dict(metadata)
    handoff_metadata.update({
        "handoff_summary": str(payload.get("summary") or ""),
        "summary_authority": "non_authoritative",
        "clean_session_start": True,
        "parent_scope": payload.get("scope"),
    })
    return build_continuation_packet(
        scope="clean_session_handoff",
        reason=payload.get("raw_reason") or payload.get("reason") or "context_compression_cap",
        summary="Resume from structured continuation packet; prior summary is non-authoritative metadata.",
        session_id=None,
        parent_session_id=payload.get("session_id"),
        task_id=payload.get("task_id"),
        parent_packet_id=payload.get("packet_id"),
        model=payload.get("model"),
        provider=payload.get("provider"),
        remaining_work=list(payload.get("remaining_work") or []),
        completed_work=list(payload.get("completed_work") or []),
        evidence=list(payload.get("evidence") or []),
        goal_boundary=payload.get("goal_boundary") or {},
        side_effect_ledger=list(payload.get("side_effect_ledger") or []),
        refusal_flags=list(payload.get("refusal_flags") or []),
        freshness_requirements=payload.get("freshness_requirements") or {},
        resume={"mode": "clean_session", "executor": "conversation_loop"},
        delivery=payload.get("delivery") or {},
        limits=payload.get("limits") or {},
        recurrent_task_state=payload.get("recurrent_task_state") or {},
        metadata=handoff_metadata,
    )


def write_continuation_packet(packet: ContinuationPacket | Mapping[str, Any], *, root: Path | str | None = None, state: str = "active") -> Path:
    if state not in STATES:
        raise ValueError(f"invalid continuation state: {state}")
    payload = packet.to_dict() if isinstance(packet, ContinuationPacket) else dict(packet)
    base = Path(root).expanduser() if root is not None else continuation_root()

    if state == "active":
        active_dir = base / "active"
        active_count = len(list(active_dir.glob("*.json"))) if active_dir.exists() else 0
        if active_count >= MAX_ACTIVE_CONTINUATIONS:
            state = "archived"
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["overflow_archived"] = True
            payload["metadata"] = metadata

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
    safe_idx = _safe_slug(str(idx))
    path = out_dir / f"relay-{session_id}-{safe_idx}.json"
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
