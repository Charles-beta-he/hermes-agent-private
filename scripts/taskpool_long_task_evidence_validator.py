#!/usr/bin/env python3
"""Validate taskpool long-task / high-tool-call evidence claims.

This is a non-invasive helper: it does not mutate task notes or provider routing.
It classifies candidate JSON and fails only candidates that explicitly claim
long-task/high-tool-call/resume robustness without concrete evidence slots.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LONG_TASK_PATTERNS = (
    re.compile(r"\blong[- ]?(?:task|running|horizon)\b", re.I),
    re.compile(r"\bhigh[- ]?tool[- ]?call\b", re.I),
    re.compile(r"\b\d{3,}\+?\s*(?:tool[- ]?)?calls?\b", re.I),
    re.compile(r"\bresume(?:able|d)?\b|\brecovery\b|\bcheckpoint(?:ed|s)?\b", re.I),
    re.compile(r"\bmulti[- ]?(?:hour|step)\b", re.I),
)

_SLOT_PATTERNS = {
    "checkpoints": re.compile(r"checkpoint|state snapshot|run ledger|state file", re.I),
    "tool_logs": re.compile(r"tool[- ]?log|tool[- ]?call|command log|worker job log|tools?\.jsonl", re.I),
    "resume_state": re.compile(r"resume[- ]?state|continuation packet|claim lease|recovery marker|restart", re.I),
    "error_classes": re.compile(r"error[- ]?class|failure mode|timeout|auth|quota|retryable|nonretryable|tool[- ]?schema", re.I),
    "final_verification": re.compile(r"final[- ]?verification|pytest|vitest|regression|curl|browser|read[- ]?back|feature[- ]?verification", re.I),
}

_WEAK_ONLY_PATTERNS = (
    re.compile(r"\bpy_compile\b", re.I),
    re.compile(r"\bselfcheck\b", re.I),
    re.compile(r"\bproof[- ]?check\b", re.I),
    re.compile(r"\bsha256\b|\bproof hash\b", re.I),
    re.compile(r"\bmultimodel\b.*\bpass\b|\bpass_advisory_only\b", re.I),
)

_CLAIM_FIELDS = (
    "title",
    "objective",
    "description",
    "capability_delta",
    "acceptance_evidence",
    "evidence",
    "evidence_refs",
    "validation",
)

_EVIDENCE_FIELDS = (
    "acceptance_evidence",
    "evidence",
    "evidence_refs",
    "validation",
    "proof_log",
)


@dataclass(frozen=True)
class ValidationResult:
    long_task_claim: bool
    ok: bool
    present_slots: list[str]
    missing_slots: list[str]
    weak_only_evidence: bool
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "taskpool_long_task_evidence_validation_v1",
            "long_task_claim": self.long_task_claim,
            "ok": self.ok,
            "present_slots": self.present_slots,
            "missing_slots": self.missing_slots,
            "weak_only_evidence": self.weak_only_evidence,
            "reasons": self.reasons,
        }


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(f"{k}: {_flatten(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def _candidate_text(candidate: dict[str, Any], fields: tuple[str, ...]) -> str:
    return "\n".join(_flatten(candidate.get(field)) for field in fields)


def validate_candidate(candidate: dict[str, Any]) -> ValidationResult:
    claim_text = _candidate_text(candidate, _CLAIM_FIELDS)
    evidence_text = _candidate_text(candidate, _EVIDENCE_FIELDS)
    long_task_claim = any(pattern.search(claim_text) for pattern in _LONG_TASK_PATTERNS)
    if not long_task_claim:
        return ValidationResult(
            long_task_claim=False,
            ok=True,
            present_slots=[],
            missing_slots=[],
            weak_only_evidence=False,
            reasons=["no_long_task_claim"],
        )

    present_slots = [slot for slot, pattern in _SLOT_PATTERNS.items() if pattern.search(evidence_text)]
    missing_slots = [slot for slot in _SLOT_PATTERNS if slot not in present_slots]
    weak_matches = [pattern.pattern for pattern in _WEAK_ONLY_PATTERNS if pattern.search(evidence_text)]
    weak_only = bool(weak_matches) and bool(missing_slots)

    reasons: list[str] = []
    if missing_slots:
        reasons.append("missing_long_task_evidence_slots:" + ",".join(missing_slots))
    if weak_only:
        reasons.append("weak_only_evidence_cannot_satisfy_long_task_claim")
    if not reasons:
        reasons.append("long_task_evidence_slots_present")

    return ValidationResult(
        long_task_claim=True,
        ok=not missing_slots and not weak_only,
        present_slots=present_slots,
        missing_slots=missing_slots,
        weak_only_evidence=weak_only,
        reasons=reasons,
    )


def _load_candidate(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        obj = json.load(fh)
    if not isinstance(obj, dict):
        raise SystemExit(f"candidate must be a JSON object: {path}")
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_json", type=Path, help="Task candidate JSON to validate")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    result = validate_candidate(_load_candidate(args.candidate_json))
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("OK" if result.ok else "FAIL")
        for reason in result.reasons:
            print(f"- {reason}")
    return 0 if result.ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
