"""Abstraction-governance runtime gates for durable Hermes writes.

This module deliberately implements a small deterministic gate.  It does not try
an LLM review in the hot path.  Its job is to catch high-confidence violations of
Hermes durable-state boundaries before they become sticky prompt context.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class AbstractionGateDecision:
    ok: bool
    reason: str
    recommended_landing_zone: str = ""
    required_skill: str = "abstraction-governance"
    message: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "recommended_landing_zone": self.recommended_landing_zone,
            "required_skill": self.required_skill,
            "message": self.message,
        }


_TASK_PROGRESS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bPR\s*#?\d+\b.*\b(merged|submitted|done|fixed|closed|opened)\b",
        r"\b(issue|ticket|task)\s*#?\d+\b.*\b(done|closed|fixed|resolved|submitted)\b",
        r"\b(commit|sha)\s+[0-9a-f]{7,40}\b",
        r"\b(phase|round|step)\s*\d+\b.*\b(done|complete|completed|finished)\b",
        r"\b(fixed|resolved|submitted|merged|closed|completed)\b.*\b(this session|today|current task|this task)\b",
        r"(本轮|本次|这次|今天).{0,24}(完成|修完|已修|提交|合并|关闭)",
        r"(PR|issue|任务).{0,20}(已完成|已修复|已提交|已合并|已关闭)",
    )
)

_PROCEDURE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(SOP|workflow|runbook|checklist|procedure)\s*:",
        r"\b(run|execute)\s+[^\n]{0,80}\b(pytest|npm|pnpm|git|docker|curl|mvn)\b",
        r"\b(step\s*1|1\.|first,)\b.{0,80}\b(step\s*2|2\.|then)\b",
        r"(流程|步骤|检查清单|门禁|SOP)[:：]",
    )
)

_STABLE_MEMORY_ALLOW_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^User\s+(prefers|likes|dislikes|expects|requires)\b",
        r"^用户(偏好|要求|讨厌|喜欢|希望|强烈偏好)",
        r"^Project\s+[-\w.]+\s+(uses|is|has|requires)\b",
        r"^项目\s*[-\w.\u4e00-\u9fff]+\s*(使用|采用|要求|是)",
    )
)

_PROJECT_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bfeat/[A-Za-z0-9_.-]+\b",
        r"\b[A-Za-z0-9_.-]+/(app|admin|backend|frontend|service|worker)[A-Za-z0-9_.-]*\b",
        r"/Users/[A-Za-z0-9_.-]+/[^\n` ]+",
        r"\bPR\s*#?\d+\b",
        r"\bissue\s*#?\d+\b",
    )
)

_SCOPE_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bScope\s*:\s*(project|repo|domain|user|global|task|session)\b",
        r"\bAnti-conditions?\s*:",
        r"\bApplicability\s*:",
        r"\bProject fact\s*:",
        r"适用范围[:：]",
        r"反条件[:：]",
        r"仅适用于",
    )
)


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def should_apply_abstraction_gate() -> bool:
    """Return whether abstraction-governance runtime gates are enabled."""

    return _truthy_env("HERMES_ABSTRACTION_GOVERNANCE_GATE", True)


def _matches_any(content: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(content) for pattern in patterns)


def _has_scope_marker(content: str) -> bool:
    return _matches_any(content, _SCOPE_MARKER_PATTERNS)


def _candidate_text(*parts: str | None) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def review_skill_mutation(
    *,
    action: str,
    name: str,
    content: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
) -> AbstractionGateDecision:
    """Review a skill create/edit/patch/write_file mutation before persisting.

    Skills are the landing zone for reusable procedures, so this gate does not
    block procedural language.  It blocks two high-confidence boundary errors:
    session/task progress being preserved as a skill, and project-specific
    entity rules being promoted without explicit scope/anti-condition markers.
    """

    if not should_apply_abstraction_gate():
        return AbstractionGateDecision(ok=True, reason="disabled_or_empty")

    text = _candidate_text(content, new_string)
    if not text:
        return AbstractionGateDecision(ok=True, reason="empty_deferred_to_skill_tool")

    if _matches_any(text, _TASK_PROGRESS_PATTERNS):
        return AbstractionGateDecision(
            ok=False,
            reason="task_progress_not_skill",
            recommended_landing_zone="session_log_or_workpad",
            message=(
                "Skills may not store PR/issue/commit status, session outcomes, "
                "or active task progress. Keep those facts in the transcript, "
                "Workpad, incident log, or session_search."
            ),
        )

    if _matches_any(text, _PROJECT_ENTITY_PATTERNS) and not _has_scope_marker(text):
        return AbstractionGateDecision(
            ok=False,
            reason="context_coupled_skill",
            recommended_landing_zone="project_profile_or_scoped_reference",
            message=(
                "This skill mutation contains project-specific entities but no "
                "explicit scope/applicability/anti-condition marker. Strip entities "
                "for global skills, or land the fact in a project profile/scoped "
                "reference with clear boundaries."
            ),
        )

    return AbstractionGateDecision(ok=True, reason="accepted")


def review_durable_write(
    *,
    kind: str,
    target: str,
    content: str | None = None,
    action: str = "",
    old_string: str | None = None,
    new_string: str | None = None,
) -> AbstractionGateDecision:
    """Route durable-write reviews by governance surface.

    This is intentionally side-effect free so tools, hooks, and future profile / ADR /
    experience-promotion paths can share the same deterministic boundary checks.
    Unknown kinds are accepted to avoid surprising unrelated write paths.
    """

    normalized = (kind or "").strip().lower()
    if normalized == "memory":
        return review_memory_write(target=target, content=content)
    if normalized == "skill":
        return review_skill_mutation(
            action=action,
            name=target,
            content=content,
            old_string=old_string,
            new_string=new_string,
        )
    if normalized in {"project_profile", "adr", "experience"}:
        text = _candidate_text(content, new_string)
        if not should_apply_abstraction_gate() or not text:
            return AbstractionGateDecision(ok=True, reason="disabled_or_empty")
        if _matches_any(text, _TASK_PROGRESS_PATTERNS):
            return AbstractionGateDecision(
                ok=False,
                reason=f"task_progress_not_{normalized}",
                recommended_landing_zone="session_log_or_workpad",
                message=(
                    "Durable governance/profile/ADR/experience writes may not store "
                    "active task progress, PR/issue/commit status, or session outcomes. "
                    "Keep those facts in the transcript, Workpad, proof log, or session_search."
                ),
            )
        if normalized in {"adr", "experience"} and _matches_any(text, _PROJECT_ENTITY_PATTERNS) and not _has_scope_marker(text):
            return AbstractionGateDecision(
                ok=False,
                reason=f"context_coupled_{normalized}",
                recommended_landing_zone="project_profile_or_scoped_reference",
                message=(
                    "This durable write contains project-specific entities but no explicit "
                    "scope/applicability/anti-condition marker. Add boundaries or land the "
                    "fact in the scoped project profile/reference instead."
                ),
            )
        return AbstractionGateDecision(ok=True, reason="accepted")
    return AbstractionGateDecision(ok=True, reason="unsupported_kind_deferred")


def review_memory_write(*, target: str, content: str | None) -> AbstractionGateDecision:
    """Review a proposed memory add/replace before it is persisted.

    The gate blocks only high-confidence boundary mistakes:
    - task/session progress belongs in transcript, Workpad, or session_search;
    - reusable procedures belong in skills or experience-ratchet proposals.

    Stable declarative user preferences and scoped project facts remain allowed.
    """

    if not should_apply_abstraction_gate() or not content:
        return AbstractionGateDecision(ok=True, reason="disabled_or_empty")

    text = content.strip()
    if not text:
        return AbstractionGateDecision(ok=True, reason="empty_deferred_to_memory_tool")

    if _matches_any(text, _STABLE_MEMORY_ALLOW_PATTERNS):
        return AbstractionGateDecision(ok=True, reason="accepted")

    if _matches_any(text, _TASK_PROGRESS_PATTERNS):
        return AbstractionGateDecision(
            ok=False,
            reason="task_progress_not_memory",
            recommended_landing_zone="session_log_or_workpad",
            message=(
                "Durable memory may not store active task progress, PR/issue/commit "
                "status, or session outcomes. Keep this in the transcript, Workpad, "
                "incident log, or use session_search later."
            ),
        )

    if target == "memory" and _matches_any(text, _PROCEDURE_PATTERNS):
        return AbstractionGateDecision(
            ok=False,
            reason="procedure_belongs_in_skill",
            recommended_landing_zone="skill_or_experience_atom",
            message=(
                "Procedures, workflows, and reusable checklists should be written as "
                "skills or captured as experience-ratchet atoms before promotion, "
                "not injected into always-on memory."
            ),
        )

    return AbstractionGateDecision(ok=True, reason="accepted")
