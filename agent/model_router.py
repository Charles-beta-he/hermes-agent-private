"""Cache-safe smart model routing for fresh sessions and delegated children.

Routing is deliberately restricted to cache boundaries.  A main conversation may
be routed before its first model call; a populated/resumed conversation is never
switched automatically.  Delegated children use the same pure policy resolver in
``tools.delegate_tool`` before their fresh agent is built.

The module supports both three-tier and four-tier executable schemas under
``smart_model_routing.tiers``.  It also preserves the existing four-tier
``router.tiers`` configuration through an explicit compatibility adapter.
``candidate_models`` is telemetry/optimizer input only and is never silently
converted into executable routing configuration.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

_THREE_TIER_ORDER = ("LIGHT", "STANDARD", "HEAVY")
_FOUR_TIER_ORDER = ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")
_MAX_CLASSIFY_CHARS = 4000
_ROUTE_STATE_LOCK = threading.Lock()
_RECEIPT_LOCK = threading.Lock()


def _enabled_flag(value: Any) -> bool:
    """Only a real YAML/JSON boolean may activate routing."""
    return value is True


@dataclass(frozen=True)
class RouteTarget:
    provider: str
    model: str


@dataclass(frozen=True)
class RoutingPolicy:
    enabled: bool
    tiers: Dict[str, RouteTarget] = field(default_factory=dict)
    tier_order: tuple[str, ...] = ()
    boundaries: Dict[str, float] = field(default_factory=dict)
    config_source: str = "none"
    diagnostics: tuple[str, ...] = ()
    min_tier: str = ""


@dataclass(frozen=True)
class RouteDecision:
    status: str
    tier: str = ""
    provider: str = ""
    model: str = ""
    config_source: str = "none"
    reason: str = ""
    diagnostics: tuple[str, ...] = ()


def _normalise_tiers(
    raw: Any,
    order: Sequence[str],
    diagnostics: list[str],
) -> Dict[str, RouteTarget]:
    if not isinstance(raw, Mapping):
        return {}
    result: Dict[str, RouteTarget] = {}
    raw_by_upper = {str(key).strip().upper(): value for key, value in raw.items()}
    for tier in order:
        entry = raw_by_upper.get(tier)
        if not isinstance(entry, Mapping):
            diagnostics.append(f"missing_tier:{tier}")
            continue
        provider = str(entry.get("provider") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not provider or not model:
            diagnostics.append(f"invalid_tier_target:{tier}")
            continue
        result[tier] = RouteTarget(provider=provider, model=model)
    return result


def resolve_routing_policy(config: Optional[Dict[str, Any]] = None) -> RoutingPolicy:
    """Normalize supported routing schemas into one explicit executable policy."""
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly

            config = load_config_readonly()
        except Exception as exc:  # noqa: BLE001
            return RoutingPolicy(False, diagnostics=(f"config_load_failed:{type(exc).__name__}",))
    config = config if isinstance(config, dict) else {}
    smart = config.get("smart_model_routing")
    smart = smart if isinstance(smart, dict) else {}
    legacy = config.get("router")
    legacy = legacy if isinstance(legacy, dict) else {}
    diagnostics: list[str] = []

    if smart.get("candidate_models"):
        diagnostics.append("candidate_models_not_execution_targets")

    smart_tiers = smart.get("tiers")
    if _enabled_flag(smart.get("enabled")) and isinstance(smart_tiers, Mapping):
        tier_keys = {str(key).strip().upper() for key in smart_tiers}
        if tier_keys == set(_FOUR_TIER_ORDER):
            tier_order = _FOUR_TIER_ORDER
        elif tier_keys == set(_THREE_TIER_ORDER):
            tier_order = _THREE_TIER_ORDER
        else:
            diagnostics.append("invalid_canonical_tier_set")
            diagnostics.extend(
                f"unknown_tier:{tier}" for tier in sorted(
                    tier_keys - set(_FOUR_TIER_ORDER) - set(_THREE_TIER_ORDER)
                )
            )
            diagnostics.append("no_complete_executable_tiers")
            return RoutingPolicy(
                enabled=False,
                tier_order=(),
                config_source="smart_model_routing.tiers",
                diagnostics=tuple(dict.fromkeys(diagnostics)),
            )

        tiers = _normalise_tiers(smart_tiers, tier_order, diagnostics)
        enabled = len(tiers) == len(tier_order)
        if not enabled:
            diagnostics.append("no_complete_executable_tiers")
        boundaries: Dict[str, float] = {}
        if tier_order == _FOUR_TIER_ORDER:
            raw_boundaries = smart.get("boundaries")
            if isinstance(raw_boundaries, Mapping):
                for key in ("simple_medium", "medium_complex", "complex_reasoning"):
                    try:
                        boundaries[key] = float(raw_boundaries[key])
                    except (KeyError, TypeError, ValueError):
                        pass
        floor = str(smart.get("min_tier") or "").strip().upper()
        return RoutingPolicy(
            enabled=enabled,
            tiers=tiers,
            tier_order=tier_order,
            boundaries=boundaries,
            config_source="smart_model_routing.tiers",
            diagnostics=tuple(dict.fromkeys(diagnostics)),
            min_tier=floor if floor in tier_order else "",
        )

    legacy_tiers = legacy.get("tiers")
    if (
        _enabled_flag(smart.get("enabled"))
        and _enabled_flag(legacy.get("enabled"))
        and isinstance(legacy_tiers, Mapping)
    ):
        tiers = _normalise_tiers(legacy_tiers, _FOUR_TIER_ORDER, diagnostics)
        enabled = len(tiers) == len(_FOUR_TIER_ORDER)
        if not enabled:
            diagnostics.append("no_complete_executable_tiers")
        raw_boundaries = legacy.get("boundaries")
        boundaries: Dict[str, float] = {}
        if isinstance(raw_boundaries, Mapping):
            for key in ("simple_medium", "medium_complex", "complex_reasoning"):
                try:
                    boundaries[key] = float(raw_boundaries[key])
                except (KeyError, TypeError, ValueError):
                    pass
        return RoutingPolicy(
            enabled=enabled,
            tiers=tiers,
            tier_order=_FOUR_TIER_ORDER,
            boundaries=boundaries,
            config_source="router.tiers_legacy_adapter",
            diagnostics=tuple(dict.fromkeys(diagnostics)),
        )

    diagnostics.append("no_executable_tiers")
    return RoutingPolicy(
        enabled=False,
        config_source="none",
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def _parse_classifier_tier(raw: str, valid_tiers: Sequence[str]) -> Optional[str]:
    """Accept one exact tier token; reject prose and ambiguous output."""
    token = (raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z_]+", token):
        return None
    return token if token in set(valid_tiers) else None


def _heuristic_complexity_score(message: str) -> float:
    """Cheap deterministic score on the legacy 0.00–0.30 boundary scale."""
    text = (message or "").strip().lower()
    if not text:
        return 0.0
    score = min(len(text), _MAX_CLASSIFY_CHARS) / _MAX_CLASSIFY_CHARS * 0.06
    if "```" in text or "traceback" in text or "error:" in text:
        score += 0.035
    if any(term in text for term in ("implement", "refactor", "debug", "测试", "实现", "修复", "重构")):
        score += 0.045
    if any(term in text for term in ("multi-file", "production", "architecture", "security", "迁移", "生产", "架构", "闭环")):
        score += 0.075
    if any(term in text for term in ("prove", "invariant", "adversarial", "root cause", "全盘", "证明", "不变量", "敌对", "根因")):
        score += 0.09
    if text.count("\n") >= 12:
        score += 0.025
    return min(score, 0.30)


def _heuristic_tier(message: str, policy: RoutingPolicy) -> str:
    score = _heuristic_complexity_score(message)
    if policy.tier_order == _FOUR_TIER_ORDER:
        simple_medium = policy.boundaries.get("simple_medium", 0.08)
        medium_complex = policy.boundaries.get("medium_complex", 0.15)
        complex_reasoning = policy.boundaries.get("complex_reasoning", 0.25)
        if score < simple_medium:
            return "SIMPLE"
        if score < medium_complex:
            return "MEDIUM"
        if score < complex_reasoning:
            return "COMPLEX"
        return "REASONING"
    if score < 0.10:
        return "LIGHT"
    if score < 0.22:
        return "STANDARD"
    return "HEAVY"


def _configured_classifier(
    message: str,
    policy: RoutingPolicy,
    config: Dict[str, Any],
    *,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    auxiliary = config.get("auxiliary") if isinstance(config, dict) else None
    auxiliary = auxiliary if isinstance(auxiliary, dict) else {}
    classifier_cfg = auxiliary.get("routing_classifier")
    classifier_cfg = classifier_cfg if isinstance(classifier_cfg, dict) else {}
    # Empty/auto model means the deterministic classifier.  Do not spend a main
    # model call merely to choose the model for the first real call.
    if not str(classifier_cfg.get("model") or "").strip():
        return None
    try:
        from agent.auxiliary_client import get_auxiliary_extra_body, get_text_auxiliary_client

        client, model = get_text_auxiliary_client(
            "routing_classifier", main_runtime=main_runtime
        )
        if client is None or not model:
            return None
        tiers = ", ".join(policy.tier_order)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the request by required model capability. "
                        f"Reply with exactly one token from: {tiers}."
                    ),
                },
                {"role": "user", "content": (message or "")[:_MAX_CLASSIFY_CHARS]},
            ],
            temperature=0,
            max_tokens=16,
            timeout=float(classifier_cfg.get("timeout") or 20),
            extra_body=get_auxiliary_extra_body() or None,
        )
        raw = response.choices[0].message.content or ""
        return _parse_classifier_tier(raw, policy.tier_order)
    except Exception as exc:  # noqa: BLE001
        logger.info("smart routing classifier failed: %s", type(exc).__name__)
        return None


def select_route_decision(
    message: str,
    config: Optional[Dict[str, Any]] = None,
    *,
    classifier: Optional[Callable[[str, Sequence[str]], str]] = None,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> RouteDecision:
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly

            loaded = load_config_readonly()
            config = loaded if isinstance(loaded, dict) else {}
        except Exception:
            config = {}
    else:
        config = config if isinstance(config, dict) else {}
    policy = resolve_routing_policy(config)
    if not policy.enabled:
        return RouteDecision(
            status="disabled",
            config_source=policy.config_source,
            reason="routing policy is not executable",
            diagnostics=policy.diagnostics,
        )

    raw_tier: Optional[str] = None
    reason = "heuristic"
    if classifier is not None:
        try:
            raw_tier = _parse_classifier_tier(classifier(message, policy.tier_order), policy.tier_order)
            reason = "injected_classifier" if raw_tier else "invalid_classifier_output"
        except Exception as exc:  # noqa: BLE001
            reason = f"classifier_error:{type(exc).__name__}"
        if raw_tier is None:
            return RouteDecision(
                status="classifier_failed",
                config_source=policy.config_source,
                reason=reason,
                diagnostics=policy.diagnostics,
            )
    else:
        auxiliary = config.get("auxiliary") if isinstance(config, dict) else None
        auxiliary = auxiliary if isinstance(auxiliary, dict) else {}
        classifier_cfg = auxiliary.get("routing_classifier")
        classifier_cfg = classifier_cfg if isinstance(classifier_cfg, dict) else {}
        configured_classifier_requested = bool(
            str(classifier_cfg.get("model") or "").strip()
        )
        raw_tier = _configured_classifier(
            message, policy, config, main_runtime=main_runtime
        )
        if raw_tier:
            reason = "configured_classifier"
        elif configured_classifier_requested:
            return RouteDecision(
                status="classifier_failed",
                config_source=policy.config_source,
                reason="configured_classifier_failed",
                diagnostics=policy.diagnostics,
            )
    tier = raw_tier or _heuristic_tier(message, policy)

    if policy.min_tier and policy.tier_order.index(tier) < policy.tier_order.index(policy.min_tier):
        tier = policy.min_tier
        reason += ":min_tier_floor"
    target = policy.tiers.get(tier)
    if target is None:
        return RouteDecision(
            status="no_target",
            tier=tier,
            config_source=policy.config_source,
            reason=reason,
            diagnostics=policy.diagnostics,
        )
    return RouteDecision(
        status="selected",
        tier=tier,
        provider=target.provider,
        model=target.model,
        config_source=policy.config_source,
        reason=reason,
        diagnostics=policy.diagnostics,
    )


def _resolve_target_runtime(target: RouteTarget, _agent: Any, _config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=target.provider, target_model=target.model)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(runtime, dict):
        return {
            "success": False,
            "error": f"runtime provider returned {type(runtime).__name__}, expected mapping",
        }
    model = str(target.model or runtime.get("model") or "").strip()
    provider = str(runtime.get("provider") or target.provider or "").strip()
    # Native runtimes may intentionally return ``None`` for a credentialless
    # transport. Normalize it to the explicit-empty sentinel so delegation
    # cannot reinterpret it as "inherit the parent's provider secret".
    api_key = runtime.get("api_key") or ""
    command = runtime.get("command") or runtime.get("acp_command")
    args = runtime.get("args") or runtime.get("acp_args")
    api_mode = runtime.get("api_mode") or ""
    cold_native = api_mode in {"bedrock_converse", "codex_app_server"}
    if not model or not provider or (not api_key and not command and not cold_native):
        return {"success": False, "error": "runtime provider resolution returned incomplete credentials"}
    resolved = {
        "success": True,
        "model": model,
        "provider": provider,
        "api_key": api_key,
        "base_url": runtime.get("base_url") or "",
        "api_mode": api_mode,
    }
    if command:
        resolved["command"] = command
        resolved["args"] = list(args or [])
    return resolved


def _default_receipt_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
    return home / "logs" / "model-routing.jsonl"


def append_route_receipt(receipt: Dict[str, Any], receipt_path: Optional[Path] = None) -> None:
    path = Path(receipt_path) if receipt_path is not None else _default_receipt_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n"
        with _RECEIPT_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError:
        logger.warning("failed to append smart-routing receipt", exc_info=True)


def _receipt_base(agent: Any, *, scope: str, decision: Optional[RouteDecision] = None) -> Dict[str, Any]:
    selected = None
    if decision is not None and decision.status == "selected":
        selected = {"tier": decision.tier, "provider": decision.provider, "model": decision.model}
    return {
        "schema": "hermes_model_route_receipt_v1",
        "event_type": "route_resolution",
        "created_at": time.time(),
        "scope": scope,
        "session_id": str(getattr(agent, "session_id", "") or ""),
        "config_source": decision.config_source if decision is not None else "none",
        "decision_reason": decision.reason if decision is not None else "",
        "diagnostics": list(decision.diagnostics) if decision is not None else [],
        "selected": selected,
        "actual": {
            "provider": str(getattr(agent, "provider", "") or ""),
            "model": str(getattr(agent, "model", "") or ""),
        },
        "external_side_effects_enabled": False,
    }


_DURABLE_ROUTE_FIELDS = frozenset(
    {"session_id", "provider", "model", "base_url", "api_mode", "tier"}
)
_DURABLE_MODEL_CONFIG_FIELDS = frozenset(
    {
        "max_iterations",
        "reasoning_config",
        "max_tokens",
        "execution_graph_id",
        "prompt_hash",
        "prompt_manifest",
        "service_tier",
        "model",
        "provider",
        "base_url",
        "api_mode",
        "smart_routing_runtime",
        "_delegate_from",
        "_branched_from",
    }
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "cookie",
    "database_url",
    "connection_string",
    "private_key",
)


def _contains_sensitive_material(value: Any) -> bool:
    """Reject credential-bearing structures before durable session persistence."""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_material(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_material(item) for item in value)
    return False


def _safe_base_url(value: str) -> bool:
    if not value:
        return True
    try:
        from urllib.parse import parse_qsl, urlsplit

        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return False
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                return False
    except (TypeError, ValueError):
        return False
    return True


def _canonical_durable_route(
    route: Any, expected_session_id: str
) -> Optional[Dict[str, str]]:
    if not isinstance(route, dict) or set(route) != _DURABLE_ROUTE_FIELDS:
        return None
    if any(not isinstance(value, str) for value in route.values()):
        return None
    provider = route["provider"].strip()
    model = route["model"].strip()
    if route["session_id"] != expected_session_id or not provider or not model:
        return None
    if not _safe_base_url(route["base_url"]):
        return None
    return {
        "session_id": route["session_id"],
        "provider": provider,
        "model": model,
        "base_url": route["base_url"],
        "api_mode": route["api_mode"],
        "tier": route["tier"],
    }


def _clear_durable_route(agent: Any) -> None:
    agent._smart_routing_runtime = None
    model_config = getattr(agent, "_session_init_model_config", None)
    if isinstance(model_config, dict):
        model_config.pop("smart_routing_runtime", None)


def rebind_smart_routing_runtime(
    agent: Any,
    session_id: str,
    *,
    expected_session_id: Optional[str] = None,
) -> bool:
    """Validate and rebind a non-secret durable route to a continuation session."""
    expected = str(
        expected_session_id
        if expected_session_id is not None
        else getattr(agent, "session_id", "") or ""
    )
    route = _canonical_durable_route(
        getattr(agent, "_smart_routing_runtime", None), expected
    )
    if not session_id or route is None:
        _clear_durable_route(agent)
        return False
    rebound = dict(route)
    rebound["session_id"] = str(session_id)
    agent._smart_routing_runtime = rebound
    model_config = getattr(agent, "_session_init_model_config", None)
    if isinstance(model_config, dict):
        model_config["smart_routing_runtime"] = dict(rebound)
    return True


def _canonical_model_config(config: Any) -> Dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    return {
        key: value
        for key, value in config.items()
        if key in _DURABLE_MODEL_CONFIG_FIELDS
        and key != "smart_routing_runtime"
        and not _contains_sensitive_material(value)
        and (key != "base_url" or (isinstance(value, str) and _safe_base_url(value)))
    }


def _persist_runtime_identity(agent: Any, session_id: str) -> None:
    """Keep fresh and pre-created session rows aligned with the active runtime."""
    model_config = _canonical_model_config(
        getattr(agent, "_session_init_model_config", {}) or {}
    )
    route_runtime = dict(getattr(agent, "_smart_routing_runtime", {}) or {})
    durable_route = {
        "session_id": str(session_id),
        "provider": str(getattr(agent, "provider", "") or ""),
        "model": str(getattr(agent, "model", "") or ""),
        "base_url": str(getattr(agent, "base_url", "") or ""),
        "api_mode": str(getattr(agent, "api_mode", "") or ""),
        "tier": str(route_runtime.get("tier") or ""),
    }
    model_config["smart_routing_runtime"] = durable_route
    agent._smart_routing_runtime = dict(durable_route)
    session_db = getattr(agent, "_session_db", None)
    session_db_created = bool(getattr(agent, "_session_db_created", False))
    if session_db is not None and session_db_created:
        try:
            row = session_db.get_session(session_id)
            stored = row.get("model_config") if isinstance(row, dict) else None
            if isinstance(stored, str):
                stored = json.loads(stored)
            if isinstance(stored, dict):
                latest = _canonical_model_config(stored)
                for key, value in model_config.items():
                    latest.setdefault(key, value)
                model_config = latest
        except Exception:
            logger.warning("failed to read durable runtime identity", exc_info=True)
    # The active route always replaces a stale marker from a pre-created row.
    model_config["smart_routing_runtime"] = dict(durable_route)
    model_config.update(
        {
            "model": str(getattr(agent, "model", "") or ""),
            "provider": str(getattr(agent, "provider", "") or ""),
            "base_url": str(getattr(agent, "base_url", "") or ""),
            "api_mode": str(getattr(agent, "api_mode", "") or ""),
        }
    )
    agent._session_init_model_config = model_config
    if session_db is None or not session_db_created:
        return
    try:
        session_db.update_session_meta(
            session_id,
            json.dumps(model_config, ensure_ascii=False, sort_keys=True),
            model=str(getattr(agent, "model", "") or ""),
        )
    except Exception:
        logger.warning("failed to persist smart-routing runtime identity", exc_info=True)


def maybe_route_session(
    agent: Any,
    *,
    user_message: str,
    messages: Sequence[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    classifier: Optional[Callable[[str, Sequence[str]], str]] = None,
    runtime_resolver: Optional[Callable[[RouteTarget, Any, Dict[str, Any]], Dict[str, Any]]] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Apply routing once before a fresh session's first model call."""
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly

            loaded = load_config_readonly()
            config = loaded if isinstance(loaded, dict) else {}
        except Exception:
            config = {}
    else:
        config = config if isinstance(config, dict) else {}
    policy = resolve_routing_policy(config)
    if not policy.enabled:
        return {"status": "disabled"}
    session_id = str(getattr(agent, "session_id", "") or "")
    gate_status = ""
    # Claim the first-turn route atomically. Classification and provider resolution
    # happen outside this short critical section, but no concurrent turn can pay for
    # or apply a second route for the same cache-bearing agent/session.
    with _ROUTE_STATE_LOCK:
        bound_session = str(getattr(agent, "_smart_routing_session_id", "") or "")
        if bound_session != session_id:
            agent._smart_routing_session_id = session_id
            agent._smart_routing_applied = False
            # A runtime selection belongs to one session identity only. `/new`
            # re-arms routing and must not carry intentional-switch state forward.
            agent._smart_routing_runtime = None

        if bool(getattr(agent, "_smart_routing_applied", False)):
            gate_status = "skipped_already_applied"
        # AIAgent is fail-closed by default. User-facing entrypoints explicitly opt
        # default-model sessions in; direct/API constructors and explicit pins do not.
        elif getattr(agent, "_smart_routing_entrypoint_eligible", False) is not True:
            agent._smart_routing_applied = True
            gate_status = "skipped_entrypoint_not_eligible"
        # ``messages`` is the prior conversation history plus the current user
        # message. Any additional record — including a system/tool-only compacted
        # history — proves this is not a pristine cache boundary.
        elif len(messages) != 1 or messages[0].get("role") != "user":
            agent._smart_routing_applied = True
            gate_status = "skipped_resumed_session"
        elif str(getattr(agent, "_smart_routing_user_override_session_id", "") or "") == session_id:
            agent._smart_routing_applied = True
            gate_status = "skipped_explicit_override"
        else:
            # Set before external classification/resolution so failures never cause
            # repeated paid routing attempts within the same session.
            agent._smart_routing_applied = True

    if gate_status == "skipped_already_applied":
        return {"status": gate_status}
    if gate_status:
        receipt = _receipt_base(agent, scope="session")
        receipt.update({"switch_status": "skipped", "status": gate_status, "error": ""})
        append_route_receipt(receipt, receipt_path)
        return {"status": gate_status}
    main_runtime = {
        "provider": str(getattr(agent, "provider", "") or ""),
        "model": str(getattr(agent, "model", "") or ""),
        "base_url": str(getattr(agent, "base_url", "") or ""),
        "api_key": getattr(agent, "api_key", "") or "",
        "api_mode": str(getattr(agent, "api_mode", "") or ""),
    }
    decision = select_route_decision(
        user_message,
        config,
        classifier=classifier,
        main_runtime=main_runtime,
    )
    if decision.status != "selected":
        receipt = _receipt_base(agent, scope="session", decision=decision)
        receipt.update({"switch_status": "skipped", "status": decision.status, "error": ""})
        append_route_receipt(receipt, receipt_path)
        return {"status": decision.status, "decision": asdict(decision)}

    if decision.provider == str(getattr(agent, "provider", "") or "") and decision.model == str(
        getattr(agent, "model", "") or ""
    ):
        agent._smart_routing_runtime = {
            "session_id": session_id,
            "provider": decision.provider,
            "model": decision.model,
            "tier": decision.tier,
        }
        _persist_runtime_identity(agent, session_id)
        receipt = _receipt_base(agent, scope="session", decision=decision)
        receipt.update({"switch_status": "already_active", "status": "already_active", "error": ""})
        append_route_receipt(receipt, receipt_path)
        return {"status": "already_active", "decision": asdict(decision)}

    if getattr(agent, "acp_command", None) or str(
        getattr(agent, "api_mode", "") or ""
    ) in {"bedrock_converse", "codex_app_server"}:
        receipt = _receipt_base(agent, scope="session", decision=decision)
        receipt.update(
            {
                "switch_status": "unsupported",
                "status": "unsupported_hot_switch_runtime",
                "error": "current runtime requires process/native agent reconstruction",
            }
        )
        append_route_receipt(receipt, receipt_path)
        return {
            "status": "unsupported_hot_switch_runtime",
            "decision": asdict(decision),
        }

    resolver = runtime_resolver or _resolve_target_runtime
    resolved = resolver(RouteTarget(decision.provider, decision.model), agent, config)
    if not resolved.get("success"):
        receipt = _receipt_base(agent, scope="session", decision=decision)
        receipt.update(
            {
                "switch_status": "resolution_failed",
                "status": "resolution_failed",
                "error": str(resolved.get("error") or "runtime resolution failed"),
            }
        )
        append_route_receipt(receipt, receipt_path)
        return {"status": "resolution_failed", "decision": asdict(decision)}

    if resolved.get("command") or resolved.get("api_mode") in {
        "bedrock_converse",
        "codex_app_server",
    }:
        receipt = _receipt_base(agent, scope="session", decision=decision)
        receipt.update(
            {
                "switch_status": "unsupported",
                "status": "unsupported_hot_switch_runtime",
                "error": "selected runtime requires process/native agent reconstruction",
            }
        )
        append_route_receipt(receipt, receipt_path)
        return {
            "status": "unsupported_hot_switch_runtime",
            "decision": asdict(decision),
        }

    try:
        agent._smart_routing_switch_in_progress = True
        agent.switch_model(
            new_model=resolved["model"],
            new_provider=resolved["provider"],
            api_key=resolved.get("api_key", ""),
            base_url=resolved.get("base_url", ""),
            api_mode=resolved.get("api_mode", ""),
        )
    except Exception as exc:  # noqa: BLE001
        receipt = _receipt_base(agent, scope="session", decision=decision)
        receipt.update({"switch_status": "failed", "status": "switch_failed", "error": f"{type(exc).__name__}: {exc}"})
        append_route_receipt(receipt, receipt_path)
        return {"status": "switch_failed", "decision": asdict(decision), "error": str(exc)}
    finally:
        agent._smart_routing_switch_in_progress = False

    agent._smart_routing_runtime = {
        "session_id": session_id,
        "provider": str(getattr(agent, "provider", "") or ""),
        "model": str(getattr(agent, "model", "") or ""),
        "tier": decision.tier,
    }
    # Persist the selected non-secret runtime identity. Fresh rows are created
    # later from this dict; pre-created Desktop/Gateway rows are updated here.
    _persist_runtime_identity(agent, session_id)
    receipt = _receipt_base(agent, scope="session", decision=decision)
    receipt.update({"switch_status": "applied", "status": "applied", "error": ""})
    append_route_receipt(receipt, receipt_path)
    return {"status": "applied", "decision": asdict(decision), "receipt": receipt}
