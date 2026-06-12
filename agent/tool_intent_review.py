"""Dry-run intent/risk review contract for high-risk tool calls.

This module is intentionally side-effect-light: it classifies a proposed tool
call and can emit a versioned sidecar for taskpool evidence, but it does not yet
block runtime execution. Runtime interception should be a later tranche after
false-positive review of these sidecars.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "tool_intent_review_v1"
DECISIONS = {"allow", "block", "require_confirmation", "degrade"}
RISK_LEVELS = {"low", "medium", "high", "critical"}

TOOL_INTENT_REVIEW_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Hermes tool intent/risk review dry-run sidecar",
    "type": "object",
    "required": [
        "schema_version",
        "tool_name",
        "risk_level",
        "decision",
        "reason_codes",
        "evidence_refs",
        "cron_mode",
        "degraded_flags",
        "missing_evidence",
    ],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "tool_name": {"type": "string", "minLength": 1},
        "risk_level": {"enum": sorted(RISK_LEVELS)},
        "decision": {"enum": sorted(DECISIONS)},
        "reason_codes": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "cron_mode": {"type": "boolean"},
        "degraded_flags": {"type": "array", "items": {"type": "string"}},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "intent_summary": {"type": "string"},
        "target_surfaces": {"type": "array", "items": {"type": "string"}},
        "created_at": {"type": "string"},
    },
    "additionalProperties": False,
}

_WRITE_TOOLS = {"write_file", "patch", "terminal", "execute_code"}
_BROWSER_TOOLS = {"browser_navigate", "browser_click", "browser_type", "browser_evaluate", "browser_screenshot"}
_READ_ONLY_TOOLS = {"read_file", "search_files", "tool_search", "tool_describe", "browser_snapshot"}
_DESTRUCTIVE_TERMINAL_HINTS = (
    " rm ",
    "rm -",
    "sudo ",
    "chmod ",
    "chown ",
    "git push",
    "curl ",
    "wget ",
    "launchctl ",
    "crontab ",
)


@dataclass(frozen=True)
class ToolIntentReview:
    schema_version: str
    tool_name: str
    risk_level: str
    decision: str
    reason_codes: list[str]
    evidence_refs: list[str]
    cron_mode: bool
    degraded_flags: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    intent_summary: str = ""
    target_surfaces: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["created_at"]:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True, indent=2)


def _arg_text(args: Mapping[str, Any]) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True).lower()
    except TypeError:
        return str(args).lower()


def _target_surfaces(tool_name: str, args: Mapping[str, Any]) -> list[str]:
    surfaces: set[str] = set()
    text = _arg_text(args)
    if tool_name in _BROWSER_TOOLS or tool_name.startswith("browser_"):
        surfaces.add("browser")
    if tool_name in {"terminal", "execute_code"}:
        surfaces.add("terminal")
    if tool_name in {"write_file", "patch"} or "path" in args:
        surfaces.add("filesystem")
    if "/.hermes/profiles/" in text:
        surfaces.add("cross_profile")
    if ".hermes" in text or "brain_task_orchestrator" in text:
        surfaces.add("hermes_control_plane")
    return sorted(surfaces)


def _is_structured_evidence(ref: str) -> bool:
    return ref.startswith(("command:", "artifact:", "proof:", "file:", "test:", "dry-run:"))


def review_tool_intent(
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    *,
    intent_summary: str = "",
    evidence_refs: list[str] | None = None,
    cron_mode: bool = False,
) -> ToolIntentReview:
    """Classify a proposed tool call for dry-run evidence.

    High-risk write/terminal actions without structured evidence never return
    ``allow``. In cron mode they degrade instead of prompting for confirmation,
    preserving unattended fail-safe semantics for later runtime integration.
    """

    safe_args: Mapping[str, Any] = args or {}
    refs = list(evidence_refs or [])
    reason_codes: list[str] = []
    degraded_flags: list[str] = []
    missing_evidence: list[str] = []
    surfaces = _target_surfaces(tool_name, safe_args)
    text = _arg_text(safe_args)

    risk_level = "medium"
    if tool_name in _READ_ONLY_TOOLS:
        risk_level = "low"
        reason_codes.append("read_only_or_observational")
    if (tool_name in _BROWSER_TOOLS or tool_name.startswith("browser_")) and tool_name not in _READ_ONLY_TOOLS:
        risk_level = max(risk_level, "medium", key=["low", "medium", "high", "critical"].index)
        reason_codes.append("browser_automation_surface")
    if tool_name in _WRITE_TOOLS:
        risk_level = "high"
        reason_codes.append("write_or_execution_surface")
    if tool_name == "terminal" and any(hint in f" {text} " for hint in _DESTRUCTIVE_TERMINAL_HINTS):
        risk_level = "critical"
        reason_codes.append("destructive_or_external_terminal_hint")
    if "cross_profile" in surfaces:
        risk_level = "critical"
        reason_codes.append("cross_profile_write_surface")

    structured_refs = [ref for ref in refs if _is_structured_evidence(ref)]
    if risk_level in {"high", "critical"} and not structured_refs:
        missing_evidence.append("structured_pretool_evidence_ref")
        reason_codes.append("missing_structured_evidence")

    if risk_level == "low":
        decision = "allow"
    elif missing_evidence and cron_mode:
        decision = "degrade"
        degraded_flags.append("cron_no_user_confirmation_available")
    elif missing_evidence:
        decision = "require_confirmation"
    elif risk_level == "critical" and cron_mode:
        decision = "block"
        reason_codes.append("cron_critical_risk_fail_closed")
    else:
        decision = "allow"

    return ToolIntentReview(
        schema_version=SCHEMA_VERSION,
        tool_name=tool_name,
        risk_level=risk_level,
        decision=decision,
        reason_codes=sorted(set(reason_codes)),
        evidence_refs=refs,
        cron_mode=cron_mode,
        degraded_flags=sorted(set(degraded_flags)),
        missing_evidence=missing_evidence,
        intent_summary=intent_summary,
        target_surfaces=surfaces,
    )


def write_review_sidecar(review: ToolIntentReview, path: str | Path) -> Path:
    """Write a deterministic JSON sidecar and return its path."""

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(review.to_json() + "\n", encoding="utf-8")
    return target
