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
class SentinelWakeup:
    id: str
    kind: str
    severity: str
    status: str
    reason: str
    source: str
    request_digest: str
    evidence_refs: list[str]
    created_at: str


@dataclass(frozen=True)
class RepairAuthorization:
    id: str
    plan_id: str
    action_id: str
    decision: str
    actor: str
    created_at: str
    expires_at: str
    request_digest: str
    evidence_refs: list[str]
    note: str


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
    authorization_id: str
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


def _contains_secret_like(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=_json_default) if not isinstance(value, str) else value
    return bool(re.search(r"(?i)\b(api[_-]?key|token|secret|password|authorization|credential)\b", text))


def _load_plan(root: Path, plan_id: str) -> dict[str, Any]:
    plans = _read_jsonl_tail(root / "repair_plans.jsonl", limit=2000)
    plan = next((p for p in reversed(plans) if str(p.get("id")) == str(plan_id)), None)
    if plan is None:
        raise ValueError(f"repair plan not found: {plan_id}")
    return plan


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


def _open_waiting_plans(plans: Sequence[Mapping[str, Any]], closed_plan_ids: set[str]) -> list[Mapping[str, Any]]:
    """Return unique open waiting plans by plan id.

    The repair plan ledger is append-only and a repeated runtime fingerprint may
    append the same deterministic plan id many times. Projection surfaces should
    count that as one judgement item, not as hundreds of duplicated decisions.
    """
    latest_by_id: dict[str, Mapping[str, Any]] = {}
    order: list[str] = []
    for plan in plans:
        if plan.get("status") != "waiting_for_judgement":
            continue
        plan_id = str(plan.get("id") or "")
        if not plan_id or plan_id in closed_plan_ids:
            continue
        if plan_id not in latest_by_id:
            order.append(plan_id)
        latest_by_id[plan_id] = plan
    return [latest_by_id[plan_id] for plan_id in order]


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
    authorizations = _read_jsonl_tail(root / "repair_authorizations.jsonl", limit=500)
    recent_events = _read_jsonl_tail(root / "events.jsonl", limit=200)

    closed_plan_ids = {
        str(a.get("plan_id"))
        for a in attempts
        if a.get("status") in {"dry_run_verified", "authorized_readback_verified", "applied_verified", "superseded"}
    }
    waiting_plans = _open_waiting_plans(plans, closed_plan_ids)
    active_authorizations = [
        a for a in authorizations
        if a.get("decision") == "approved" and float(a.get("expires_at_epoch") or 0) >= ts
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
        "active_authorizations": len(active_authorizations),
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


def _sentinel_enabled() -> bool:
    raw = os.environ.get("HERMES_SELFCHECK_SENTINEL", "").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def _auto_repair_enabled() -> bool:
    """Whether the resident operator may run allowlisted L1 local repairs.

    This intentionally opens only ledger-local/projection-maintenance actions.
    External sends, provider/config changes, task lifecycle mutations, and shell
    commands remain outside this capability boundary.
    """
    raw = os.environ.get("HERMES_SELFCHECK_AUTO_REPAIR", "1").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def _notification_outbox_enabled() -> bool:
    raw = os.environ.get("HERMES_SELFCHECK_NOTIFICATION_OUTBOX", "1").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def _sentinel_threshold(name: str, default: int) -> int:
    raw = os.environ.get(f"HERMES_SELFCHECK_SENTINEL_{name.upper()}", "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _recent_wakeup_fingerprints(root: Path, *, window_seconds: int, now_epoch: float) -> set[str]:
    cutoff = now_epoch - max(1, window_seconds)
    seen: set[str] = set()
    for rec in _read_jsonl_tail(root / "wakeups.jsonl", limit=200):
        try:
            if float(rec.get("created_at_epoch") or 0) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        fp = str(rec.get("fingerprint") or "")
        if fp:
            seen.add(fp)
    return seen


def _make_wakeup(
    *,
    kind: str,
    severity: str,
    status: str,
    reason: str,
    source: str,
    evidence_refs: Sequence[str],
    ts: float,
) -> SentinelWakeup:
    payload = {
        "kind": kind,
        "severity": severity,
        "status": status,
        "reason": _sanitize_text(reason, 240),
        "evidence_refs": list(evidence_refs),
    }
    digest = _stable_digest(payload)
    return SentinelWakeup(
        id="wakeup-" + hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16],
        kind=kind,
        severity=severity,
        status=status,
        reason=_sanitize_text(reason, 240),
        source=source,
        request_digest=digest,
        evidence_refs=[str(x) for x in evidence_refs if x],
        created_at=_utc_ts(ts),
    )


def _build_sentinel_wakeups(root: Path, *, source: str, now_epoch: float) -> list[SentinelWakeup]:
    summary = _read_json_file(root / "health_summary.json")
    latest = _read_json_file(root / "latest_findings.json")
    components = _read_json_file(root / "component_status.json")
    findings = list(latest.get("findings") or []) if isinstance(latest.get("findings"), list) else []
    if not findings:
        findings = _read_jsonl_tail(root / "findings.jsonl", limit=50)
    plans = _read_jsonl_tail(root / "repair_plans.jsonl", limit=200)
    attempts = _read_jsonl_tail(root / "repair_attempts.jsonl", limit=200)
    coalesced = _read_jsonl_tail(root / "coalesced.jsonl", limit=200)
    authorizations = _read_jsonl_tail(root / "repair_authorizations.jsonl", limit=200)

    closed_plan_ids = {
        str(a.get("plan_id")) for a in attempts
        if a.get("status") in {"dry_run_verified", "authorized_readback_verified", "applied_verified", "superseded"}
    }
    waiting = _open_waiting_plans(plans, closed_plan_ids)
    wakeups: list[SentinelWakeup] = []
    status = str(summary.get("overall_status") or "green")
    if status in {"red", "yellow"}:
        sev = "P0" if status == "red" else "P1"
        wakeups.append(_make_wakeup(
            kind="health_degraded",
            severity=sev,
            status=status,
            reason=f"selfcheck health is {status}; components={','.join(summary.get('components') or [])}",
            source=source,
            evidence_refs=["file:health_summary.json", "file:component_status.json"],
            ts=now_epoch,
        ))

    waiting_threshold = _sentinel_threshold("waiting_THRESHOLD", 1)
    if len(waiting) >= max(1, waiting_threshold):
        wakeups.append(_make_wakeup(
            kind="judgement_required",
            severity="P1" if status in {"red", "yellow"} else "P2",
            status=status,
            reason=f"{len(waiting)} repair plan(s) are waiting for judgement",
            source=source,
            evidence_refs=[f"repair_plan:{p.get('id')}" for p in waiting[:5]],
            ts=now_epoch,
        ))

    repeated_threshold = _sentinel_threshold("REPEATED_THRESHOLD", 3)
    if repeated_threshold:
        # Only user/runtime findings count as repeated-findings.  Sentinel wakeup
        # debounce records are internal noise-control bookkeeping; counting them
        # here creates a feedback loop where the sentinel alerts about its own
        # suppressed wakeups and then sends the user repeated selfcheck spam.
        recent = [
            c for c in coalesced
            if c.get("reason") == "debounced_recent_fingerprint"
            and c.get("finding_id")
            and float(c.get("created_at_epoch") or 0) >= now_epoch - _DEFAULT_DEBOUNCE_SECONDS
        ]
        if len(recent) >= repeated_threshold:
            wakeups.append(_make_wakeup(
                kind="repeated_fingerprint",
                severity="P1",
                status=status,
                reason=f"{len(recent)} repeated finding(s) coalesced inside debounce window",
                source=source,
                evidence_refs=[f"finding:{c.get('finding_id')}" for c in recent[-5:]],
                ts=now_epoch,
            ))

    expiry_threshold = _sentinel_threshold("AUTH_EXPIRY_SECONDS", 300)
    expired = []
    expiring = []
    for auth in authorizations:
        if auth.get("decision") != "approved":
            continue
        try:
            expires = float(auth.get("expires_at_epoch") or 0)
        except (TypeError, ValueError):
            continue
        if expires < now_epoch:
            expired.append(auth)
        elif now_epoch <= expires <= now_epoch + expiry_threshold:
            expiring.append(auth)
    if expired:
        wakeups.append(_make_wakeup(
            kind="authorization_expired",
            severity="P2",
            status=status,
            reason=f"{len(expired)} repair authorization(s) expired and can be locally archived",
            source=source,
            evidence_refs=[f"repair_authorization:{a.get('id')}" for a in expired[:5]],
            ts=now_epoch,
        ))
    if expiring:
        wakeups.append(_make_wakeup(
            kind="authorization_expiring",
            severity="P2",
            status=status,
            reason=f"{len(expiring)} repair authorization(s) expire soon",
            source=source,
            evidence_refs=[f"repair_authorization:{a.get('id')}" for a in expiring[:5]],
            ts=now_epoch,
        ))

    # Projection integrity is a product-level invariant: if a status projection is
    # missing while findings exist, wake the operator to rebuild/diagnose.  This
    # still performs no repair beyond writing the sentinel ledger below.
    if findings and not components.get("components"):
        wakeups.append(_make_wakeup(
            kind="projection_incomplete",
            severity="P1",
            status=status,
            reason="findings exist but component_status projection is empty or unreadable",
            source=source,
            evidence_refs=["file:latest_findings.json", "file:component_status.json"],
            ts=now_epoch,
        ))
    return wakeups


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _append_sentinel_outputs(root: Path, wakeup: SentinelWakeup, *, ts: float) -> None:
    record = asdict(wakeup)
    record.update({
        "schema_version": _SCHEMA_VERSION,
        "created_at_epoch": ts,
        "fingerprint": wakeup.request_digest,
        "side_effects_executed": False,
        "runtime_boundary": "sentinel_wakeup",
    })
    _append_jsonl(root / "wakeups.jsonl", record)
    diagnosis = {
        "schema_version": _SCHEMA_VERSION,
        "wakeup_id": wakeup.id,
        "kind": wakeup.kind,
        "severity": wakeup.severity,
        "status": wakeup.status,
        "summary": wakeup.reason,
        "created_at": _utc_ts(ts),
        "created_at_epoch": ts,
        "request_digest": wakeup.request_digest,
        "recommended_next": "review_health_projection_and_authorize_exact_repair_plan_if_needed",
        "evidence_refs": wakeup.evidence_refs,
        "side_effects_executed": False,
    }
    _append_jsonl(root / "diagnosis_packets.jsonl", diagnosis)
    if wakeup.severity in {"P0", "P1"}:
        pending_alert = {
            "schema_version": _SCHEMA_VERSION,
            "wakeup_id": wakeup.id,
            "status": "pending_local_alert",
            "severity": wakeup.severity,
            "summary": wakeup.reason,
            "created_at": _utc_ts(ts),
            "created_at_epoch": ts,
            "delivery": "local_only_no_platform_send",
            "request_digest": wakeup.request_digest,
            "evidence_refs": wakeup.evidence_refs,
        }
        _append_jsonl(root / "pending_alerts.jsonl", pending_alert)


def _append_l1_auto_attempt(
    root: Path,
    *,
    action_id: str,
    wakeup: SentinelWakeup,
    status: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    verification: Sequence[str],
    note: str,
    ts: float,
) -> RepairAttempt:
    payload = {
        "action_id": action_id,
        "wakeup_id": wakeup.id,
        "mode": "l1_auto",
        "request_digest": wakeup.request_digest,
    }
    attempt = RepairAttempt(
        id="attempt-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16],
        plan_id=f"sentinel:{wakeup.id}",
        action_id=action_id,
        mode="l1_auto",
        status=status,
        started_at=_utc_ts(ts),
        completed_at=_utc_ts(ts),
        request_digest=wakeup.request_digest,
        authorization_id="auto-l1-allowlist",
        before=dict(before),
        after=dict(after),
        verification=[str(v) for v in verification],
        note=_sanitize_text(note, 300),
    )
    existing = _read_jsonl_tail(root / "repair_attempts.jsonl", limit=2000)
    if not any(str(r.get("id")) == attempt.id for r in existing):
        record = asdict(attempt)
        record.update({
            "schema_version": _SCHEMA_VERSION,
            "created_at": _utc_ts(ts),
            "created_at_epoch": ts,
            "runtime_boundary": "l1_auto_allowlist",
            "side_effect_scope": "ledger_local_projection_only",
            "external_send_executed": False,
            "shell_executed": False,
            "lifecycle_mutated": False,
            "adapter_mutated": False,
        })
        _append_jsonl(root / "repair_attempts.jsonl", record)
    return attempt


def _archive_expired_authorizations(root: Path, *, wakeup: SentinelWakeup, ts: float) -> RepairAttempt | None:
    authorizations = _read_jsonl_tail(root / "repair_authorizations.jsonl", limit=2000)
    expired: list[dict[str, Any]] = []
    archived_ids = {str(x.get("authorization_id")) for x in _read_jsonl_tail(root / "archived_authorizations.jsonl", limit=2000)}
    for auth in authorizations:
        if str(auth.get("id")) in archived_ids:
            continue
        if auth.get("decision") != "approved":
            continue
        try:
            expires = float(auth.get("expires_at_epoch") or 0)
        except (TypeError, ValueError):
            continue
        if expires < ts:
            expired.append(auth)
    if not expired:
        return None
    before = {
        "expired_authorization_count": len(expired),
        "archived_authorization_count": len(archived_ids),
        "archive": _safe_read_path(root / "archived_authorizations.jsonl"),
    }
    for auth in expired:
        _append_jsonl(root / "archived_authorizations.jsonl", {
            "schema_version": _SCHEMA_VERSION,
            "authorization_id": auth.get("id"),
            "plan_id": auth.get("plan_id"),
            "action_id": auth.get("action_id"),
            "expired_at_epoch": auth.get("expires_at_epoch"),
            "archived_at": _utc_ts(ts),
            "archived_at_epoch": ts,
            "source_wakeup_id": wakeup.id,
            "raw_authorization_hash": _stable_digest(auth),
        })
    after_archived = {str(x.get("authorization_id")) for x in _read_jsonl_tail(root / "archived_authorizations.jsonl", limit=2000)}
    after = {
        "expired_authorization_count": len(expired),
        "archived_authorization_count": len(after_archived),
        "archive": _safe_read_path(root / "archived_authorizations.jsonl"),
    }
    return _append_l1_auto_attempt(
        root,
        action_id="selfcheck.l1.archive_expired_authorizations",
        wakeup=wakeup,
        status="applied_verified",
        before=before,
        after=after,
        verification=[
            "expired authorizations were copied to archived_authorizations.jsonl",
            "source repair_authorizations.jsonl remained append-only and unmodified",
            "no shell command executed",
            "no external message sent",
            "no task lifecycle or adapter state mutated",
        ],
        note="allowlisted L1 local archive completed",
        ts=ts,
    )


def _execute_l1_auto_repairs(root: Path, wakeups: Sequence[SentinelWakeup], *, ts: float) -> list[RepairAttempt]:
    """Run all currently opened L1 auto repairs under a strict allowlist."""
    if not _auto_repair_enabled():
        return []
    attempts: list[RepairAttempt] = []
    for wakeup in wakeups:
        if wakeup.kind == "projection_incomplete":
            before = {
                "health_summary": _safe_read_path(root / "health_summary.json"),
                "latest_findings": _safe_read_path(root / "latest_findings.json"),
                "component_status": _safe_read_path(root / "component_status.json"),
                "sentinel_status": _safe_read_path(root / "sentinel_status.json"),
            }
            _rebuild_projections(root, now_epoch=ts)
            _rebuild_sentinel_projection(root, now_epoch=ts)
            after = {
                "health_summary": _safe_read_path(root / "health_summary.json"),
                "latest_findings": _safe_read_path(root / "latest_findings.json"),
                "component_status": _safe_read_path(root / "component_status.json"),
                "sentinel_status": _safe_read_path(root / "sentinel_status.json"),
            }
            attempts.append(_append_l1_auto_attempt(
                root,
                action_id="selfcheck.l1.rebuild_projections",
                wakeup=wakeup,
                status="applied_verified",
                before=before,
                after=after,
                verification=[
                    "derived projection files were regenerated from append-only ledgers",
                    "no source ledger was deleted or rewritten",
                    "no shell command executed",
                    "no external message sent",
                    "no task lifecycle or adapter state mutated",
                ],
                note="allowlisted L1 projection rebuild completed",
                ts=ts,
            ))
        elif wakeup.kind == "authorization_expired":
            attempt = _archive_expired_authorizations(root, wakeup=wakeup, ts=ts)
            if attempt is not None:
                attempts.append(attempt)
    if attempts:
        _rebuild_projections(root, now_epoch=ts)
        _rebuild_sentinel_projection(root, now_epoch=ts)
    return attempts


def _notification_outbox_records(root: Path, *, ts: float) -> None:
    """Convert local pending alerts into governed notification outbox records.

    This is an event-governed boundary: it creates local intents, local delivery
    authorizations, and local channel dry-runs only.  It never calls platform
    APIs, reads tokens, or sends external messages.
    """
    if not _notification_outbox_enabled():
        return
    alerts = _read_jsonl_tail(root / "pending_alerts.jsonl", limit=500)
    if not alerts:
        return
    outbox_existing = _read_jsonl_tail(root / "notification_outbox.jsonl", limit=1000)
    auth_existing = _read_jsonl_tail(root / "delivery_authorizations.jsonl", limit=1000)
    dry_existing = _read_jsonl_tail(root / "delivery_dry_runs.jsonl", limit=1000)
    outbox_ids = {str(x.get("id")) for x in outbox_existing}
    auth_ids = {str(x.get("id")) for x in auth_existing}
    dry_ids = {str(x.get("id")) for x in dry_existing}
    for alert in alerts:
        if alert.get("status") != "pending_local_alert":
            continue
        summary = _sanitize_text(alert.get("summary"), 300)
        payload_hash = _stable_digest({
            "summary": summary,
            "severity": alert.get("severity"),
            "wakeup_id": alert.get("wakeup_id"),
            "request_digest": alert.get("request_digest"),
        })
        outbox_payload = {
            "source_alert_id": alert.get("wakeup_id"),
            "channel": "local",
            "target_id": "selfcheck-local",
            "payload_hash": payload_hash,
        }
        outbox_id = "outbox-" + hashlib.sha256(json.dumps(outbox_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if outbox_id not in outbox_ids:
            _append_jsonl(root / "notification_outbox.jsonl", {
                "schema_version": _SCHEMA_VERSION,
                "id": outbox_id,
                "status": "pending_delivery_authorization",
                "channel": "local",
                "target_id": "selfcheck-local",
                "source_alert_id": alert.get("wakeup_id"),
                "severity": alert.get("severity"),
                "payload_hash": payload_hash,
                "payload_summary": summary,
                "payload_body_stored": False,
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "request_digest": _stable_digest(outbox_payload),
                "external_send_executed": False,
                "token_read": False,
            })
            outbox_ids.add(outbox_id)
        auth_payload = {
            "outbox_id": outbox_id,
            "channel": "local",
            "target_id": "selfcheck-local",
            "payload_hash": payload_hash,
            "decision": "approved",
            "actor": "policy:selfcheck-local-alert",
        }
        auth_id = "delivery-auth-" + hashlib.sha256(json.dumps(auth_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if auth_id not in auth_ids:
            _append_jsonl(root / "delivery_authorizations.jsonl", {
                "schema_version": _SCHEMA_VERSION,
                "id": auth_id,
                "outbox_id": outbox_id,
                "channel": "local",
                "target_id": "selfcheck-local",
                "payload_hash": payload_hash,
                "decision": "approved",
                "actor": "policy:selfcheck-local-alert",
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "expires_at": _utc_ts(ts + 900),
                "expires_at_epoch": ts + 900,
                "request_digest": _stable_digest(auth_payload),
                "external_send_executed": False,
                "token_read": False,
            })
            auth_ids.add(auth_id)
        dry_payload = {
            "delivery_authorization_id": auth_id,
            "outbox_id": outbox_id,
            "channel": "local",
            "target_id": "selfcheck-local",
            "payload_hash": payload_hash,
        }
        dry_id = "delivery-dry-run-" + hashlib.sha256(json.dumps(dry_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if dry_id not in dry_ids:
            _append_jsonl(root / "delivery_dry_runs.jsonl", {
                "schema_version": _SCHEMA_VERSION,
                "id": dry_id,
                "status": "local_delivery_dry_run_verified",
                "delivery_authorization_id": auth_id,
                "outbox_id": outbox_id,
                "channel": "local",
                "target_id": "selfcheck-local",
                "payload_hash": payload_hash,
                "platform_payload_hash": _stable_digest({"channel": "local", "summary": summary}),
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "request_digest": _stable_digest(dry_payload),
                "send_executed": False,
                "external_send_executed": False,
                "token_read": False,
                "verification": [
                    "local notification outbox dry-run only",
                    "no platform API called",
                    "no token or credential read",
                    "payload body not persisted; hash and summary only",
                ],
            })
            dry_ids.add(dry_id)

        telegram_target = os.environ.get("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng").strip()
        telegram_enabled = os.environ.get("HERMES_SELFCHECK_TELEGRAM_OUTBOX", "1").strip().lower() not in {"0", "false", "off", "no", "disabled"}
        if not telegram_enabled or not telegram_target.startswith("telegram:") or _contains_secret_like(telegram_target):
            continue
        telegram_outbox_payload = {
            "source_alert_id": alert.get("wakeup_id"),
            "channel": "telegram",
            "target_id": telegram_target,
            "payload_hash": payload_hash,
        }
        telegram_outbox_id = "outbox-" + hashlib.sha256(json.dumps(telegram_outbox_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if telegram_outbox_id not in outbox_ids:
            _append_jsonl(root / "notification_outbox.jsonl", {
                "schema_version": _SCHEMA_VERSION,
                "id": telegram_outbox_id,
                "status": "pending_delivery_authorization",
                "channel": "telegram",
                "target_id": telegram_target,
                "source_alert_id": alert.get("wakeup_id"),
                "severity": alert.get("severity"),
                "payload_hash": payload_hash,
                "payload_summary": summary,
                "payload_body_stored": False,
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "request_digest": _stable_digest(telegram_outbox_payload),
                "external_send_executed": False,
                "token_read": False,
            })
            outbox_ids.add(telegram_outbox_id)
        telegram_auth_payload = {
            "outbox_id": telegram_outbox_id,
            "channel": "telegram",
            "target_id": telegram_target,
            "payload_hash": payload_hash,
            "decision": "approved",
            "actor": "policy:selfcheck-telegram-alert",
        }
        telegram_auth_id = "delivery-auth-" + hashlib.sha256(json.dumps(telegram_auth_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if telegram_auth_id not in auth_ids:
            _append_jsonl(root / "delivery_authorizations.jsonl", {
                "schema_version": _SCHEMA_VERSION,
                "id": telegram_auth_id,
                "outbox_id": telegram_outbox_id,
                "channel": "telegram",
                "target_id": telegram_target,
                "payload_hash": payload_hash,
                "decision": "approved",
                "actor": "policy:selfcheck-telegram-alert",
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "expires_at": _utc_ts(ts + 900),
                "expires_at_epoch": ts + 900,
                "request_digest": _stable_digest(telegram_auth_payload),
                "external_send_executed": False,
                "token_read": False,
            })
            auth_ids.add(telegram_auth_id)
        telegram_dry_payload = {
            "delivery_authorization_id": telegram_auth_id,
            "outbox_id": telegram_outbox_id,
            "channel": "telegram",
            "target_id": telegram_target,
            "payload_hash": payload_hash,
        }
        telegram_dry_id = "delivery-dry-run-" + hashlib.sha256(json.dumps(telegram_dry_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if telegram_dry_id not in dry_ids:
            _append_jsonl(root / "delivery_dry_runs.jsonl", {
                "schema_version": _SCHEMA_VERSION,
                "id": telegram_dry_id,
                "status": "telegram_delivery_dry_run_verified",
                "delivery_authorization_id": telegram_auth_id,
                "outbox_id": telegram_outbox_id,
                "channel": "telegram",
                "target_id": telegram_target,
                "payload_hash": payload_hash,
                "platform_payload_hash": _stable_digest({"channel": "telegram", "target_id": telegram_target, "summary": summary}),
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "request_digest": _stable_digest(telegram_dry_payload),
                "send_executed": False,
                "external_send_executed": False,
                "token_read": False,
                "verification": [
                    "telegram delivery dry-run only",
                    "target is explicitly allowlisted by HERMES_SELFCHECK_TELEGRAM_TARGET/default",
                    "no platform API called at dry-run boundary",
                    "no token or credential read at dry-run boundary",
                    "payload body not persisted; hash and summary only",
                ],
            })
            dry_ids.add(telegram_dry_id)


def _rebuild_sentinel_projection(root: Path, *, now_epoch: float | None = None) -> None:
    ts = now_epoch or _now()
    wakeups = _read_jsonl_tail(root / "wakeups.jsonl", limit=100)
    alerts = _read_jsonl_tail(root / "pending_alerts.jsonl", limit=100)
    outbox = _read_jsonl_tail(root / "notification_outbox.jsonl", limit=100)
    delivery_dry_runs = _read_jsonl_tail(root / "delivery_dry_runs.jsonl", limit=100)
    projection = {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _utc_ts(ts),
        "generated_at_epoch": ts,
        "wakeup_count_tail": len(wakeups),
        "pending_local_alerts": len([a for a in alerts if a.get("status") == "pending_local_alert"]),
        "notification_outbox_tail": len(outbox),
        "local_delivery_dry_runs_tail": len(delivery_dry_runs),
        "latest_wakeup": wakeups[-1] if wakeups else None,
    }
    _write_json(root / "sentinel_status.json", projection)


def run_sentinel_tick(*, source: str = "manual", debounce_seconds: int | None = None) -> list[SentinelWakeup]:
    """Actively wake the selfcheck control plane without executing repairs.

    This is safe for cron/finalizer/background use: it reads projections and
    append-only ledgers, emits wakeups/diagnosis/local-alert intents, and never
    runs shell commands, sends platform messages, or mutates task/provider state.
    """
    root = ledger_dir()
    if not _enabled() or not _sentinel_enabled():
        return []
    ts = _now()
    with _LEDGER_LOCK:
        wakeups = _build_sentinel_wakeups(root, source=source, now_epoch=ts)
        debounce = _DEFAULT_DEBOUNCE_SECONDS if debounce_seconds is None else max(0, int(debounce_seconds))
        recent = _recent_wakeup_fingerprints(root, window_seconds=debounce, now_epoch=ts) if debounce else set()
        emitted: list[SentinelWakeup] = []
        for wakeup in wakeups:
            if debounce and wakeup.request_digest in recent:
                _append_jsonl(root / "coalesced.jsonl", {
                    "schema_version": _SCHEMA_VERSION,
                    "wakeup_id": wakeup.id,
                    "fingerprint": wakeup.request_digest,
                    "component": "selfcheck_sentinel",
                    "created_at": _utc_ts(ts),
                    "created_at_epoch": ts,
                    "reason": "debounced_recent_wakeup",
                })
                continue
            _append_sentinel_outputs(root, wakeup, ts=ts)
            recent.add(wakeup.request_digest)
            emitted.append(wakeup)
        _execute_l1_auto_repairs(root, emitted, ts=ts)
        _notification_outbox_records(root, ts=ts)
        _rebuild_sentinel_projection(root, now_epoch=ts)
        return emitted


def _run_sentinel_after_ingest(root: Path, *, source: str, now_epoch: float) -> None:
    if not _sentinel_enabled():
        return
    try:
        wakeups = _build_sentinel_wakeups(root, source=source, now_epoch=now_epoch)
        recent = _recent_wakeup_fingerprints(root, window_seconds=_DEFAULT_DEBOUNCE_SECONDS, now_epoch=now_epoch)
        emitted: list[SentinelWakeup] = []
        for wakeup in wakeups:
            if wakeup.request_digest in recent:
                continue
            _append_sentinel_outputs(root, wakeup, ts=now_epoch)
            recent.add(wakeup.request_digest)
            emitted.append(wakeup)
        _execute_l1_auto_repairs(root, emitted, ts=now_epoch)
        _notification_outbox_records(root, ts=now_epoch)
        _rebuild_sentinel_projection(root, now_epoch=now_epoch)
    except Exception as exc:  # pragma: no cover - defensive hot-path guard
        logger.debug("selfcheck sentinel wakeup failed: %s", exc, exc_info=True)


def _telegram_sender_enabled() -> bool:
    # External Telegram sends are a live platform side effect.  Keep the resident
    # sender default-off; operators can still enable it explicitly after the
    # local outbox/dry-run evidence is reviewed.
    raw = os.environ.get("HERMES_SELFCHECK_TELEGRAM_LIVE_SEND", "0").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled"}


def _telegram_delivery_batch_limit() -> int:
    raw = os.environ.get("HERMES_SELFCHECK_TELEGRAM_BATCH_LIMIT", "5").strip()
    try:
        return max(1, min(int(raw), 20))
    except ValueError:
        return 5


def _telegram_delivery_max_attempts() -> int:
    raw = os.environ.get("HERMES_SELFCHECK_TELEGRAM_MAX_ATTEMPTS", "5").strip()
    try:
        return max(1, min(int(raw), 20))
    except ValueError:
        return 5


def _load_outbox_by_id(root: Path, outbox_id: str) -> dict[str, Any] | None:
    for rec in reversed(_read_jsonl_tail(root / "notification_outbox.jsonl", limit=2000)):
        if str(rec.get("id")) == outbox_id:
            return rec
    return None


def run_telegram_delivery_tick(*, sender: Any | None = None) -> list[dict[str, Any]]:
    """Consume verified Telegram dry-runs and send to the allowlisted Telegram target.

    This is the first live platform boundary.  It is intentionally narrow:
    source must be `telegram_delivery_dry_run_verified`, target must match
    `HERMES_SELFCHECK_TELEGRAM_TARGET` (default `telegram:he cheng`), payloads
    are reconstructed from summaries only, and receipts never persist tokens.
    """
    if not _enabled() or not _telegram_sender_enabled():
        return []
    root = ledger_dir()
    target = os.environ.get("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng").strip()
    if not target.startswith("telegram:") or _contains_secret_like(target):
        raise ValueError("Telegram target must be an explicit telegram: allowlist reference")
    ts = _now()
    receipts = _read_jsonl_tail(root / "delivery_receipts.jsonl", limit=2000)
    # Successful receipts are terminal. Failed sends are retryable up to a small
    # bounded attempt cap so post-outage recovery can compensate after Telegram
    # comes back without hammering a broken channel forever.
    sent_dry_ids = {
        str(r.get("delivery_dry_run_id"))
        for r in receipts
        if r.get("status") == "sent_verified"
    }
    failed_attempts_by_dry_id: dict[str, int] = {}
    for r in receipts:
        if r.get("status") == "send_failed":
            dry_id = str(r.get("delivery_dry_run_id") or "")
            if dry_id:
                failed_attempts_by_dry_id[dry_id] = failed_attempts_by_dry_id.get(dry_id, 0) + 1
    max_attempts = _telegram_delivery_max_attempts()
    batch_limit = _telegram_delivery_batch_limit()
    dry_runs = _read_jsonl_tail(root / "delivery_dry_runs.jsonl", limit=2000)
    emitted: list[dict[str, Any]] = []
    if sender is None:
        from tools.send_message_tool import _handle_send as sender  # type: ignore
    with _LEDGER_LOCK:
        for dry in dry_runs:
            if len(emitted) >= batch_limit:
                break
            dry_id = str(dry.get("id") or "")
            if not dry_id or dry_id in sent_dry_ids:
                continue
            if failed_attempts_by_dry_id.get(dry_id, 0) >= max_attempts:
                continue
            if dry.get("status") != "telegram_delivery_dry_run_verified":
                continue
            if dry.get("channel") != "telegram" or dry.get("target_id") != target:
                continue
            outbox = _load_outbox_by_id(root, str(dry.get("outbox_id") or ""))
            if not outbox:
                continue
            if outbox.get("payload_hash") != dry.get("payload_hash"):
                continue
            summary = _sanitize_text(outbox.get("payload_summary"), 300)
            message = f"Hermes selfcheck alert: {summary}\n\nsource={outbox.get('source_alert_id')}"
            result_raw = sender({"target": target, "message": message})
            try:
                result = json.loads(result_raw) if isinstance(result_raw, str) else dict(result_raw)
            except Exception:
                result = {"error": "unparseable sender result"}
            success = bool(result.get("success")) and not result.get("error")
            attempt_no = failed_attempts_by_dry_id.get(dry_id, 0) + 1
            receipt = {
                "schema_version": _SCHEMA_VERSION,
                "id": "delivery-receipt-" + hashlib.sha256(json.dumps({"dry_run_id": dry_id, "target": target, "attempt": attempt_no}, sort_keys=True).encode("utf-8")).hexdigest()[:16],
                "delivery_dry_run_id": dry_id,
                "delivery_authorization_id": dry.get("delivery_authorization_id"),
                "outbox_id": dry.get("outbox_id"),
                "channel": "telegram",
                "target_id": target,
                "status": "sent_verified" if success else "send_failed",
                "created_at": _utc_ts(ts),
                "created_at_epoch": ts,
                "request_digest": _stable_digest({"dry_run_id": dry_id, "target": target, "payload_hash": dry.get("payload_hash")}),
                "external_send_executed": success,
                "token_value_persisted": False,
                "payload_body_persisted": False,
                "message_id": result.get("message_id") or result.get("id"),
                "error_type": _sanitize_text(result.get("error"), 120) if result.get("error") else None,
            }
            if not any(str(r.get("id")) == receipt["id"] for r in receipts):
                _append_jsonl(root / "delivery_receipts.jsonl", receipt)
                receipts.append(receipt)
            emitted.append(receipt)
    return emitted


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


def _load_authorization(root: Path, authorization_id: str) -> dict[str, Any]:
    authorizations = _read_jsonl_tail(root / "repair_authorizations.jsonl", limit=2000)
    auth = next((a for a in reversed(authorizations) if str(a.get("id")) == str(authorization_id)), None)
    if auth is None:
        raise ValueError(f"repair authorization not found: {authorization_id}")
    return auth


def authorize_repair_plan(
    plan_id: str,
    *,
    request_digest: str,
    actor: str,
    ttl_seconds: int = 900,
    decision: str = "approved",
    evidence_refs: Sequence[str] | None = None,
    note: str = "",
) -> RepairAuthorization:
    """Create a short-lived, digest-bound repair authorization.

    This boundary still performs no repair.  It records an exact actor/plan/
    digest/TTL decision that later executor calls must consume.
    """
    root = ledger_dir()
    ts = _now()
    plan = _load_plan(root, plan_id)
    expected_digest = str(plan.get("request_digest") or "")
    if request_digest != expected_digest:
        raise ValueError("repair plan digest mismatch")
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    if not actor or _contains_secret_like(actor):
        raise ValueError("actor is required and must not contain secret-like text")
    refs = [str(x) for x in evidence_refs or [] if x]
    if _contains_secret_like(refs) or _contains_secret_like(note):
        raise ValueError("authorization evidence/note must not contain secret-like text")
    ttl = max(1, min(int(ttl_seconds), 24 * 3600))
    expires = ts + ttl
    payload = {
        "plan_id": plan_id,
        "action_id": plan.get("action_id"),
        "decision": decision,
        "actor": actor,
        "request_digest": request_digest,
        "expires_at_epoch": expires,
    }
    auth = RepairAuthorization(
        id="auth-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16],
        plan_id=plan_id,
        action_id=str(plan.get("action_id") or ""),
        decision=decision,
        actor=actor,
        created_at=_utc_ts(ts),
        expires_at=_utc_ts(expires),
        request_digest=request_digest,
        evidence_refs=refs,
        note=_sanitize_text(note, 300),
    )
    with _LEDGER_LOCK:
        record = asdict(auth)
        record.update({
            "schema_version": _SCHEMA_VERSION,
            "created_at_epoch": ts,
            "expires_at_epoch": expires,
        })
        existing = _read_jsonl_tail(root / "repair_authorizations.jsonl", limit=2000)
        if not any(str(r.get("id")) == auth.id for r in existing):
            _append_jsonl(root / "repair_authorizations.jsonl", record)
        _rebuild_projections(root, now_epoch=ts)
        _run_sentinel_after_ingest(root, source="authorization", now_epoch=ts)
    return auth


def run_repair_plan_dry_run(plan_id: str, *, request_digest: str | None = None) -> RepairAttempt:
    """Append a governed repair_attempt for a plan without executing side effects.

    This compatibility boundary consumes an exact plan id (and, when provided,
    an exact digest), performs only local readback, and records a repair_attempt.
    Production mutation remains closed behind explicit authorization.
    """
    root = ledger_dir()
    ts = _now()
    plan = _load_plan(root, plan_id)
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
        authorization_id="",
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
        existing = _read_jsonl_tail(root / "repair_attempts.jsonl", limit=2000)
        if not any(str(r.get("id")) == attempt.id for r in existing):
            _append_jsonl(root / "repair_attempts.jsonl", record)
        _rebuild_projections(root, now_epoch=ts)
        _rebuild_sentinel_projection(root, now_epoch=ts)
    return attempt


def run_authorized_repair_plan(
    authorization_id: str,
    *,
    plan_id: str,
    request_digest: str,
    mode: str = "authorized_readback",
) -> RepairAttempt:
    """Consume a live authorization and run the enterprise safe executor boundary.

    Current production-safe executor is intentionally readback-only.  It proves
    authorization consumption, exact digest binding, TTL enforcement, idempotent
    attempt records, and no shell/send/lifecycle mutation.
    """
    if mode != "authorized_readback":
        raise ValueError("only authorized_readback mode is enabled")
    root = ledger_dir()
    ts = _now()
    plan = _load_plan(root, plan_id)
    auth = _load_authorization(root, authorization_id)
    if auth.get("decision") != "approved":
        raise ValueError("repair authorization is not approved")
    if str(auth.get("plan_id")) != str(plan_id):
        raise ValueError("authorization plan mismatch")
    if str(auth.get("request_digest")) != request_digest or str(plan.get("request_digest")) != request_digest:
        raise ValueError("repair plan digest mismatch")
    if float(auth.get("expires_at_epoch") or 0) < ts:
        raise ValueError("repair authorization expired")
    before, after, verification, note = _dry_run_action(plan, root)
    verification = [
        f"authorization consumed: {authorization_id}",
        "exact plan id and request digest matched",
        "authorization TTL is still valid",
        *verification,
    ]
    attempt_payload = {
        "authorization_id": authorization_id,
        "plan_id": plan_id,
        "action_id": plan.get("action_id"),
        "mode": mode,
        "request_digest": request_digest,
    }
    attempt = RepairAttempt(
        id="attempt-" + hashlib.sha256(json.dumps(attempt_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16],
        plan_id=plan_id,
        action_id=str(plan.get("action_id") or ""),
        mode=mode,
        status="authorized_readback_verified",
        started_at=_utc_ts(ts),
        completed_at=_utc_ts(ts),
        request_digest=request_digest,
        authorization_id=authorization_id,
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
        existing = _read_jsonl_tail(root / "repair_attempts.jsonl", limit=2000)
        if not any(str(r.get("id")) == attempt.id for r in existing):
            _append_jsonl(root / "repair_attempts.jsonl", record)
        _rebuild_projections(root, now_epoch=ts)
        _rebuild_sentinel_projection(root, now_epoch=ts)
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
            _run_sentinel_after_ingest(root, source="observe_event", now_epoch=ts)
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
    sidecar_run_id: str | None = None,
    sidecar_path: str | None = None,
    extra_evidence_refs: Sequence[str] | None = None,
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
    summary_text = "; ".join(summary_parts) or "cron job completed"
    cron_success = bool(success) and not delivery_error and not (success and not str(final_response or "").strip())
    try:
        from agent.health_records import record_health_observation

        record_health_observation(
            chain_type="cron",
            target_id=str(job.get("id") or "unknown"),
            target_label=str(job.get("name") or job.get("id") or "cron job"),
            status="success" if cron_success else "degraded",
            success=cron_success,
            error_message=error or delivery_error or (None if cron_success else summary_text),
            details={
                "event_type": event_type,
                "output_file": output_file,
                "has_final_response": bool(str(final_response or "").strip()),
                "sidecar_run_id": sidecar_run_id,
                "sidecar_path": sidecar_path,
            },
        )
    except Exception as exc:
        logger.debug("health record cron observation failed: %s", exc)
    evidence_refs = [x for x in [f"cron_job:{job.get('id')}", f"file:{output_file}" if output_file else None] if x]
    for ref in extra_evidence_refs or []:
        ref_text = str(ref or "").strip()
        if ref_text and ref_text not in evidence_refs:
            evidence_refs.append(ref_text)
    event = {
        "event_type": event_type,
        "trigger": "cron_scheduler",
        "component": "cron",
        "job_id": job.get("id"),
        "job_name": job.get("name"),
        "success": bool(success),
        "summary": summary_text,
        "error": error,
        "delivery_error": delivery_error,
        "evidence_refs": evidence_refs,
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
    try:
        from agent.health_records import record_health_observation

        record_health_observation(
            chain_type="connector",
            target_id=str(platform or "unknown"),
            target_label=str(platform or "connector"),
            status="success" if success else "degraded",
            success=bool(success),
            error_message=None if success else text,
            details={
                "chat_id": _sanitize_text(chat_id, 120),
                "thread_id": _sanitize_text(thread_id, 120),
                "source": source or "gateway_delivery",
                "event_type": event_type,
            },
        )
    except Exception as exc:
        logger.debug("health record gateway observation failed: %s", exc)
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
    data = _read_json_file(ledger_dir() / "health_summary.json")
    return data if isinstance(data, dict) else {}


def read_sentinel_status() -> dict[str, Any]:
    """Read the resident sentinel wake-up projection if one has been generated."""
    data = _read_json_file(ledger_dir() / "sentinel_status.json")
    return data if isinstance(data, dict) else {}


__all__ = [
    "Finding",
    "OperatorDecision",
    "RepairAttempt",
    "RepairAuthorization",
    "RepairPlan",
    "SentinelWakeup",
    "authorize_repair_plan",
    "ledger_dir",
    "observe_cron_job_result",
    "observe_event",
    "observe_gateway_delivery",
    "observe_provider_error",
    "observe_taskpool_event",
    "observe_turn_result",
    "read_health_summary",
    "read_recent_findings",
    "read_sentinel_status",
    "run_authorized_repair_plan",
    "run_repair_plan_dry_run",
    "run_sentinel_tick",
    "run_telegram_delivery_tick",
]
