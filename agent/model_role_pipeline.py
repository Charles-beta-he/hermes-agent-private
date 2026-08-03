"""Pure, authority-negative model-role routing and failover projections.

This module separates the control plane, specialist executors, independent
reviewers, and objective evidence into distinct failure domains.  It never
invokes a provider and never mutates the session/global fallback chain.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence


INFRASTRUCTURE_FAILURES = frozenset(
    {
        "authentication_failed",
        "provider_rate_limited",
        "provider_quota_exhausted",
        "provider_connection_failed",
        "provider_timeout",
        "provider_crashed",
        "mcp_initialization_failed",
        "empty_output",
        "unparseable_output",
    }
)


def _target(provider: str, model: str) -> dict[str, str]:
    return {"provider": provider, "model": model}


def _coding_agent_target(
    provider: str,
    model: str,
    command: str,
) -> dict[str, str]:
    return {
        "provider": provider,
        "model": model,
        "execution_engine": "coding_agent_cli",
        "command": command,
    }


def default_model_role_policy() -> dict[str, Any]:
    """Return a fresh frozen-v1 default policy for role-isolated routing."""
    return {
        "schema": "hermes_model_role_policy_v1",
        "control_plane": {
            "chain": [
                _target("openai-codex", "gpt-5.6-sol"),
                _target("anthropic", "claude-sonnet-4.6"),
                _target("google-gemini", "gemini-3.1-pro-preview"),
            ]
        },
        "surfaces": {
            "code_correctness": {
                "executors": [
                    _coding_agent_target(
                        "anthropic",
                        "claude-sonnet-4-6",
                        "claude",
                    ),
                    _target("openai-codex", "gpt-5.6-sol"),
                ],
                "reviewers": [
                    _target("openai-codex", "gpt-5.6-sol"),
                    _target("deepseek", "deepseek-v4-pro"),
                    _target("xai", "grok-4.5"),
                ],
                "required_evidence": ["test_output", "diff_readback"],
            },
            "ui_visual": {
                "executors": [
                    _target("google-gemini", "gemini-3.1-pro-preview"),
                    _target("google-gemini", "gemini-3.1-flash"),
                    _target("anthropic", "claude-sonnet-4.6"),
                ],
                "reviewers": [
                    _target("anthropic", "claude-sonnet-4.6"),
                    _target("openai-codex", "gpt-5.6-sol"),
                ],
                "required_evidence": ["browser_readback", "screenshot_matrix"],
            },
            "security_logic": {
                "executors": [
                    _target("deepseek", "deepseek-v4-pro"),
                    _target("anthropic", "claude-sonnet-4.6"),
                    _target("xai", "grok-4.5"),
                ],
                "reviewers": [
                    _target("openai-codex", "gpt-5.6-sol"),
                    _target("anthropic", "claude-sonnet-4.6"),
                    _target("xai", "grok-4.5"),
                ],
                "required_evidence": ["hostile_probe", "test_output"],
            },
            "triage": {
                "executors": [
                    _target("xiaomi", "mimo-v2.5-pro"),
                    _target("openai-codex", "gpt-5.6-sol"),
                ],
                "reviewers": [
                    _target("openai-codex", "gpt-5.6-sol"),
                    _target("deepseek", "deepseek-v4-pro"),
                ],
                "required_evidence": ["source_readback"],
            },
            "general": {
                "executors": [_target("openai-codex", "gpt-5.6-sol")],
                "reviewers": [
                    _target("anthropic", "claude-sonnet-4.6"),
                    _target("deepseek", "deepseek-v4-pro"),
                ],
                "required_evidence": [],
            },
        },
        "review_policy": {
            "forbid_executor_self_review": True,
            "require_provider_independence": True,
            "infrastructure_failure_is_no_verdict": True,
        },
        "authority": {
            "may_invoke_models": False,
            "may_mutate_global_model_config": False,
            "may_execute_tools": False,
            "may_mark_done": False,
        },
    }


def infer_surfaces(text: str | None) -> list[str]:
    lowered = (text or "").lower()
    surfaces: list[str] = []
    if any(term in lowered for term in ("代码", "实现", "测试", "pytest", "build", "code", "commit", "review")):
        surfaces.append("code_correctness")
    if any(term in lowered for term in ("ui", "视觉", "界面", "截图", "uimax", "a+")):
        surfaces.append("ui_visual")
    if any(term in lowered for term in ("安全", "逻辑", "security", "漏洞", "threat")):
        surfaces.append("security_logic")
    if any(term in lowered for term in ("分类", "分诊", "triage", "快速判断")):
        surfaces.append("triage")
    return surfaces or ["general"]


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    body = deepcopy(dict(value))
    body.pop("route_digest", None)
    body.pop("transition_receipt", None)
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _route(chain: list[dict[str, str]], index: int = 0) -> dict[str, Any]:
    selected = deepcopy(chain[index]) if index < len(chain) else None
    return {
        "chain": deepcopy(chain),
        "selected": selected,
        "selected_index": index if selected is not None else None,
        "status": "ready" if selected is not None else "blocked_no_candidate",
    }


def _same_target(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return left.get("provider") == right.get("provider") and left.get("model") == right.get("model")


def _target_selection_changed(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> bool:
    if previous is None or current is None:
        return previous is not current
    return not _same_target(previous, current)


def _select_independent_reviewer(
    chain: Sequence[Mapping[str, Any]],
    executor: Mapping[str, Any] | None,
    *,
    start_index: int = 0,
    forbid_executor_self_review: bool = True,
    require_provider_independence: bool = True,
) -> tuple[dict[str, Any] | None, int | None]:
    for index in range(max(0, start_index), len(chain)):
        candidate = chain[index]
        if forbid_executor_self_review and _same_target(candidate, executor):
            continue
        if (
            require_provider_independence
            and isinstance(executor, Mapping)
            and candidate.get("provider") == executor.get("provider")
        ):
            continue
        return deepcopy(dict(candidate)), index
    return None, None


def _validate_target_chain(value: Any, *, path: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty list")
    seen: set[tuple[str, str]] = set()
    for index, target in enumerate(value):
        if not isinstance(target, Mapping):
            raise ValueError(f"{path}[{index}] must be an object")
        provider = str(target.get("provider") or "").strip()
        model = str(target.get("model") or "").strip()
        if not provider:
            raise ValueError(f"{path}[{index}].provider is required")
        if not model:
            raise ValueError(f"{path}[{index}].model is required")
        identity = (provider, model)
        if identity in seen:
            raise ValueError(f"{path}[{index}] duplicates earlier target {identity!r}")
        seen.add(identity)


def _reject_unknown_keys(
    value: Mapping[str, Any], *, allowed: set[str], path: str
) -> None:
    unknown = sorted(str(key) for key in value.keys() if key not in allowed)
    if unknown:
        raise ValueError(f"{path} contains unknown keys: {unknown}")


def _validate_policy(policy: Mapping[str, Any]) -> None:
    _reject_unknown_keys(
        policy,
        allowed={"schema", "control_plane", "surfaces", "review_policy", "authority"},
        path="policy",
    )
    control = policy.get("control_plane")
    if not isinstance(control, Mapping):
        raise ValueError("control_plane must be an object")
    _reject_unknown_keys(control, allowed={"chain"}, path="control_plane")
    _validate_target_chain(control.get("chain"), path="control_plane.chain")

    surfaces = policy.get("surfaces")
    if not isinstance(surfaces, Mapping) or not surfaces:
        raise ValueError("surfaces must be a non-empty object")
    for surface, config in surfaces.items():
        path = f"surfaces.{surface}"
        if not isinstance(config, Mapping):
            raise ValueError(f"{path} must be an object")
        _reject_unknown_keys(
            config,
            allowed={"executors", "reviewers", "required_evidence"},
            path=path,
        )
        _validate_target_chain(config.get("executors"), path=f"{path}.executors")
        _validate_target_chain(config.get("reviewers"), path=f"{path}.reviewers")
        if not isinstance(config.get("required_evidence", []), list):
            raise ValueError(f"{path}.required_evidence must be a list")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in config.get("required_evidence", [])
        ):
            raise ValueError(f"{path}.required_evidence items must be non-empty strings")

    review_policy = policy.get("review_policy")
    if not isinstance(review_policy, Mapping):
        raise ValueError("review_policy must be an object")
    _reject_unknown_keys(
        review_policy,
        allowed={
            "forbid_executor_self_review",
            "require_provider_independence",
            "infrastructure_failure_is_no_verdict",
        },
        path="review_policy",
    )
    for key in (
        "forbid_executor_self_review",
        "require_provider_independence",
        "infrastructure_failure_is_no_verdict",
    ):
        if review_policy.get(key) is not True:
            raise ValueError(f"review_policy.{key} must be true in v1")

    authority = policy.get("authority")
    if not isinstance(authority, Mapping):
        raise ValueError("authority must be an object")
    _reject_unknown_keys(
        authority,
        allowed={
            "may_invoke_models",
            "may_mutate_global_model_config",
            "may_execute_tools",
            "may_mark_done",
        },
        path="authority",
    )
    for key in (
        "may_invoke_models",
        "may_mutate_global_model_config",
        "may_execute_tools",
        "may_mark_done",
    ):
        if authority.get(key) is not False:
            raise ValueError(f"authority.{key} must be false in v1")


class UnifiedModelRolePipeline:
    """Compile and transition role-isolated routing projections."""

    def __init__(self, policy: Mapping[str, Any] | None = None):
        self.policy = deepcopy(dict(policy or default_model_role_policy()))
        if self.policy.get("schema") != "hermes_model_role_policy_v1":
            raise ValueError("unsupported model role policy schema")
        _validate_policy(self.policy)

    def compile_turn(
        self,
        *,
        input_id: str,
        source: str,
        surfaces: Iterable[str],
        control_model: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_surfaces = _ordered_unique(str(item) for item in surfaces if str(item))
        if not normalized_surfaces:
            normalized_surfaces = ["general"]
        surface_policy = self.policy.get("surfaces") or {}
        unknown = [item for item in normalized_surfaces if item not in surface_policy]
        if unknown:
            raise ValueError(f"unknown model capability surfaces: {unknown}")

        configured_control_chain = deepcopy(self.policy["control_plane"]["chain"])
        control_chain = configured_control_chain
        if isinstance(control_model, Mapping) and control_model.get("provider") and control_model.get("model"):
            current = {
                "provider": str(control_model["provider"]),
                "model": str(control_model["model"]),
            }
            control_chain = [current] + [item for item in configured_control_chain if not _same_target(item, current)]

        executors: dict[str, Any] = {}
        reviewers: dict[str, Any] = {}
        required_evidence: list[str] = []
        forbid_executor_self_review = bool(
            (self.policy.get("review_policy") or {}).get("forbid_executor_self_review", True)
        )
        require_provider_independence = bool(
            (self.policy.get("review_policy") or {}).get("require_provider_independence", True)
        )
        for surface in normalized_surfaces:
            cfg = surface_policy[surface]
            executor_route = _route(cfg.get("executors") or [])
            executors[surface] = executor_route
            reviewer_chain = deepcopy(cfg.get("reviewers") or [])
            reviewer, reviewer_index = _select_independent_reviewer(
                reviewer_chain,
                executor_route.get("selected"),
                forbid_executor_self_review=forbid_executor_self_review,
                require_provider_independence=require_provider_independence,
            )
            reviewers[surface] = {
                "chain": reviewer_chain,
                "selected": reviewer,
                "selected_index": reviewer_index,
                "status": "ready" if reviewer is not None else "blocked_no_independent_reviewer",
                "independent_from_executor": reviewer is not None,
            }
            required_evidence.extend(str(item) for item in cfg.get("required_evidence") or [])

        result: dict[str, Any] = {
            "schema": "hermes_model_role_route_v1",
            "input_id": str(input_id),
            "source": str(source),
            "surfaces": normalized_surfaces,
            "control_plane": _route(control_chain),
            "executors": executors,
            "reviewers": reviewers,
            "evidence": {
                "status": "missing" if required_evidence else "not_required",
                "required": _ordered_unique(required_evidence),
                "model_switch_can_satisfy": False,
            },
            "failure_domains": ["control_plane", "executor", "reviewer", "evidence"],
            "authority": deepcopy(self.policy.get("authority") or {}),
        }
        result["route_digest"] = _canonical_digest(result)
        return result

    def apply_failure(
        self,
        route: Mapping[str, Any],
        *,
        role: str,
        surface: str,
        failure: Mapping[str, Any],
    ) -> dict[str, Any]:
        if route.get("schema") != "hermes_model_role_route_v1":
            raise ValueError("unsupported model role route schema")
        opening = deepcopy(dict(route))
        opening.pop("transition_receipt", None)
        opening_digest = _canonical_digest(opening)
        if route.get("route_digest") != opening_digest:
            raise ValueError("model role route digest mismatch")

        successor = deepcopy(opening)
        category = str(failure.get("category") or "")
        code = str(failure.get("code") or "")
        infrastructure = category == "infrastructure" and code in INFRASTRUCTURE_FAILURES
        changed: list[str] = []
        outcome = "blocked_unclassified_failure"

        if role == "evidence":
            if successor["evidence"].get("status") == "failed":
                outcome = "already_exhausted"
            else:
                successor["evidence"]["status"] = "failed"
                successor["evidence"]["failure_code"] = code
                outcome = "evidence_blocked"
        elif role == "executor":
            executor_route = successor.get("executors", {}).get(surface)
            reviewer_route = successor.get("reviewers", {}).get(surface)
            if not isinstance(executor_route, dict):
                raise ValueError(f"unknown executor surface: {surface}")
            if not isinstance(reviewer_route, dict):
                raise ValueError(f"unknown reviewer surface: {surface}")
            if executor_route.get("status") in {
                "blocked_failover_exhausted",
                "blocked_no_candidate",
            }:
                outcome = "already_exhausted"
            elif not infrastructure:
                outcome = "return_to_orchestrator"
            else:
                next_index = int(executor_route.get("selected_index") or 0) + 1
                chain = executor_route.get("chain") or []
                if next_index >= len(chain):
                    executor_route["selected"] = None
                    executor_route["selected_index"] = None
                    executor_route["status"] = "blocked_failover_exhausted"
                    outcome = "failover_exhausted"
                else:
                    executor_route["selected_index"] = next_index
                    executor_route["selected"] = deepcopy(chain[next_index])
                    executor_route["status"] = "recovered"
                    changed.append(f"executor:{surface}")
                    reviewer_terminal = (
                        reviewer_route.get("status") == "no_verdict_failover_exhausted"
                    )
                    current_reviewer_index = reviewer_route.get("selected_index")
                    reviewer_start_index = (
                        int(current_reviewer_index)
                        if isinstance(current_reviewer_index, int)
                        else 0
                    )
                    if reviewer_terminal:
                        reviewer = reviewer_route.get("selected")
                        reviewer_index = current_reviewer_index
                    else:
                        reviewer, reviewer_index = _select_independent_reviewer(
                            reviewer_route.get("chain") or [],
                            executor_route["selected"],
                            start_index=reviewer_start_index,
                            forbid_executor_self_review=bool(
                                (self.policy.get("review_policy") or {}).get(
                                    "forbid_executor_self_review", True
                                )
                            ),
                            require_provider_independence=bool(
                                (self.policy.get("review_policy") or {}).get(
                                    "require_provider_independence", True
                                )
                            ),
                        )
                    if _target_selection_changed(
                        reviewer_route.get("selected"), reviewer
                    ):
                        reviewer_route["selected"] = reviewer
                        reviewer_route["selected_index"] = reviewer_index
                        reviewer_route["status"] = (
                            "recovered"
                            if reviewer is not None
                            else "blocked_no_independent_reviewer"
                        )
                        reviewer_route["independent_from_executor"] = reviewer is not None
                        changed.append(f"reviewer:{surface}")
                    outcome = "executor_failed_over"
        elif role == "reviewer":
            reviewer_route = successor.get("reviewers", {}).get(surface)
            executor_route = successor.get("executors", {}).get(surface)
            if not isinstance(reviewer_route, dict) or not isinstance(executor_route, dict):
                raise ValueError(f"unknown reviewer surface: {surface}")
            if reviewer_route.get("status") in {
                "blocked_no_independent_reviewer",
                "no_verdict_failover_exhausted",
            }:
                outcome = "already_exhausted"
            elif not infrastructure:
                outcome = "reviewer_returned_verdict"
            else:
                current_index = reviewer_route.get("selected_index")
                start_index = int(current_index) + 1 if isinstance(current_index, int) else 0
                reviewer, reviewer_index = _select_independent_reviewer(
                    reviewer_route.get("chain") or [],
                    executor_route.get("selected"),
                    start_index=start_index,
                    forbid_executor_self_review=bool(
                        (self.policy.get("review_policy") or {}).get(
                            "forbid_executor_self_review", True
                        )
                    ),
                    require_provider_independence=bool(
                        (self.policy.get("review_policy") or {}).get(
                            "require_provider_independence", True
                        )
                    ),
                )
                reviewer_route["selected"] = reviewer
                reviewer_route["selected_index"] = reviewer_index
                reviewer_route["status"] = (
                    "recovered" if reviewer is not None else "no_verdict_failover_exhausted"
                )
                reviewer_route["independent_from_executor"] = reviewer is not None
                if reviewer is not None:
                    changed.append(f"reviewer:{surface}")
                    outcome = "reviewer_failed_over"
                else:
                    outcome = "no_verdict_failover_exhausted"
        elif role == "control_plane":
            control_route = successor["control_plane"]
            if control_route.get("status") in {
                "blocked_failover_exhausted",
                "blocked_no_candidate",
            }:
                outcome = "already_exhausted"
            elif not infrastructure:
                outcome = "control_returned_to_orchestrator"
            else:
                next_index = int(control_route.get("selected_index") or 0) + 1
                chain = control_route.get("chain") or []
                if next_index >= len(chain):
                    control_route["selected"] = None
                    control_route["selected_index"] = None
                    control_route["status"] = "blocked_failover_exhausted"
                    outcome = "failover_exhausted"
                else:
                    control_route["selected_index"] = next_index
                    control_route["selected"] = deepcopy(chain[next_index])
                    control_route["status"] = "recovered"
                    changed.append("control_plane")
                    outcome = "control_failed_over"
        else:
            raise ValueError(f"unknown failure role: {role}")

        successor["route_digest"] = _canonical_digest(successor)
        successor["transition_receipt"] = {
            "schema": "hermes_model_role_failover_receipt_v1",
            "from_digest": opening_digest,
            "to_digest": successor["route_digest"],
            "role": role,
            "surface": surface,
            "failure": {"category": category, "code": code},
            "outcome": outcome,
            "changed_roles": changed,
            "global_model_config_mutated": False,
            "authority_expanded": False,
        }
        return successor


__all__ = [
    "INFRASTRUCTURE_FAILURES",
    "UnifiedModelRolePipeline",
    "default_model_role_policy",
    "infer_surfaces",
]
