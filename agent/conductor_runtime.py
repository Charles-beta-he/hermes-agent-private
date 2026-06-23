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
    return bool(conductor.get("enabled") or router.get("enabled") or fugu.get("enabled"))


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
        forbid_self = bool(self.role_separation.get("forbid_self_certification", True))

        for surface in surfaces:
            cfg_raw = self.surfaces.get(surface)
            cfg: Dict[str, Any] = cfg_raw if isinstance(cfg_raw, dict) else {}
            candidates = list(cfg.get("verifiers") or []) + list(cfg.get("fallback_verifiers") or [])
            verifier = ""
            for candidate in candidates:
                if available and candidate not in available:
                    continue
                if forbid_self and implementer_model and candidate == implementer_model:
                    continue
                verifier = candidate
                break
            if not verifier and candidates:
                verifier = candidates[0]
            if verifier and verifier != implementer_model:
                certification_participants.append(verifier)
            evidence = list(cfg.get("final_evidence") or [])
            required_evidence.extend(evidence)
            surface_routes[surface] = {
                "verifier": verifier,
                "required_evidence": evidence,
                "implementer_vote_counts_for_certification": False if implementer_model else True,
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
    ):
        self.require_objective_evidence = require_objective_evidence
        self.require_evidence_refs_for_votes = require_evidence_refs_for_votes
        self.forbid_model_only_certification = forbid_model_only_certification
        self.forbid_self_certification = forbid_self_certification

    def evaluate(self, claims: List[Dict[str, Any]], evidence: List[Dict[str, Any]], votes: List[Dict[str, Any]], implementer_model: str = "") -> Dict[str, Any]:
        failures: List[str] = []
        evidence_ids = {str(item.get("id")) for item in evidence if isinstance(item, dict) and item.get("id")}
        if self.require_objective_evidence and claims and not evidence_ids:
            failures.append("objective_evidence_missing")
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
            if verdict in {"reject", "rejected"}:
                failures.append(f"reject_vote:{model}")
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
