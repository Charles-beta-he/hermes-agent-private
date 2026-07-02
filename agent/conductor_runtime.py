#!/usr/bin/env python3
"""Hermes conductor runtime primitives.

Default-off, fail-soft helpers for Sakana/Fugu-inspired orchestration:
- task ledger for durable async task state
- conversation intent router with rule fast-path and optional default-model decider
- optional Sakana Fugu adapter normalization

This module is deliberately side-effect-light. It does not call external models or
Fugu unless a caller explicitly injects a decider/client.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib import request as urlrequest

HOME = Path.home()
DEFAULT_LEDGER = HOME / ".hermes" / "task_ledger.db"
DEFAULT_MODEL_PROFILES = HOME / ".hermes" / "config" / "model_profiles.yaml"

VALID_STATUSES = {"queued", "running", "paused", "blocked", "done", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "paused", "blocked"}
VALID_INTENTS = {
    "current_task_control",
    "new_background_task",
    "status_query",
    "normal_chat",
    "priority_change",
    "multimodel_escalation",
}
VALID_ACTIONS = {
    "append_context", "create", "pause", "cancel", "resume", "fork", "answer", "escalate",
}


def _now() -> float:
    return time.time()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def default_conductor_config() -> Dict[str, Any]:
    """Return default-off conductor configuration."""
    return {
        "conductor": {
            "enabled": False,
            "intent_router": {
                "enabled": False,
                "mode": "default_main_model_bootstrap",
                "router_call_isolated": True,
                "max_context_chars": 4000,
                "output_schema": "hermes_intent_decision_v1",
            },
            "complex_task_harness": {
                "enabled": False,
                "mode": "iceberg_scan_advisory",
                "output_schema": "hermes_iceberg_complexity_scan_v1",
                "runtime_mutation_authorized": False,
                "ledger_enabled": False,
                "ledger_path": str(HOME / ".hermes" / "state" / "complex-task-harness" / "ledger.jsonl"),
            },
            "profile_file": str(DEFAULT_MODEL_PROFILES),
            "role_plan": {},
        },
        "orchestration_providers": {
            "sakana_fugu": {
                "enabled": False,
                "mode": "api",
                "api_key_env": "SAKANA_FUGU_API_KEY",
                "base_url": "",
                "timeout_seconds": 180,
                "roles": ["orchestrator", "arbiter", "planner"],
                "cost_gate": {"max_calls_per_task": 2, "require_user_escalation": True},
            }
        },
    }


def conductor_defaults_enabled(config: Dict[str, Any] | None) -> bool:
    """True only if any conductor/Fugu behavior is explicitly enabled."""
    cfg = config or {}
    conductor_raw = cfg.get("conductor")
    conductor: Dict[str, Any] = conductor_raw if isinstance(conductor_raw, dict) else {}
    providers_raw = cfg.get("orchestration_providers")
    providers: Dict[str, Any] = providers_raw if isinstance(providers_raw, dict) else {}
    fugu_raw = providers.get("sakana_fugu")
    fugu: Dict[str, Any] = fugu_raw if isinstance(fugu_raw, dict) else {}
    router_raw = conductor.get("intent_router")
    router: Dict[str, Any] = router_raw if isinstance(router_raw, dict) else {}
    harness_raw = conductor.get("complex_task_harness")
    harness: Dict[str, Any] = harness_raw if isinstance(harness_raw, dict) else {}
    return bool(
        conductor.get("enabled")
        or router.get("enabled")
        or harness.get("enabled")
        or harness.get("ledger_enabled")
        or fugu.get("enabled")
    )


class IcebergComplexityScanner:
    """Classify whether a request needs the complex-task harness boundary.

    This is the first LazyCodex/OmO absorption primitive for Hermes core/runtime:
    a deterministic, default-safe preflight that decides whether the next step is
    a lightweight answer/minimal action or an SSOT-backed runtime scaffold.  It
    does not execute work, mutate lifecycle state, or grant side-effect authority.
    """

    EXTERNAL_REFERENCE_TERMS = (
        "lazycodex", "omo", "oh-my-openagent", "opencode", "codex", "reference", "借鉴", "学习", "吸收",
    )
    HERMES_SELF_TERMS = (
        "hermes", "core", "runtime", "主循环", "自进化", "控制面", "taskpool", "workflow", "工作流",
    )
    DIRECT_ADVANCE_TERMS = (
        "直接推进", "全盘推进", "继续推进", "落地", "替换", "生产级", "直到", "直至",
    )
    AUTHORITY_TERMS = (
        "权限", "authority", "side effect", "副作用", "cron", "dispatch", "deploy", "publish", "删除", "外部",
    )

    def scan(self, user_message: str, *, target_surface: str = "") -> Dict[str, Any]:
        text = f"{user_message or ''}\n{target_surface or ''}".lower()
        above: List[str] = []
        submerged: List[str] = []

        if any(term.lower() in text for term in self.EXTERNAL_REFERENCE_TERMS):
            above.append("external_reference_absorption")
        if any(term.lower() in text for term in self.HERMES_SELF_TERMS):
            above.append("hermes_self_architecture")
        if any(term.lower() in text for term in self.DIRECT_ADVANCE_TERMS):
            above.append("direct_advancement")
        if "core" in text or "runtime" in text or "主循环" in text or "替换" in text:
            above.append("core_runtime_replacement")
        if any(term in text for term in ("memory", "skill", "docs", "taskpool", "gate", "hook", "runtime")):
            above.append("multiple_landing_zones")

        if any(term.lower() in text for term in self.AUTHORITY_TERMS) or "core_runtime_replacement" in above:
            submerged.append("authority_side_effect_ambiguity")
        if "external_reference_absorption" in above and "hermes_self_architecture" in above:
            submerged.append("cargo_cult_risk")
        if ("lazycodex" in text or "omo" in text) and ("hermes_self_architecture" in above or "direct_advancement" in above):
            submerged.append("source_vs_marketing_evidence_boundary")
        if ("完成" in text or "直至" in text or "until" in text) and ("direct_advancement" in above or "core_runtime_replacement" in above):
            submerged.append("completion_claim_needs_objective_evidence")

        # Deduplicate while preserving order.
        above = list(dict.fromkeys(above))
        submerged = list(dict.fromkeys(submerged))
        classification = "complex" if len(above) >= 2 or bool(submerged) else "simple"
        complex_path = classification == "complex"
        return {
            "schema": "hermes_iceberg_complexity_scan_v1",
            "classification": classification,
            "above_water_triggers": above,
            "submerged_triggers": submerged,
            "objective_gate_required": complex_path,
            "ssot_required": complex_path,
            "safe_next_boundary": "ssot_then_default_off_runtime_scaffold" if complex_path else "answer_or_minimal_action",
            "side_effect_ceiling": "local_write_only_until_exact_runtime_gate" if complex_path else "none_or_minimal",
            "runtime_mutation_authorized": False,
        }


class ComplexTaskHarnessLedger:
    """Append-only local receipt ledger for complex-task harness scans.

    The ledger is deliberately local and authority-negative: it can preserve the
    scan/evidence trail needed for continuation and review, but every receipt
    explicitly denies external side effects and lifecycle completion authority.
    """

    AUTHORITY_KEYS = {
        "mark_done",
        "task_status",
        "lifecycle_status",
        "external_write",
        "external_writes",
        "outbound_message",
        "memory_write",
        "skill_write",
        "provider_config_change",
    }

    def __init__(self, path: str | Path = HOME / ".hermes" / "state" / "complex-task-harness" / "ledger.jsonl"):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _digest(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _has_authority_smuggling(self, value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key) in self.AUTHORITY_KEYS:
                    return True
                if self._has_authority_smuggling(child):
                    return True
        elif isinstance(value, list):
            return any(self._has_authority_smuggling(item) for item in value)
        return False

    def _append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def record_scan(self, *, scan: Dict[str, Any], user_message: str, origin: str = "") -> Dict[str, Any]:
        payload = {"scan": scan, "user_message": user_message, "origin": origin}
        if self._has_authority_smuggling(payload):
            return {
                "schema": "hermes_complex_task_harness_receipt_v1",
                "status": "rejected",
                "reason": "authority_smuggling_detected",
                "external_side_effects_enabled": False,
                "allowed_to_mark_done": False,
            }
        scan_id = f"complex-scan-{self._digest(payload)[:16]}"
        event = {
            "schema": "hermes_complex_task_harness_receipt_v1",
            "event_type": "scan",
            "scan_id": scan_id,
            "status": "recorded",
            "created_at": _now(),
            "origin": origin,
            "user_message": user_message,
            "scan": scan,
            "external_side_effects_enabled": False,
            "allowed_to_mark_done": False,
        }
        return self._append(event)

    def append_evidence(self, scan_id: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"scan_id": scan_id, "evidence": evidence}
        if self._has_authority_smuggling(payload):
            return {
                "schema": "hermes_complex_task_harness_receipt_v1",
                "event_type": "evidence",
                "scan_id": scan_id,
                "status": "rejected",
                "reason": "authority_smuggling_detected",
                "external_side_effects_enabled": False,
                "allowed_to_mark_done": False,
            }
        event = {
            "schema": "hermes_complex_task_harness_receipt_v1",
            "event_type": "evidence",
            "scan_id": scan_id,
            "status": "recorded",
            "created_at": _now(),
            "evidence": evidence,
            "external_side_effects_enabled": False,
            "allowed_to_mark_done": False,
        }
        return self._append(event)

    def append_checkpoint(
        self,
        scan_id: str,
        *,
        objective: str,
        completed_evidence: Optional[List[str]] = None,
        pending_actions: Optional[List[str]] = None,
        rollback: str = "",
    ) -> Dict[str, Any]:
        checkpoint = {
            "objective": objective,
            "completed_evidence": completed_evidence or [],
            "pending_actions": pending_actions or [],
            "rollback": rollback,
        }
        payload = {"scan_id": scan_id, "checkpoint": checkpoint}
        if self._has_authority_smuggling(payload):
            return {
                "schema": "hermes_complex_task_harness_receipt_v1",
                "event_type": "checkpoint",
                "scan_id": scan_id,
                "status": "rejected",
                "reason": "authority_smuggling_detected",
                "external_side_effects_enabled": False,
                "allowed_to_mark_done": False,
            }
        event = {
            "schema": "hermes_complex_task_harness_receipt_v1",
            "event_type": "checkpoint",
            "scan_id": scan_id,
            "status": "recorded",
            "created_at": _now(),
            "checkpoint": checkpoint,
            "external_side_effects_enabled": False,
            "allowed_to_mark_done": False,
        }
        return self._append(event)

    def read_events(self, *, scan_id: str = "") -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        events: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if isinstance(event, dict) and (not scan_id or event.get("scan_id") == scan_id):
                events.append(event)
        return events


class TaskLedger:
    """SQLite-backed durable task ledger for async/background orchestration."""

    def __init__(self, path: str | Path = DEFAULT_LEDGER):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    origin_session_id TEXT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_goal TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    latest_user_directive TEXT NOT NULL DEFAULT '',
                    interrupt_policy TEXT NOT NULL DEFAULT 'accept_updates',
                    artifacts_json TEXT NOT NULL,
                    last_summary TEXT NOT NULL DEFAULT '',
                    active_worker TEXT NOT NULL DEFAULT '',
                    multimodel_mode TEXT NOT NULL DEFAULT 'off',
                    history_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def create_task(
        self,
        title: str,
        current_goal: str,
        origin_session_id: str = "",
        constraints: Optional[List[str]] = None,
        interrupt_policy: str = "accept_updates",
        multimodel_mode: str = "off",
        task_id: str | None = None,
    ) -> Dict[str, Any]:
        now = _now()
        task_id = task_id or f"bg_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        history = [{"ts": now, "action": "create", "directive": current_goal, "reason": "created"}]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, origin_session_id, title, status, current_goal,
                    constraints_json, artifacts_json, history_json,
                    interrupt_policy, multimodel_mode, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    origin_session_id,
                    title,
                    "queued",
                    current_goal,
                    _json_dumps(constraints or []),
                    _json_dumps([]),
                    _json_dumps(history),
                    interrupt_policy,
                    multimodel_mode,
                    now,
                    now,
                ),
            )
        return self.get_task(task_id) or {}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def get_active_tasks(self, limit: int = 8) -> List[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        query = f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, (*sorted(ACTIVE_STATUSES), int(limit))).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_task_directive(self, task_id: str, directive: str, action: str = "append_context") -> Dict[str, Any]:
        return self._append_history(task_id, action=action, directive=directive)

    def pause_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        return self._set_status(task_id, "paused", action="pause", reason=reason)

    def resume_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        return self._set_status(task_id, "running", action="resume", reason=reason)

    def cancel_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        return self._set_status(task_id, "cancelled", action="cancel", reason=reason)

    def _set_status(self, task_id: str, status: str, action: str, reason: str = "") -> Dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid task status: {status}")
        return self._append_history(task_id, action=action, directive=reason, status=status, update_latest_directive=False)

    def _append_history(
        self,
        task_id: str,
        action: str,
        directive: str = "",
        status: str | None = None,
        update_latest_directive: bool = True,
    ) -> Dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        now = _now()
        history = list(task.get("history") or [])
        history.append({"ts": now, "action": action, "directive": directive, "reason": directive})
        new_status = status or task["status"]
        latest_directive = directive if update_latest_directive and directive else task.get("latest_user_directive", "")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks SET status = ?, latest_user_directive = ?, history_json = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (new_status, latest_directive, _json_dumps(history), now, task_id),
            )
        return self.get_task(task_id) or {}

    def _row_to_task(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        return {
            "task_id": data["task_id"],
            "origin_session_id": data.get("origin_session_id") or "",
            "title": data.get("title") or "",
            "status": data.get("status") or "queued",
            "current_goal": data.get("current_goal") or "",
            "constraints": _json_loads(data.get("constraints_json"), []),
            "latest_user_directive": data.get("latest_user_directive") or "",
            "interrupt_policy": data.get("interrupt_policy") or "accept_updates",
            "artifacts": _json_loads(data.get("artifacts_json"), []),
            "last_summary": data.get("last_summary") or "",
            "active_worker": data.get("active_worker") or "",
            "multimodel_mode": data.get("multimodel_mode") or "off",
            "history": _json_loads(data.get("history_json"), []),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }


class ConversationIntentRouter:
    """Classify user messages into task-control intents.

    The optional model_decider represents the Hermes default main model bootstrap
    call. Its result is sanitized so router use never counts as a participant in
    later execution or multi-model deliberation.
    """

    def __init__(
        self,
        default_model_name: str = "default-main-model",
        model_decider: Optional[Callable[[Dict[str, Any]], Dict[str, Any] | str]] = None,
    ):
        self.default_model_name = default_model_name
        self.model_decider = model_decider

    def classify(self, message: str, active_tasks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        active_tasks = active_tasks or []
        rule = self._rule_decision(message, active_tasks)
        if rule:
            return self._sanitize(rule)
        if self.model_decider:
            payload = self._model_payload(message, active_tasks)
            raw = self.model_decider(payload)
            parsed = self._parse_model_decision(raw)
            if parsed:
                return self._sanitize(parsed)
        return self._sanitize({
            "intent": "normal_chat",
            "action": "answer",
            "target_task_id": None,
            "confidence": 0.55,
            "requires_confirmation": False,
            "reason": "no task-control signal detected",
        })

    def _model_payload(self, message: str, active_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        summarized = [
            {"task_id": t.get("task_id"), "title": t.get("title"), "status": t.get("status"), "last_summary": t.get("last_summary", "")[:500]}
            for t in active_tasks[:8]
        ]
        return {
            "schema": "hermes_intent_router_input_v1",
            "output_contract": "hermes_intent_decision_v1",
            "message": message[:2000],
            "active_tasks": summarized,
            "rules": [
                "Return only intent/action/target_task_id/confidence/requires_confirmation/reason.",
                "Do not produce task plans or execute changes.",
                "This is a router_call; participant_models must remain empty.",
            ],
        }

    def _rule_decision(self, message: str, active_tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        text = (message or "").strip()
        lowered = text.lower()
        target = self._latest_task_id(active_tasks)
        if any(x in text for x in ["另外", "新建", "另开", "顺便帮我查", "再帮我查"]):
            return {"intent": "new_background_task", "action": "create", "target_task_id": None, "confidence": 0.86, "requires_confirmation": False, "reason": "new-task marker"}
        if any(x in text for x in ["做到哪", "进展", "状态", "现在怎么样"]) or "status" in lowered:
            return {"intent": "status_query", "action": "answer", "target_task_id": target, "confidence": 0.82, "requires_confirmation": target is None, "reason": "status query marker"}
        if any(x in text for x in ["多模型", "真实验证", "仲裁", "升级验证"]):
            return {"intent": "multimodel_escalation", "action": "escalate", "target_task_id": target, "confidence": 0.78, "requires_confirmation": target is None, "reason": "multimodel escalation marker"}
        if any(x in text for x in ["停掉", "取消", "暂停", "继续", "刚才那个", "这个", "它"]):
            action = "append_context"
            if any(x in text for x in ["停掉", "取消"]):
                action = "cancel"
            elif "暂停" in text:
                action = "pause"
            elif "继续" in text:
                action = "resume"
            return {"intent": "current_task_control", "action": action, "target_task_id": target, "confidence": 0.75, "requires_confirmation": target is None or action == "cancel", "reason": "current-task control marker"}
        return None

    def _latest_task_id(self, active_tasks: List[Dict[str, Any]]) -> Optional[str]:
        if not active_tasks:
            return None
        return active_tasks[0].get("task_id")

    def _parse_model_decision(self, raw: Dict[str, Any] | str) -> Optional[Dict[str, Any]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return None
        return raw if isinstance(raw, dict) else None

    def _sanitize(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        intent = str(decision.get("intent") or "normal_chat")
        action = str(decision.get("action") or "answer")
        if intent not in VALID_INTENTS:
            intent = "normal_chat"
        if action not in VALID_ACTIONS:
            action = "answer"
        try:
            confidence = float(decision.get("confidence", 0.5))
        except Exception:
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        result = {
            "schema": "hermes_intent_decision_v1",
            "intent": intent,
            "target_task_id": decision.get("target_task_id"),
            "action": action,
            "confidence": confidence,
            "requires_confirmation": bool(decision.get("requires_confirmation", False)),
            "reason": str(decision.get("reason") or ""),
            "router_model": self.default_model_name,
            "router_call": True,
            "participant_models": [],
        }
        result["trace"] = {
            "router_model": result["router_model"],
            "router_call": True,
            "participant_models": [],
        }
        return result


def normalize_fugu_response(raw: Dict[str, Any] | str | None) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {"raw_text": raw}
    raw = raw if isinstance(raw, dict) else {}
    return {
        "schema": "hermes_conductor_plan_v1",
        "provider": "sakana_fugu",
        "status": raw.get("status", "ok"),
        "role_plan": raw.get("role_plan") if isinstance(raw.get("role_plan"), list) else [],
        "workflow": raw.get("workflow") if isinstance(raw.get("workflow"), list) else [],
        "evidence_requests": raw.get("evidence_requests") if isinstance(raw.get("evidence_requests"), list) else [],
        "risks": raw.get("risks") if isinstance(raw.get("risks"), list) else [],
        "confidence": raw.get("confidence", 0.0),
        "truth_certified": False,
        "fail_soft": bool(raw.get("fail_soft", False)),
    }


class FuguAdapter:
    """Optional Sakana Fugu API adapter.

    It is fail-soft by design. Without explicit enabled/base_url/API key, plan()
    returns an unavailable Hermes-schema response instead of raising.
    """

    def __init__(self, config: Dict[str, Any] | None = None, http_post: Optional[Callable[..., Any]] = None):
        self.config = config or {}
        self.http_post = http_post

    def plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.get("enabled"):
            return normalize_fugu_response({"status": "unavailable", "fail_soft": True, "risks": ["sakana_fugu disabled"]})
        base_url = str(self.config.get("base_url") or "").strip()
        api_key_env = str(self.config.get("api_key_env") or "SAKANA_FUGU_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not base_url or not api_key:
            return normalize_fugu_response({"status": "unavailable", "fail_soft": True, "risks": ["sakana_fugu missing base_url or api key"]})
        try:
            raw = self._post(base_url, api_key, payload)
            return normalize_fugu_response(raw)
        except Exception as exc:
            return normalize_fugu_response({"status": "error", "fail_soft": True, "risks": [f"sakana_fugu call failed: {exc}"]})

    def _post(self, base_url: str, api_key: str, payload: Dict[str, Any]) -> Any:
        if self.http_post:
            return self.http_post(base_url, api_key, payload)
        body = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(
            base_url,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        timeout = int(self.config.get("timeout_seconds") or 180)
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        return json.loads(text)


DEFAULT_ROLES = ["scout", "planner", "implementer", "critic", "verifier", "architect", "arbiter"]
TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}


def _ordered_unique(values: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


class ModelCapabilityRouter:
    """Route verification surfaces to specialist models with role separation.

    This is a contract-only router: it selects verifier roles and evidence
    requirements, but does not call models or mark work complete.
    """

    def __init__(self, profiles: Dict[str, Any] | None = None):
        self.profiles = profiles or {}
        role_raw = self.profiles.get("role_separation")
        overlap_raw = self.profiles.get("overlap_voting")
        surfaces_raw = self.profiles.get("surfaces")
        self.role_separation: Dict[str, Any] = role_raw if isinstance(role_raw, dict) else {}
        self.overlap_voting: Dict[str, Any] = overlap_raw if isinstance(overlap_raw, dict) else {}
        self.surfaces: Dict[str, Any] = surfaces_raw if isinstance(surfaces_raw, dict) else {}

    def route(
        self,
        surfaces: List[str],
        risk_tier: str = "T0",
        implementer_model: str = "",
        available_models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        available = set(available_models or [])
        certification_participants: List[str] = []
        required_evidence: List[str] = []
        surface_routes: Dict[str, Any] = {}
        primary_candidates: List[str] = []
        forbid_self = bool(self.role_separation.get("forbid_self_certification", True))

        for surface in surfaces:
            cfg_raw = self.surfaces.get(surface)
            cfg: Dict[str, Any] = cfg_raw if isinstance(cfg_raw, dict) else {}
            implementer_candidates = list(cfg.get("implementers") or [])
            primary = ""
            for candidate in implementer_candidates:
                if available and candidate not in available:
                    continue
                primary = candidate
                break
            if not primary and implementer_candidates:
                primary = implementer_candidates[0]
            if primary:
                primary_candidates.append(primary)

            candidates = list(cfg.get("verifiers") or []) + list(cfg.get("fallback_verifiers") or [])
            verifier = ""
            for candidate in candidates:
                if available and candidate not in available:
                    continue
                if forbid_self and (implementer_model or primary) and candidate in {implementer_model, primary}:
                    continue
                verifier = candidate
                break
            if not verifier and candidates:
                verifier = candidates[0]
            if verifier and verifier not in {implementer_model, primary}:
                certification_participants.append(verifier)
            evidence = list(cfg.get("final_evidence") or [])
            required_evidence.extend(evidence)
            surface_routes[surface] = {
                "primary": primary,
                "verifier": verifier,
                "required_evidence": evidence,
                "implementer_vote_counts_for_certification": False if (implementer_model or primary) else True,
            }

        trigger_count = int(self.overlap_voting.get("trigger_when_surface_count_gte", 2) or 2)
        trigger_tier = str(self.overlap_voting.get("trigger_when_risk_gte", "T2") or "T2")
        overlap_required = bool(self.overlap_voting.get("enabled", True)) and (
            len(surfaces) >= trigger_count or TIER_RANK.get(str(risk_tier), 0) >= TIER_RANK.get(trigger_tier, 2)
        )
        arbiter = str(self.overlap_voting.get("arbiter_model") or "")
        if overlap_required and arbiter and (not available or arbiter in available) and arbiter != implementer_model:
            certification_participants.append(arbiter)

        return {
            "schema": "hermes_model_capability_route_v1",
            "surfaces": list(surfaces),
            "risk_tier": risk_tier,
            "implementer_model": implementer_model,
            "primary_models": _ordered_unique(primary_candidates),
            "surface_routes": surface_routes,
            "overlap_voting_required": overlap_required,
            "certification_participants": _ordered_unique(certification_participants),
            "required_evidence": _ordered_unique(required_evidence),
            "truth_authority": "hermes_evidence_gate",
        }


class AcceptanceGovernance:
    """Fail-closed acceptance gate for model votes plus objective evidence."""

    def __init__(
        self,
        require_objective_evidence: bool = True,
        require_evidence_refs_for_votes: bool = True,
        forbid_model_only_certification: bool = True,
        forbid_self_certification: bool = True,
        require_independent_verifier_vote: bool = False,
    ):
        self.require_objective_evidence = require_objective_evidence
        self.require_evidence_refs_for_votes = require_evidence_refs_for_votes
        self.forbid_model_only_certification = forbid_model_only_certification
        self.forbid_self_certification = forbid_self_certification
        self.require_independent_verifier_vote = require_independent_verifier_vote

    def evaluate(self, claims: List[Dict[str, Any]], evidence: List[Dict[str, Any]], votes: List[Dict[str, Any]], implementer_model: str = "") -> Dict[str, Any]:
        failures: List[str] = []
        evidence_ids = {str(item.get("id")) for item in evidence if isinstance(item, dict) and item.get("id")}
        if self.require_objective_evidence and claims and not evidence_ids:
            failures.append("objective_evidence_missing")
        independent_approve_votes = 0
        for vote in votes or []:
            if not isinstance(vote, dict):
                failures.append("vote_not_object")
                continue
            model = str(vote.get("model") or "unknown")
            refs = [str(ref) for ref in (vote.get("evidence_refs") or [])]
            if self.require_evidence_refs_for_votes and not refs:
                failures.append(f"vote_evidence_refs_missing:{model}")
            if refs and not set(refs).issubset(evidence_ids):
                failures.append(f"vote_evidence_refs_unknown:{model}")
            if self.forbid_self_certification and implementer_model and model == implementer_model:
                failures.append(f"self_certification_forbidden:{model}")
            verdict = str(vote.get("vote") or "").lower()
            if verdict in {"approve", "approved"} and (not implementer_model or model != implementer_model):
                independent_approve_votes += 1
            if verdict in {"reject", "rejected"}:
                failures.append(f"reject_vote:{model}")
        if self.require_independent_verifier_vote and claims and independent_approve_votes <= 0:
            failures.append("independent_verifier_vote_missing")
        if failures:
            status = "advisory_only" if "objective_evidence_missing" in failures or not evidence_ids else "not_certified"
        else:
            status = "certified"
        return {
            "schema": "hermes_acceptance_governance_result_v1",
            "status": status,
            "failures": sorted(set(failures)),
            "claim_count": len(claims or []),
            "evidence_count": len(evidence_ids),
            "model_judgment_policy": "advisory_only_without_evidence",
        }


DEFAULT_MAINLOOP_CERTIFICATION_JOBS = HOME / ".hermes" / "state" / "mainloop-verification" / "jobs"
AUTHORITY_SMUGGLING_KEYS = {
    "mark_done",
    "task_status",
    "lifecycle_status",
    "external_write",
    "external_writes",
    "outbound_message",
    "memory_write",
    "skill_write",
    "provider_config_change",
}


class MainloopCertificationQueue:
    """Durable queue for background mainloop certification requests.

    Queue entries are local JSON records only. They preserve evidence and gate
    state for a later worker, but cannot mark tasks done or perform external
    side effects by construction.
    """

    def __init__(self, root: str | Path = DEFAULT_MAINLOOP_CERTIFICATION_JOBS):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _digest(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _job_path(self, job_id: str) -> Path:
        safe = "".join(ch for ch in job_id if ch.isalnum() or ch in {"-", "_"})
        return self.root / f"{safe}.json"

    def _has_authority_smuggling(self, value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key) in AUTHORITY_SMUGGLING_KEYS:
                    return True
                if self._has_authority_smuggling(child):
                    return True
        elif isinstance(value, list):
            return any(self._has_authority_smuggling(item) for item in value)
        return False

    def enqueue(self, *, decision: Dict[str, Any], user_message: str, final_response: str, implementer_model: str = "") -> Dict[str, Any]:
        request_payload = {
            "decision": decision,
            "user_message": user_message,
            "final_response": final_response,
            "implementer_model": implementer_model,
        }
        if self._has_authority_smuggling(request_payload):
            return {"schema": "hermes_mainloop_certification_job_v1", "status": "rejected", "reason": "authority_smuggling_detected"}
        digest = self._digest(request_payload)
        job_id = f"mainloop-cert-{digest[:16]}"
        path = self._job_path(job_id)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    return existing
            except Exception:
                pass
        job = {
            "schema": "hermes_mainloop_certification_job_v1",
            "job_id": job_id,
            "status": "queued",
            "created_at": _now(),
            "request_digest": digest,
            "external_side_effects_enabled": False,
            "allowed_to_mark_done": False,
            "request": request_payload,
        }
        path.write_text(json.dumps(job, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        return job

    def run_job(self, job_id: str, runner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None) -> Dict[str, Any]:
        path = self._job_path(job_id)
        if not path.exists():
            return {"schema": "hermes_mainloop_certification_job_v1", "job_id": job_id, "status": "missing"}
        job = json.loads(path.read_text(encoding="utf-8"))
        if job.get("status") == "completed":
            return job
        job["status"] = "running"
        job["started_at"] = _now()
        path.write_text(json.dumps(job, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        try:
            if runner is None:
                result = {"status": "queued_for_external_worker", "reason": "no_runner_configured"}
            else:
                result = runner(job)
            job["status"] = "completed"
            job["completed_at"] = _now()
            job["certification_result"] = result
        except Exception as exc:
            job["status"] = "failed"
            job["failed_at"] = _now()
            job["error"] = str(exc)
        path.write_text(json.dumps(job, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        return job


COMPLETION_CLAIM_TERMS = ("完成", "验收", "通过", "done", "complete", "completed", "verified", "certified", "production-ready", "可上线")
T3_TERMS = ("Hermes", "主循环", "门禁", "生产", "长期稳定", "自修改", "外部副作用", "MCP", "部署", "删除")
UI_TERMS = ("UI", "界面", "视觉", "截图", "safe-area", "uIMax", "A+")
CODE_TERMS = ("代码", "测试", "pytest", "build", "实现", "commit", "push")


class MainloopVerificationGate:
    """Evaluate final-response completion claims before turn finalization.

    The gate is deliberately evidence-bound. It does not call external models;
    it classifies the turn, extracts objective tool evidence already present in
    the transcript, and returns a blocking/degraded decision when the response
    makes a completion-critical claim without evidence and independent review.
    """

    def __init__(self, profiles: Dict[str, Any] | None = None):
        self.profiles = profiles or {}
        cfg_raw = self.profiles.get("mainloop_verification")
        self.cfg: Dict[str, Any] = cfg_raw if isinstance(cfg_raw, dict) else {}
        acceptance_raw = self.profiles.get("acceptance_governance")
        self.acceptance_cfg: Dict[str, Any] = acceptance_raw if isinstance(acceptance_raw, dict) else {}

    def _has_completion_claim(self, text: str) -> bool:
        lowered = text.lower()
        return any(term.lower() in lowered for term in COMPLETION_CLAIM_TERMS)

    def _risk_tier(self, user_message: str, final_response: str) -> str:
        text = f"{user_message}\n{final_response}"
        if any(term.lower() in text.lower() for term in T3_TERMS):
            return "T3"
        if self._has_completion_claim(text):
            return "T2"
        return "T0"

    def _is_internal_projection(self, user_message: str, final_response: str) -> bool:
        prompt = user_message or ""
        if "Hermes ContextCard" in prompt and "SOURCE_CARD" in prompt and "EVIDENCE_SPANS" in prompt:
            return True
        response = (final_response or "").lstrip()
        if response.startswith("{") and "cardVersion" in response and "Hermes Context" in response:
            return True
        if response.startswith("{") and "\"planner_model\"" in response and "\"patches\"" in response and "\"diagnosis\"" in response:
            return True
        if "Previous turn stopped because max_iterations_reached" in prompt and "Continue the task from that handoff" in prompt:
            return True
        return False

    def _surfaces(self, user_message: str, final_response: str) -> List[str]:
        text = f"{user_message}\n{final_response}"
        surfaces: List[str] = []
        if self._has_completion_claim(text):
            surfaces.append("completion_claim")
        if any(term.lower() in text.lower() for term in UI_TERMS):
            surfaces.append("ui_visual")
        if any(term.lower() in text.lower() for term in CODE_TERMS):
            surfaces.append("code_correctness")
        if any(term.lower() in text.lower() for term in T3_TERMS):
            surfaces.append("hermes_self_modification")
        return _ordered_unique(surfaces)

    def _infer_evidence_classes(self, *, source: str, content: str) -> List[str]:
        text = f"{source}\n{content}".lower()
        classes: List[str] = []
        if "pytest" in text or " passed" in text:
            classes.extend(["focused_pytest", "test_output"])
        if "py_compile" in text or "compile" in text:
            classes.append("py_compile")
        if "post-update" in text or "post_update" in text:
            classes.append("post_update_check")
        if "git diff" in text or "diff --check" in text:
            classes.append("git_diff")
        if "git status" in text:
            classes.append("git_status")
        if "config" in text or "yaml" in text or "readback" in text:
            classes.append("config_readback")
        if "build" in text or "compiled" in text:
            classes.append("build_output")
        if "smoke" in text or "worker" in text or "runtime" in text:
            classes.append("runtime_smoke")
        return _ordered_unique(classes)

    def _evidence_from_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        evidence: List[Dict[str, Any]] = []
        for idx, message in enumerate(messages or []):
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            lower = content.lower()
            status = "fail" if any(marker in lower for marker in ("traceback", "error", "failed", "exit_code\": 1")) else "pass"
            source = str(message.get("name") or "tool")
            evidence.append({
                "id": f"E-tool-{idx}",
                "source": source,
                "status": status,
                "supports": [],
                "evidence_classes": self._infer_evidence_classes(source=source, content=content),
            })
        return evidence

    def _route_required_evidence_coverage(self, route: Dict[str, Any], evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
        required = _ordered_unique([str(item) for item in route.get("required_evidence") or [] if item])
        present: List[str] = []
        for item in evidence or []:
            if not isinstance(item, dict):
                continue
            for cls in item.get("evidence_classes") or []:
                if cls:
                    present.append(str(cls))
            explicit = item.get("evidence_class")
            if explicit:
                present.append(str(explicit))
        present = _ordered_unique(present)
        missing = [cls for cls in required if cls not in present]
        coverage = 1.0 if not required else (len(required) - len(missing)) / len(required)
        return {
            "required": required,
            "present": present,
            "missing": missing,
            "coverage": coverage,
        }

    def _build_complex_verifier_packet(
        self,
        *,
        complexity_scan: Dict[str, Any],
        surfaces: List[str],
        claims: List[Dict[str, Any]],
        route_required_evidence: Dict[str, Any],
        implementer_model: str,
    ) -> Dict[str, Any]:
        return {
            "schema": "hermes_complex_task_verifier_packet_v1",
            "status": "requires_independent_verification",
            "complexity_scan": complexity_scan,
            "surfaces": surfaces,
            "claims": claims,
            "required_evidence": route_required_evidence.get("required") or [],
            "missing_evidence": route_required_evidence.get("missing") or [],
            "implementer_model": implementer_model,
            "forbid_self_certification": True,
            "external_side_effects_enabled": False,
            "allowed_to_mark_done": False,
        }

    def evaluate_turn(
        self,
        user_message: str,
        final_response: str | None,
        messages: List[Dict[str, Any]],
        implementer_model: str = "",
        available_models: Optional[List[str]] = None,
        votes: Optional[List[Dict[str, Any]]] = None,
        complexity_scan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.cfg.get("enabled"):
            return {"schema": "hermes_mainloop_verification_gate_v1", "status": "disabled", "blocking": False}
        response = str(final_response or "")
        if self._is_internal_projection(user_message or "", response):
            return {"schema": "hermes_mainloop_verification_gate_v1", "status": "skipped_internal_projection", "blocking": False}
        surfaces = self._surfaces(user_message or "", response)
        risk_tier = self._risk_tier(user_message or "", response)
        complex_scan_active = isinstance(complexity_scan, dict) and complexity_scan.get("classification") == "complex"
        if complex_scan_active and self._has_completion_claim(response):
            surfaces.append("complex_task_harness")
            risk_tier = "T3"
        surfaces = _ordered_unique(surfaces)
        if not surfaces:
            return {"schema": "hermes_mainloop_verification_gate_v1", "status": "skipped_low_risk", "blocking": False, "risk_tier": risk_tier, "surfaces": []}

        claims = [{"id": f"C-{idx+1}", "surface": surface, "text": response[:500]} for idx, surface in enumerate(surfaces)]
        evidence = self._evidence_from_messages(messages or [])
        route = ModelCapabilityRouter(self.profiles).route(
            surfaces=surfaces,
            risk_tier=risk_tier,
            implementer_model=implementer_model,
            available_models=available_models or [],
        )
        route_required_evidence = self._route_required_evidence_coverage(route, evidence)
        governance = AcceptanceGovernance(
            require_objective_evidence=bool(self.acceptance_cfg.get("require_objective_evidence", True)),
            require_evidence_refs_for_votes=bool(self.acceptance_cfg.get("require_evidence_refs_for_votes", True)),
            forbid_model_only_certification=bool(self.acceptance_cfg.get("forbid_model_only_certification", True)),
            forbid_self_certification=bool(self.acceptance_cfg.get("forbid_self_certification", True)),
            require_independent_verifier_vote=bool(self.acceptance_cfg.get("require_independent_verifier_vote", True)),
        )
        acceptance = governance.evaluate(claims=claims, evidence=evidence, votes=votes or [], implementer_model=implementer_model)
        if route_required_evidence["missing"] and risk_tier in set(self.cfg.get("blocking_tiers") or []):
            failures = list(acceptance.get("failures") or [])
            failures.append("route_required_evidence_missing:" + ",".join(route_required_evidence["missing"]))
            acceptance = dict(acceptance)
            acceptance["failures"] = sorted(set(failures))
            if acceptance.get("status") == "certified":
                acceptance["status"] = "not_certified"
        blocking_tiers = set(self.cfg.get("blocking_tiers") or [])
        blocking = risk_tier in blocking_tiers and acceptance.get("status") != "certified" and not bool(self.cfg.get("advisory_only", False))
        notice = ""
        if blocking:
            notice = "门禁未认证：不能声明已完成/已验收；需要补充客观证据和独立 verifier vote 后再提升为 certified。"
        elif acceptance.get("status") != "certified":
            notice = "门禁降级：当前只能作为 advisory，不能声明 certified。"
        gate_status = "not_certified" if blocking else acceptance.get("status")
        verifier_packet = None
        if complex_scan_active:
            verifier_packet = self._build_complex_verifier_packet(
                complexity_scan=complexity_scan or {},
                surfaces=surfaces,
                claims=claims,
                route_required_evidence=route_required_evidence,
                implementer_model=implementer_model,
            )
        return {
            "schema": "hermes_mainloop_verification_gate_v1",
            "status": gate_status,
            "blocking": blocking,
            "risk_tier": risk_tier,
            "surfaces": surfaces,
            "complexity_scan": complexity_scan if complex_scan_active else None,
            "verifier_packet": verifier_packet,
            "claims": claims,
            "evidence": evidence,
            "route": route,
            "route_required_evidence": route_required_evidence,
            "acceptance": acceptance,
            "response_notice": notice,
        }


def load_model_profiles(profile_path: str | Path = DEFAULT_MODEL_PROFILES) -> Dict[str, Any]:
    """Load model_profiles.yaml with YAML or fallback parser."""
    path = Path(profile_path).expanduser()
    if not path.exists():
        return {"version": 1, "conductor": {"enabled": False}, "profiles": {}}
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        import yaml  # type: ignore
        loaded = yaml.safe_load(text) or {}
        return loaded if isinstance(loaded, dict) else {"profiles": {}}
    except Exception:
        pass
    # Minimal fallback
    profiles: Dict[str, Any] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("profiles:"):
            continue
        if not line.startswith(" ") and stripped.endswith(":") and ":" not in stripped[:-1]:
            current = stripped[:-1].strip()
            profiles[current] = {}
    return {"version": 1, "conductor": {"enabled": False}, "profiles": profiles}


class MultiModelConductor:
    """Role-based multi-model planning with conductor_trace output.

    Given a task context and model profiles, produce a role_plan that assigns
    models to roles (scout/planner/implementer/critic/verifier/architect/arbiter).
    The plan is advisory — the caller decides whether to actually invoke models.
    """

    def __init__(self, profiles: Dict[str, Any] | None = None, profile_path: str | Path = DEFAULT_MODEL_PROFILES):
        self.raw_profiles = profiles or load_model_profiles(profile_path)
        conductor_raw = self.raw_profiles.get("conductor")
        self.conductor_cfg: Dict[str, Any] = conductor_raw if isinstance(conductor_raw, dict) else {}
        profiles_raw = self.raw_profiles.get("profiles")
        self.profiles: Dict[str, Any] = profiles_raw if isinstance(profiles_raw, dict) else {}

    def is_enabled(self) -> bool:
        return bool(self.conductor_cfg.get("enabled"))

    def build_role_plan(
        self,
        task_context: Dict[str, Any],
        router_model: str = "",
        exclude_router_from_participants: bool = True,
    ) -> Dict[str, Any]:
        """Build a role plan from model profiles.

        Args:
            task_context: dict with keys like goal, constraints, risk_level, multimodel_mode
            router_model: model used for intent routing (isolated from participants)
            exclude_router_from_participants: if True, router_model is excluded from
                participant selection even if it appears in profiles
        """
        risk = str(task_context.get("risk_level", "low")).lower()
        multimodel_mode = str(task_context.get("multimodel_mode", "off")).lower()

        # Filter profiles to those with roles
        eligible: Dict[str, Dict[str, Any]] = {}
        for model_id, profile in self.profiles.items():
            if not isinstance(profile, dict):
                continue
            if profile.get("enabled") is False and model_id != "default_main":
                continue
            roles = profile.get("roles") or []
            if not roles:
                continue
            if exclude_router_from_participants and model_id == router_model:
                continue
            eligible[model_id] = {"roles": roles, "cost_tier": profile.get("cost_tier", "medium")}

        # Assign models to roles
        role_plan: List[Dict[str, Any]] = []
        assigned_models: List[str] = []
        for role in DEFAULT_ROLES:
            for model_id, info in eligible.items():
                if role in info["roles"]:
                    role_plan.append({"role": role, "model": model_id, "cost_tier": info["cost_tier"]})
                    assigned_models.append(model_id)
                    break  # one model per role

        # Build conductor_trace
        conductor_trace = {
            "router_model": router_model,
            "router_call": True,
            "participant_models": sorted(set(assigned_models)),
            "role_plan": role_plan,
            "risk_level": risk,
            "multimodel_mode": multimodel_mode,
            "conductor_enabled": self.is_enabled(),
            "profile_count": len(self.profiles),
            "eligible_count": len(eligible),
        }

        return {
            "schema": "hermes_conductor_role_plan_v1",
            "role_plan": role_plan,
            "participant_models": sorted(set(assigned_models)),
            "conductor_trace": conductor_trace,
            "conductor_enabled": self.is_enabled(),
        }

    def build_model_specific_instruction(self, model_id: str, role: str, task_context: Dict[str, Any]) -> str:
        """Generate a model-specific instruction based on role and profile."""
        profile = self.profiles.get(model_id, {})
        guidance = profile.get("guidance", "")
        goal = task_context.get("goal", "")
        constraints = task_context.get("constraints", [])

        role_instructions = {
            "scout": "You are a lightweight scout. Quickly identify key facts, risks, and open questions. Do not produce implementation plans.",
            "planner": "You are the architecture planner. Design the implementation approach with concrete steps.",
            "implementer": "You are the implementer. Write production-ready code following the plan.",
            "critic": "You are the critic. Find contradictions, missing edge cases, and architectural risks.",
            "verifier": "You are the verifier. Check that claims are backed by evidence. Flag unsupported assertions.",
            "architect": "You are the architect. Make high-level design decisions and trade-off analysis.",
            "arbiter": "You are the arbiter. Resolve conflicts between other models' recommendations.",
        }

        parts = [role_instructions.get(role, f"You are assigned role: {role}")]
        if guidance:
            parts.append(f"Profile guidance: {guidance}")
        if goal:
            parts.append(f"Task goal: {goal}")
        if constraints:
            parts.append(f"Constraints: {'; '.join(str(c) for c in constraints)}")
        return "\n\n".join(parts)
