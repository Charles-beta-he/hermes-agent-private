"""Deterministic Hermes session / cron / tool-call risk audit primitives.

This module is intentionally pure and runtime-passive.  It classifies already
captured run records into evidence-linked risk summaries; it does not inspect
secrets, change cron/provider routing, or call an LLM.  Missing evidence is a
hard classification fact: the output records ``insufficient_evidence`` instead
of inventing a low-risk conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any, Iterable

SCHEMA_VERSION = "session_risk_audit_v1"

_RISK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "config_change",
        (
            "config.yaml",
            "jobs.json",
            "fallback_providers",
            "provider routing",
            "model-router",
            "save_config_value",
        ),
    ),
    (
        "filesystem_write",
        (
            "write_file",
            "patch",
            "apply_patch",
            "transition",
            "git commit",
            "dashboard --write",
            "--write",
        ),
    ),
    (
        "credential_touch",
        (
            "api_key",
            "token",
            "secret",
            "credential",
            ".env",
            "auth.json",
            "oauth",
        ),
    ),
    (
        "external_network",
        (
            "curl ",
            "http://",
            "https://",
            "browser",
            "web_search",
            "requests.",
            "httpx",
        ),
    ),
    (
        "mcp_or_skill_use",
        (
            "mcp",
            "skill_view",
            "skill_manage",
            "tool_call",
            "delegate_task",
        ),
    ),
    (
        "provider_cost_signal",
        (
            "rate_limit",
            "429",
            "quota",
            "token",
            "provider",
            "model",
            "cost",
        ),
    ),
)

_SEVERITY = {
    "credential_touch": "high",
    "config_change": "high",
    "filesystem_write": "medium",
    "external_network": "medium",
    "mcp_or_skill_use": "medium",
    "provider_cost_signal": "medium",
}

_REQUIRED_EVIDENCE_FIELDS = ("evidence_bundle", "evidence_refs", "log_path", "artifact", "path")


@dataclass(frozen=True)
class AuditFinding:
    """One deterministic risk classification and its local evidence pointers."""

    risk: str
    severity: str
    evidence_bundle: list[str]
    insufficient_evidence: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "severity": self.severity,
            "evidence_bundle": list(self.evidence_bundle),
            "insufficient_evidence": self.insufficient_evidence,
            "reason": self.reason,
        }


def _flatten(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _flatten(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _flatten(item)
        return
    yield str(value)


def _coerce_evidence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def evidence_bundle_for(record: dict[str, Any]) -> list[str]:
    """Return explicit evidence pointers present in a run record."""

    evidence: list[str] = []
    for field in _REQUIRED_EVIDENCE_FIELDS:
        evidence.extend(_coerce_evidence(record.get(field)))
    # Keep stable order while deduplicating repeated frontmatter/body refs.
    seen: set[str] = set()
    result: list[str] = []
    for item in evidence:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def classify_session_risk(record: dict[str, Any]) -> dict[str, Any]:
    """Classify one existing Hermes run record into a schema-stable audit JSON.

    The classifier is deterministic and evidence-first.  It detects risk labels
    only from record text/metadata and never downgrades a run to safe when local
    evidence pointers are absent.
    """

    haystack = "\n".join(_flatten(record)).lower()
    evidence = evidence_bundle_for(record)
    findings: list[AuditFinding] = []

    for risk, needles in _RISK_RULES:
        matched = sorted({needle for needle in needles if needle.lower() in haystack})
        if not matched:
            continue
        findings.append(
            AuditFinding(
                risk=risk,
                severity=_SEVERITY[risk],
                evidence_bundle=evidence,
                insufficient_evidence=not evidence,
                reason="matched:" + ",".join(matched),
            )
        )

    if not findings and not evidence:
        findings.append(
            AuditFinding(
                risk="insufficient_evidence",
                severity="high",
                evidence_bundle=[],
                insufficient_evidence=True,
                reason="no explicit evidence_bundle/evidence_refs/log_path/artifact/path present",
            )
        )

    if findings:
        if any(item.insufficient_evidence or item.severity == "high" for item in findings):
            max_severity = "high"
        else:
            max_severity = "medium"
    else:
        max_severity = "low"

    return {
        "schema": SCHEMA_VERSION,
        "run_id": record.get("run_id") or record.get("id") or "unknown",
        "source": record.get("source") or record.get("kind") or "unknown",
        "max_severity": max_severity,
        "insufficient_evidence": any(item.insufficient_evidence for item in findings),
        "findings": [item.to_dict() for item in findings],
        "policy": {
            "deterministic_only": True,
            "llm_may_explain_but_not_classify": True,
            "fail_closed_on_missing_evidence": True,
        },
    }


def classify_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classify_session_risk(record) for record in records]


def _load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        records = payload.get("records", [payload])
    else:
        records = payload
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("input must be a JSON object or list of JSON objects")
    return records


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Classify existing Hermes run records into session-risk-audit JSON")
    parser.add_argument("input", type=Path, help="JSON object/list with read-only run records")
    parser.add_argument("--out", type=Path, help="Write audit JSON to this path")
    args = parser.parse_args(argv)

    output = {"schema": SCHEMA_VERSION, "records": classify_records(_load_records(args.input))}
    text = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
