"""System-triggered self-check and governed self-healing operator.

This module is deliberately *not* a CLI-first surface.  It is a small,
import-safe control-plane seam that runtime boundaries can call when something
interesting happens: a user asks for self-check, a turn ends abnormally, a cron
job fails, a provider/tool path degrades, etc.

The design follows the MAPE-K split:

* Monitor: runtime code calls :func:`observe_event` / :func:`observe_turn_result`.
* Analyze: events are fingerprinted, de-bounced, and classified into findings.
* Plan: only registry-backed, side-effect-free repair plans are emitted here.
* Execute: intentionally closed in this module.  Plans wait for a governed
  executor / human judgement before any non-detect action runs.
* Knowledge: append-only JSONL ledger under ``$HERMES_HOME/state/selfcheck``.

The operator is safe to call from hot paths: all filesystem work is best-effort,
small, append-only, and protected by an in-process lock.  It must never block a
user response or mutate external state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "hermes_selfcheck_operator_v1"
_LEDGER_LOCK = threading.Lock()

# Bounded de-bounce: repeated identical findings inside this window are folded
# into a coalesced ledger event instead of producing repair-plan spam.
_DEFAULT_DEBOUNCE_SECONDS = 300


@dataclass(frozen=True)
class Finding:
    id: str
    component: str
    severity: str
    fingerprint: str
    trigger: str
    symptom: str
    confidence: float
    freshness: str
    blocks_what: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    auto_fix_level: str = "L0"
    recommended_action: str = "observe"
    safety_note: str = "detect-only; no side effects were executed"


@dataclass(frozen=True)
class RepairPlan:
    id: str
    finding_id: str
    action_id: str
    safety_level: str
    status: str
    required_authorization: str
    preconditions: list[str]
    verification: list[str]
    rollback: str
    request_digest: str
    rationale: str


@dataclass(frozen=True)
class RepairAttempt:
    id: str
    plan_id: str
    action_id: str
    mode: str
    status: str
    started_at: str
    completed_at: str
    request_digest: str
    before: dict[str, Any]
    after: dict[str, Any]
    verification: list[str]
    note: str


@dataclass(frozen=True)
class OperatorDecision:
    event_id: str
    accepted: bool
    suppressed: bool
    reason: str
    findings: list[Finding] = field(default_factory=list)
    repair_plans: list[RepairPlan] = field(default_factory=list)
    ledger_dir: str = ""


def _enabled() -> bool:
    raw = os.environ.get("HERMES_SELFCHECK_OPERATOR", "").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return False
    return True


def _now() -> float:
    return time.time()


def _utc_ts(ts: float | None = None) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts or _now(), tz=_dt.timezone.utc).isoformat()


def ledger_dir() -> Path:
    return get_hermes_home() / "state" / "selfcheck"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return str(value)


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write a compact projection file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_jsonl_tail(path: Path, *, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _read_recent_fingerprints(path: Path, *, window_seconds: int) -> set[str]:
    cutoff = _now() - max(1, int(window_seconds))
    seen: set[str] = set()
    try:
        if not path.exists():
            return seen
        # Keep hot-path overhead bounded: inspect only a tail slice.
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if float(rec.get("created_at_epoch") or 0) < cutoff:
                continue
            fp = str(rec.get("fingerprint") or "").strip()
            if fp:
                seen.add(fp)
    except OSError:
        return seen
    return seen


def _stable_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sanitize_text(text: Any, limit: int = 600) -> str:
    raw = str(text or "")
    raw = re.sub(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+", r"\1=<redacted>", raw)
    raw = raw.replace("\x00", "")
    raw = " ".join(raw.split())
    return raw[:limit]


def _event_id(event: Mapping[str, Any]) -> str:
    payload = {
        "event_type": event.get("event_type"),
        "trigger": event.get("trigger"),
        "component": event.get("component"),
        "session_id": event.get("session_id"),
        "task_id": event.get("task_id"),
        "turn_id": event.get("turn_id"),
        "fingerprint_hint": event.get("fingerprint_hint"),
        "summary": _sanitize_text(event.get("summary"), 160),
    }
    return _stable_digest(payload)


def _finding_id(fingerprint: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{fingerprint}|{event_id}".encode("utf-8")).hexdigest()[:16]
    return f"finding-{digest}"


def _classify_event(event: Mapping[str, Any]) -> list[Finding]:
    event_type = str(event.get("event_type") or "unknown")
    trigger = str(event.get("trigger") or event_type)
    component = str(event.get("component") or "runtime")
    event_id = str(event.get("event_id") or _event_id(event))
    summary = _sanitize_text(event.get("summary") or event.get("error") or event_type)
    severity_hint = str(event.get("severity") or "").upper()
    evidence_refs = [str(x) for x in event.get("evidence_refs") or [] if x]

    findings: list[Finding] = []

    def add(
        *,
        fp_suffix: str,
        severity: str,
        symptom: str,
        confidence: float,
        blocks: Sequence[str] = (),
        level: str = "L0",
        action: str = "observe",
        note: str = "detect-only; no side effects were executed",
    ) -> None:
        fingerprint = f"{component}.{fp_suffix}"
        findings.append(
            Finding(
                id=_finding_id(fingerprint, event_id),
                component=component,
                severity=severity_hint if severity_hint in {"P0", "P1", "P2", "P3"} else severity,
                fingerprint=fingerprint,
                trigger=trigger,
                symptom=symptom,
                confidence=confidence,
                freshness="current",
                blocks_what=list(blocks),
                evidence_refs=evidence_refs,
                auto_fix_level=level,
                recommended_action=action,
                safety_note=note,
            )
        )

    text = " ".join(str(event.get(k) or "") for k in ("summary", "error", "turn_exit_reason", "detail")).lower()

    if event_type == "conversation.turn_end":
        turn_exit = str(event.get("turn_exit_reason") or "unknown")
        completed = bool(event.get("completed"))
        interrupted = bool(event.get("interrupted"))
        last_role = str(event.get("last_msg_role") or "")
        response_len = int(event.get("response_len") or 0)
        if interrupted:
            add(
                fp_suffix="conversation_interrupted",
                severity="P3",
                symptom="Conversation turn was interrupted before normal completion.",
                confidence=0.8,
                blocks=("conversation_completion",),
                action="record interruption; do not sync stale completion state",
            )
        if (not completed) and not interrupted:
            add(
                fp_suffix=f"abnormal_exit.{_slug(turn_exit)}",
                severity="P1" if turn_exit.startswith(("budget", "max_iterations")) else "P2",
                symptom=f"Conversation ended abnormally: {turn_exit}.",
                confidence=0.86,
                blocks=("conversation_completion",),
                action="inspect turn evidence and continue/relay before claiming completion",
            )
        if last_role == "tool":
            add(
                fp_suffix="pending_tool_result",
                severity="P1",
                symptom="Turn ended with a tool result as the tail message; the agent may have stopped mid-work.",
                confidence=0.9,
                blocks=("task_completion", "user_feedback"),
                action="require continuation or explicit handoff; do not claim done",
            )
        if response_len == 0 and not interrupted:
            add(
                fp_suffix="empty_final_response",
                severity="P1",
                symptom="Turn produced no user-visible final response.",
                confidence=0.88,
                blocks=("user_feedback",),
                action="classify provider/model failure and retry/fallback with evidence",
            )
        if event.get("failed_file_mutations"):
            add(
                fp_suffix="failed_file_mutations",
                severity="P1",
                symptom="One or more file mutation tools failed in the turn; completion claims require verification.",
                confidence=0.92,
                blocks=("code_change_verification",),
                action="read back git/files and repair failed edits before claiming done",
            )
        if _looks_like_selfcheck_request(event.get("user_message")):
            add(
                fp_suffix="user_requested_selfcheck",
                severity="P2",
                symptom="User explicitly requested self-check; scoped operator audit should be surfaced.",
                confidence=0.95,
                blocks=(),
                action="run scoped self-check report from ledger and current state",
            )

    elif event_type.startswith("cron."):
        if any(s in event_type for s in ("failed", "delivery_failed", "empty_response")) or "failed" in text:
            add(
                fp_suffix=_slug(event_type.replace("cron.", "")),
                severity="P1",
                symptom=f"Cron path degraded: {summary}",
                confidence=0.86,
                blocks=("scheduled_delivery",),
                action="classify scheduler/job/delivery/grace-window layer before replaying",
            )
        elif "miss" in text or "skip" in text:
            add(
                fp_suffix="missed_or_skipped_run",
                severity="P2",
                symptom=f"Cron run may have been missed or skipped: {summary}",
                confidence=0.75,
                blocks=("scheduled_delivery",),
                action="correlate scheduler uptime, grace window, and delivery target",
            )

    elif event_type.startswith("provider.") or component == "provider":
        add(
            fp_suffix=_slug(event.get("fingerprint_hint") or event_type),
            severity="P1" if any(x in text for x in ("auth", "quota", "client closed", "context", "image")) else "P2",
            symptom=f"Provider/model path degraded: {summary}",
            confidence=0.8,
            blocks=("model_call",),
            action="classify auth/quota/context/client/image-routing before fallback changes",
        )

    elif event_type.startswith("gateway.") or component == "gateway":
        severity = "P1" if any(x in event_type for x in ("send_failed", "delivery_failed")) else "P2"
        add(
            fp_suffix=_slug(event.get("fingerprint_hint") or event.get("platform") or event_type),
            severity=severity,
            symptom=f"Gateway delivery path degraded: {summary}",
            confidence=0.84,
            blocks=("external_delivery", "user_notification"),
            action="classify target/config/adapter/platform failure before replaying delivery",
        )

    elif event_type.startswith("taskpool.") or component == "taskpool":
        if any(x in text for x in ("stale", "claim", "running", "worker failed", "transition rejected", "done without proof")):
            severity = "P1"
        elif any(x in text for x in ("dispatchable=0", "no_dispatchable", "blocked", "eligibility")):
            severity = "P2"
        else:
            severity = "P3"
        add(
            fp_suffix=_slug(event.get("fingerprint_hint") or event_type),
            severity=severity,
            symptom=f"Taskpool control-plane signal: {summary}",
            confidence=0.82,
            blocks=("taskpool_progress",),
            action="diagnose canonical ledger/adapters/proof gates before mutating task lifecycle",
        )

    elif event_type.startswith("tool.") or component == "tool":
        add(
            fp_suffix=_slug(event.get("tool_name") or event_type),
            severity="P2",
            symptom=f"Tool path degraded: {summary}",
            confidence=0.78,
            blocks=("tool_execution",),
            action="fingerprint failure, retry only if idempotent, otherwise ask for scoped repair",
        )

    return findings


def _slug(value: Any) -> str:
    text = str(value or "unknown").lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("_")
    return text[:80] or "unknown"


def _looks_like_selfcheck_request(value: Any) -> bool:
    text = str(value or "").lower()
    needles = (
        "自检", "检查一下", "系统健康", "哪里卡", "没执行", "没发", "为什么", "self-check", "selfcheck",
    )
    return any(n in text for n in needles)


def _plan_for_finding(finding: Finding) -> RepairPlan | None:
    """Produce only governed, non-executing plans.

    L1 actions are intentionally not executed here because this operator is
    embedded in hot runtime boundaries.  Execution belongs to a separate,
    registry-backed executor after human/policy judgement.
    """
    if finding.auto_fix_level not in {"L1", "L2", "L3"}:
        # Still create a judgement-waiting plan for P1/P0 so operators see the
        # next governed action without any mutation happening automatically.
        if finding.severity not in {"P0", "P1"}:
            return None
        action_id = f"diagnose.{finding.fingerprint}"
        safety = "L0"
    else:
        action_id = f"repair.{finding.fingerprint}"
        safety = finding.auto_fix_level

    payload = {
        "finding_id": finding.id,
        "action_id": action_id,
        "fingerprint": finding.fingerprint,
        "safety_level": safety,
        "recommended_action": finding.recommended_action,
    }
    digest = _stable_digest(payload)
    return RepairPlan(
        id="plan-" + hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16],
        finding_id=finding.id,
        action_id=action_id,
        safety_level=safety,
        status="waiting_for_judgement",
        required_authorization="operator_policy_or_human" if safety != "L0" else "none_detect_only",
        preconditions=[
            "finding is still current",
            "evidence_refs are readable or the current state can be re-read",
            "no concurrent repair lock exists for this component/fingerprint",
        ],
        verification=[
            "read back component state after any future action",
            "append repair_attempt with before/after evidence",
            "do not mark lifecycle done from this plan alone",
        ],
        rollback="required for L2/L3; none executed by this embedded operator",
        request_digest=digest,
        rationale=finding.recommended_action,
    )


def _severity_rank(severity: Any) -> int:
    return {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(str(severity or "").upper(), 0)


def _status_for_rank(rank: int) -> str:
    if rank >= 4:
        return "red"
    if rank >= 3:
        return "yellow"
    if rank >= 1:
        return "watch"
    return "green"


def _rebuild_projections(root: Path, *, now_epoch: float | None = None) -> None:
    """Maintain read-optimized projections from the append-only ledger.

    Projection writes are best-effort and derived; the ledger remains the source
    of truth.  Keeping this inside the operator gives resident surfaces a stable
    status artifact without requiring a CLI command to aggregate JSONL.
    """
    ts = now_epoch or _now()
    findings = _read_jsonl_tail(root / "findings.jsonl", limit=500)
    plans = _read_jsonl_tail(root / "repair_plans.jsonl", limit=500)
    attempts = _read_jsonl_tail(root / "repair_attempts.jsonl", limit=500)
    recent_events = _read_jsonl_tail(root / "events.jsonl", limit=200)

    closed_plan_ids = {
        str(a.get("plan_id"))
        for a in attempts
        if a.get("status") in {"dry_run_verified", "applied_verified", "superseded"}
    }
    waiting_plans = [
        p for p in plans
        if p.get("status") == "waiting_for_judgement" and str(p.get("id")) not in closed_plan_ids
    ]

    by_component: dict[str, dict[str, Any]] = {}
    for finding in findings:
        component = str(finding.get("component") or "runtime")
        item = by_component.setdefault(
            component,
            {
                "component": component,
                "status": "green",
                "highest_severity": "",
                "highest_rank": 0,
                "open_findings": 0,
                "latest_finding_id": "",
                "latest_fingerprint": "",
                "latest_seen_at": "",
                "blocks_what": [],
            },
        )
        rank = _severity_rank(finding.get("severity"))
        item["open_findings"] += 1
        if rank >= int(item.get("highest_rank") or 0):
            item["highest_rank"] = rank
            item["highest_severity"] = finding.get("severity") or ""
            item["status"] = _status_for_rank(rank)
            item["latest_finding_id"] = finding.get("id") or ""
            item["latest_fingerprint"] = finding.get("fingerprint") or ""
            item["latest_seen_at"] = finding.get("created_at") or ""
        blocks = item.setdefault("blocks_what", [])
        for block in finding.get("blocks_what") or []:
            if block not in blocks:
                blocks.append(block)

    health_rank = max((int(v.get("highest_rank") or 0) for v in by_component.values()), default=0)
    summary = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _utc_ts(ts),
        "generated_at_epoch": ts,
        "overall_status": _status_for_rank(health_rank),
        "component_count": len(by_component),
        "finding_count_tail": len(findings),
        "waiting_for_judgement": len(waiting_plans),
        "recent_event_count_tail": len(recent_events),
        "components": sorted(by_component),
    }
    latest = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _utc_ts(ts),
        "findings": findings[-50:],
    }
    component_status = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _utc_ts(ts),
        "components": {
            key: {k: v for k, v in value.items() if k != "highest_rank"}
            for key, value in sorted(by_component.items())
        },
    }
    _write_json(root / "health_summary.json", summary)
    _write_json(root / "latest_findings.json", latest)
    _write_json(root / "component_status.json", component_status)


def _safe_read_path(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": exc.__class__.__name__}
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _dry_run_action(plan: Mapping[str, Any], root: Path) -> tuple[dict[str, Any], dict[str, Any], list[str], str]:
    """Run a governed dry-run/readback action without mutating external state."""
    action_id = str(plan.get("action_id") or "")
    before = {
        "ledger_dir": _safe_read_path(root),
        "findings": _safe_read_path(root / "findings.jsonl"),
        "plans": _safe_read_path(root / "repair_plans.jsonl"),
        "attempts": _safe_read_path(root / "repair_attempts.jsonl"),
    }
    after = dict(before)
    verification = [
        "dry-run only: no shell command executed",
        "dry-run only: no external message sent",
        "dry-run only: no task lifecycle or adapter state mutated",
        f"plan action_id observed: {action_id}",
    ]
    note = "diagnostic dry-run/readback completed; awaiting human/policy judgement for any real repair"
    return before, after, verification, note


def run_repair_plan_dry_run(plan_id: str, *, request_digest: str | None = None) -> RepairAttempt:
    """Append a governed repair_attempt for a plan without executing side effects.

    This is the first executor boundary: it consumes an exact plan id (and,
    when provided, an exact digest), performs only local readback, and records a
    repair_attempt.  Real L1+ mutation executors must be separate allowlisted
    actions with before/after verification and authorization.
    """
    root = ledger_dir()
    ts = _now()
    plans = _read_jsonl_tail(root / "repair_plans.jsonl", limit=1000)
    plan = next((p for p in reversed(plans) if str(p.get("id")) == str(plan_id)), None)
    if plan is None:
        raise ValueError(f"repair plan not found: {plan_id}")
    expected_digest = str(plan.get("request_digest") or "")
    if request_digest and request_digest != expected_digest:
        raise ValueError("repair plan digest mismatch")
    before, after, verification, note = _dry_run_action(plan, root)
    attempt_payload = {
        "plan_id": plan_id,
        "action_id": plan.get("action_id"),
        "mode": "dry_run",
        "request_digest": expected_digest,
        "started_at": _utc_ts(ts),
    }
    attempt = RepairAttempt(
        id="attempt-" + hashlib.sha256(json.dumps(attempt_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16],
        plan_id=plan_id,
        action_id=str(plan.get("action_id") or ""),
        mode="dry_run",
        status="dry_run_verified",
        started_at=_utc_ts(ts),
        completed_at=_utc_ts(ts),
        request_digest=expected_digest,
        before=before,
        after=after,
        verification=verification,
        note=note,
    )
    with _LEDGER_LOCK:
        record = asdict(attempt)
        record.update({
            "schema_version": _SCHEMA_VERSION,
            "created_at": _utc_ts(ts),
            "created_at_epoch": ts,
        })
        _append_jsonl(root / "repair_attempts.jsonl", record)
        _rebuild_projections(root, now_epoch=ts)
    return attempt


def observe_event(event: Mapping[str, Any], *, debounce_seconds: int | None = None) -> OperatorDecision:
    """Ingest a runtime event and append derived findings/plans to the ledger.

    This is the primary system-level trigger API.  It is safe by construction:
    it never runs shell commands, never mutates adapters, never sends messages,
    and never advances lifecycle facts.  It only writes append-only operator
    ledger records.
    """
    root = ledger_dir()
    event_id = _event_id(event)
    if not _enabled():
        return OperatorDecision(event_id=event_id, accepted=False, suppressed=True, reason="operator_disabled", ledger_dir=str(root))

    ts = _now()
    normalized = dict(event)
    normalized.update({
        "schema_version": _SCHEMA_VERSION,
        "event_id": event_id,
        "created_at": _utc_ts(ts),
        "created_at_epoch": ts,
    })
    normalized["summary"] = _sanitize_text(normalized.get("summary") or normalized.get("error") or normalized.get("event_type"))

    findings = _classify_event(normalized)
    plans = [p for f in findings for p in [_plan_for_finding(f)] if p is not None]

    debounce = _DEFAULT_DEBOUNCE_SECONDS if debounce_seconds is None else max(0, int(debounce_seconds))
    with _LEDGER_LOCK:
        try:
            _append_jsonl(root / "events.jsonl", normalized)
            recent = _read_recent_fingerprints(root / "findings.jsonl", window_seconds=debounce) if debounce else set()
            emitted_findings: list[Finding] = []
            emitted_plans: list[RepairPlan] = []
            for finding in findings:
                record = asdict(finding)
                record.update({
                    "schema_version": _SCHEMA_VERSION,
                    "event_id": event_id,
                    "created_at": _utc_ts(ts),
                    "created_at_epoch": ts,
                })
                if debounce and finding.fingerprint in recent:
                    _append_jsonl(root / "coalesced.jsonl", {
                        "schema_version": _SCHEMA_VERSION,
                        "event_id": event_id,
                        "finding_id": finding.id,
                        "fingerprint": finding.fingerprint,
                        "component": finding.component,
                        "created_at": _utc_ts(ts),
                        "created_at_epoch": ts,
                        "reason": "debounced_recent_fingerprint",
                    })
                    continue
                _append_jsonl(root / "findings.jsonl", record)
                emitted_findings.append(finding)
                recent.add(finding.fingerprint)
            emitted_ids = {f.id for f in emitted_findings}
            for plan in plans:
                if plan.finding_id not in emitted_ids:
                    continue
                rec = asdict(plan)
                rec.update({
                    "schema_version": _SCHEMA_VERSION,
                    "event_id": event_id,
                    "created_at": _utc_ts(ts),
                    "created_at_epoch": ts,
                })
                _append_jsonl(root / "repair_plans.jsonl", rec)
                emitted_plans.append(plan)
            _rebuild_projections(root, now_epoch=ts)
            return OperatorDecision(
                event_id=event_id,
                accepted=True,
                suppressed=False,
                reason="recorded",
                findings=emitted_findings,
                repair_plans=emitted_plans,
                ledger_dir=str(root),
            )
        except Exception as exc:  # pragma: no cover - defensive hot-path guard
            logger.debug("selfcheck operator ingest failed: %s", exc, exc_info=True)
            return OperatorDecision(event_id=event_id, accepted=False, suppressed=True, reason=f"write_failed:{exc.__class__.__name__}", ledger_dir=str(root))


def observe_turn_result(
    *,
    agent: Any,
    result: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]] | None = None,
    user_message: Any = None,
    original_user_message: Any = None,
    effective_task_id: str | None = None,
    turn_id: str | None = None,
    failed_file_mutations: Mapping[str, Any] | None = None,
) -> OperatorDecision:
    """System-level conversation finalizer trigger."""
    msg_list = list(messages or result.get("messages") or [])
    last_role = ""
    if msg_list and isinstance(msg_list[-1], Mapping):
        last_role = str(msg_list[-1].get("role") or "")
    evidence_refs = []
    session_id = str(result.get("session_id") or getattr(agent, "session_id", "") or "")
    if session_id:
        evidence_refs.append(f"session:{session_id}")
    if turn_id:
        evidence_refs.append(f"turn:{turn_id}")
    event = {
        "event_type": "conversation.turn_end",
        "trigger": "lifecycle_boundary",
        "component": "conversation",
        "session_id": session_id,
        "task_id": effective_task_id or result.get("task_id") or getattr(agent, "task_id", None),
        "turn_id": turn_id,
        "model": result.get("model") or getattr(agent, "model", ""),
        "provider": result.get("provider") or getattr(agent, "provider", ""),
        "completed": bool(result.get("completed")),
        "failed": bool(result.get("failed")),
        "interrupted": bool(result.get("interrupted")),
        "turn_exit_reason": result.get("turn_exit_reason") or "unknown",
        "api_calls": result.get("api_calls"),
        "max_iterations": getattr(agent, "max_iterations", None),
        "last_msg_role": last_role,
        "response_len": len(str(result.get("final_response") or "")),
        "failed_file_mutations": bool(failed_file_mutations),
        "summary": f"turn ended reason={result.get('turn_exit_reason') or 'unknown'} completed={bool(result.get('completed'))}",
        "user_message": _sanitize_text(original_user_message if original_user_message is not None else user_message, 400),
        "evidence_refs": evidence_refs,
    }
    return observe_event(event)


def observe_cron_job_result(
    *,
    job: Mapping[str, Any],
    success: bool,
    error: str | None = None,
    delivery_error: str | None = None,
    output_file: str | None = None,
    final_response: str | None = None,
) -> OperatorDecision:
    """System-level cron trigger used by the scheduler."""
    event_type = "cron.job_completed"
    summary_parts: list[str] = []
    if not success:
        event_type = "cron.job_failed"
        summary_parts.append(error or "job failed")
    if delivery_error:
        event_type = "cron.delivery_failed"
        summary_parts.append(f"delivery_error={delivery_error}")
    if success and not str(final_response or "").strip():
        event_type = "cron.empty_response"
        summary_parts.append("agent completed with empty response")
    event = {
        "event_type": event_type,
        "trigger": "cron_scheduler",
        "component": "cron",
        "job_id": job.get("id"),
        "job_name": job.get("name"),
        "success": bool(success),
        "summary": "; ".join(summary_parts) or "cron job completed",
        "error": error,
        "delivery_error": delivery_error,
        "evidence_refs": [x for x in [f"cron_job:{job.get('id')}", f"file:{output_file}" if output_file else None] if x],
    }
    return observe_event(event)


def observe_provider_error(
    *,
    provider: str | None = None,
    model: str | None = None,
    error: Any = None,
    summary: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    turn_id: str | None = None,
    api_call_count: int | None = None,
    fingerprint_hint: str | None = None,
) -> OperatorDecision:
    """System-level provider/router degradation trigger.

    Provider exceptions can occur before a normal turn result exists.  This seam
    lets the runtime record auth/quota/context/client/image-routing failures in
    the same ledger and projections as turn/tool/cron findings.
    """
    text = _sanitize_text(summary or error or "provider error")
    event = {
        "event_type": "provider.error",
        "trigger": "provider_exception",
        "component": "provider",
        "provider": provider or "",
        "model": model or "",
        "session_id": session_id or "",
        "task_id": task_id or "",
        "turn_id": turn_id or "",
        "api_calls": api_call_count,
        "fingerprint_hint": fingerprint_hint or _slug(text),
        "summary": text,
        "error": text,
        "evidence_refs": [x for x in [f"session:{session_id}" if session_id else None, f"turn:{turn_id}" if turn_id else None] if x],
    }
    return observe_event(event)


def observe_gateway_delivery(
    *,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    success: bool = False,
    error: Any = None,
    summary: str | None = None,
    source: str | None = None,
) -> OperatorDecision:
    """System-level gateway/send-message delivery trigger."""
    if success:
        event_type = "gateway.send_completed"
        text = _sanitize_text(summary or "gateway send completed")
    else:
        event_type = "gateway.send_failed"
        text = _sanitize_text(summary or error or "gateway send failed")
    event = {
        "event_type": event_type,
        "trigger": source or "gateway_delivery",
        "component": "gateway",
        "platform": platform or "",
        "chat_id": _sanitize_text(chat_id, 120),
        "thread_id": _sanitize_text(thread_id, 120),
        "success": bool(success),
        "fingerprint_hint": _slug(f"{platform or 'unknown'}.{text}"),
        "summary": text,
        "error": None if success else text,
        "evidence_refs": [x for x in [f"gateway_platform:{platform}" if platform else None] if x],
    }
    return observe_event(event)


def observe_taskpool_event(
    *,
    event_type: str,
    summary: str,
    task_id: str | None = None,
    status: str | None = None,
    actor: str | None = None,
    fingerprint_hint: str | None = None,
    evidence_refs: Sequence[str] | None = None,
) -> OperatorDecision:
    """System-level taskpool/control-plane trigger for external scripts/hooks."""
    normalized_event_type = event_type if event_type.startswith("taskpool.") else f"taskpool.{event_type}"
    event = {
        "event_type": normalized_event_type,
        "trigger": "taskpool_event",
        "component": "taskpool",
        "task_id": task_id or "",
        "status": status or "",
        "actor": actor or "",
        "fingerprint_hint": fingerprint_hint or _slug(normalized_event_type),
        "summary": _sanitize_text(summary),
        "evidence_refs": [str(x) for x in evidence_refs or [] if x],
    }
    return observe_event(event)


def read_recent_findings(*, limit: int = 20) -> list[dict[str, Any]]:
    """Read recent findings for reports/tests without adding a CLI dependency."""
    return _read_jsonl_tail(ledger_dir() / "findings.jsonl", limit=limit)


def read_health_summary() -> dict[str, Any]:
    """Read the resident health projection if one has been generated."""
    try:
        data = json.loads((ledger_dir() / "health_summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


__all__ = [
    "Finding",
    "OperatorDecision",
    "RepairAttempt",
    "RepairPlan",
    "ledger_dir",
    "observe_cron_job_result",
    "observe_event",
    "observe_gateway_delivery",
    "observe_provider_error",
    "observe_taskpool_event",
    "observe_turn_result",
    "read_health_summary",
    "read_recent_findings",
    "run_repair_plan_dry_run",
]
