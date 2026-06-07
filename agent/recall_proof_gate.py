from __future__ import annotations

"""Replayable proof gate for memory/local-brain/session recall quality.

This module turns recall evaluation from a subjective vibe-check into a
replayable gate with explicit reject reasons. It does not fetch live data or
mutate runtime memory; it evaluates candidate recall packets supplied in a JSON
fixture and emits a deterministic report.

Primary use-cases:
- benchmark whether a proposed memory/local-brain recall packet is decision-grade
- reject stale, cross-scope, or reference-only recall before it drives work
- compare future gate changes against a stable fixture corpus
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FIXTURE_SCHEMA_VERSION = "hermes.recall_proof_gate.fixture.v1"
REPORT_SCHEMA_VERSION = "hermes.recall_proof_gate.report.v1"

_DRIFT_TEXT_RE = re.compile(
    r"\b(branch|pr|issue|commit|sha|task|ticket|path|migration|endpoint|api|status|ready to merge|next step)\b",
    re.IGNORECASE,
)

_ADOPTED_SOURCE_KINDS = {
    "memory",
    "mempalace",
    "walrus_memory",
    "local_brain",
    "local_brain_task",
    "session_search",
}


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate_id: str
    source_kind: str
    admissible: bool
    quality_score: float
    passed_checks: List[str]
    failures: List[str]
    warnings: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_kind": self.source_kind,
            "admissible": self.admissible,
            "quality_score": self.quality_score,
            "passed_checks": self.passed_checks,
            "failures": self.failures,
            "warnings": self.warnings,
        }


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_now(now: Optional[str] = None) -> datetime:
    parsed = _parse_timestamp(now)
    if parsed is not None:
        return parsed
    return datetime.now(timezone.utc)


def _is_same_scope(current_scope: str, candidate_scope: str) -> bool:
    current = (current_scope or "").strip().lower()
    candidate = (candidate_scope or "").strip().lower()
    if not candidate:
        return False
    if candidate in {"global", "shared", "cross-project"}:
        return True
    return candidate == current


def _candidate_has_provenance(candidate: Dict[str, Any]) -> bool:
    source_kind = bool(str(candidate.get("source_kind") or "").strip())
    source_anchor = any(
        candidate.get(field) not in (None, "")
        for field in ("source_path", "session_id", "message_id", "artifact_path")
    )
    return source_kind and source_anchor


def _source_kind(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("source_kind") or "").strip().lower()


def _is_drift_sensitive(candidate: Dict[str, Any]) -> bool:
    tags = [str(tag).strip().lower() for tag in candidate.get("tags", []) if str(tag).strip()]
    if "drift_sensitive" in tags:
        return True
    text = str(candidate.get("text") or "")
    return bool(_DRIFT_TEXT_RE.search(text))


def _age_in_days(verified_at: datetime, now: datetime) -> float:
    return max(0.0, (now - verified_at).total_seconds() / 86400.0)


def evaluate_candidate(
    candidate: Dict[str, Any],
    *,
    current_scope: str,
    now: datetime,
    min_confidence: float,
    max_age_days: int,
    require_same_scope: bool,
    require_decision_grade: bool,
) -> CandidateEvaluation:
    passed: List[str] = []
    failures: List[str] = []
    warnings: List[str] = []

    candidate_id = str(candidate.get("id") or "unknown")
    source_kind = _source_kind(candidate)

    if source_kind in _ADOPTED_SOURCE_KINDS:
        passed.append("source_kind")
    elif source_kind:
        failures.append(f"unsupported source_kind: {source_kind}")
    else:
        failures.append("missing source_kind")

    if _candidate_has_provenance(candidate):
        passed.append("provenance")
    else:
        failures.append("missing provenance: require source_kind plus source_path/session_id/message_id/artifact_path")

    candidate_scope = str(candidate.get("scope") or "").strip()
    if candidate_scope and _is_same_scope(current_scope, candidate_scope):
        passed.append("scope")
    elif require_same_scope:
        failures.append(f"scope mismatch: current={current_scope!r} candidate={candidate_scope!r}")
    else:
        warnings.append(f"scope mismatch: current={current_scope!r} candidate={candidate_scope!r}")

    confidence = candidate.get("confidence")
    if isinstance(confidence, (int, float)) and float(confidence) >= min_confidence:
        passed.append("confidence")
    else:
        failures.append(f"confidence below threshold: need >= {min_confidence:.2f}")

    if require_decision_grade and candidate.get("reference_only"):
        failures.append("reference-only recall cannot be used as decision-grade proof")
    else:
        passed.append("decision_grade")

    expires_at = _parse_timestamp(candidate.get("expires_at"))
    if expires_at is not None and expires_at < now:
        failures.append(f"expired at {expires_at.isoformat()}")
    else:
        verified_at = _parse_timestamp(candidate.get("last_verified_at"))
        if _is_drift_sensitive(candidate):
            if verified_at is None:
                failures.append("drift-sensitive recall missing last_verified_at")
            else:
                age_days = _age_in_days(verified_at, now)
                if age_days > max_age_days:
                    failures.append(
                        f"drift-sensitive recall too old: {age_days:.1f}d > {max_age_days}d"
                    )
                else:
                    passed.append("freshness")
        elif verified_at is not None:
            passed.append("freshness")
        else:
            warnings.append("non-drift-sensitive recall missing last_verified_at")

    admissible = not failures
    total_checks = 6.0
    quality_score = round(len(passed) / total_checks, 3)
    return CandidateEvaluation(
        candidate_id=candidate_id,
        source_kind=source_kind,
        admissible=admissible,
        quality_score=quality_score,
        passed_checks=passed,
        failures=failures,
        warnings=warnings,
    )


def _expected_case_passed(case: Dict[str, Any], winner_id: Optional[str], evaluations: List[CandidateEvaluation]) -> bool:
    if "expected_winner_id" in case and winner_id != case.get("expected_winner_id"):
        return False
    if case.get("expected_no_admissible") and winner_id is not None:
        return False
    by_id = {ev.candidate_id: ev for ev in evaluations}
    for candidate_id in case.get("expected_rejected_candidate_ids", []):
        ev = by_id.get(str(candidate_id))
        if ev is None or ev.admissible:
            return False
    for candidate_id in case.get("expected_admitted_candidate_ids", []):
        ev = by_id.get(str(candidate_id))
        if ev is None or not ev.admissible:
            return False
    return True


def evaluate_case(case: Dict[str, Any], *, defaults: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    current_scope = str(case.get("current_scope") or defaults.get("current_scope") or "global")
    min_confidence = float(case.get("min_confidence", defaults.get("min_confidence", 0.65)))
    max_age_days = int(case.get("max_age_days", defaults.get("max_age_days", 30)))
    require_same_scope = bool(case.get("require_same_scope", defaults.get("require_same_scope", True)))
    require_decision_grade = bool(
        case.get("require_decision_grade", defaults.get("require_decision_grade", True))
    )

    evaluations = [
        evaluate_candidate(
            candidate,
            current_scope=current_scope,
            now=now,
            min_confidence=min_confidence,
            max_age_days=max_age_days,
            require_same_scope=require_same_scope,
            require_decision_grade=require_decision_grade,
        )
        for candidate in case.get("candidates", [])
    ]

    winner = None
    admissible = [ev for ev in evaluations if ev.admissible]
    if admissible:
        winner = sorted(
            admissible,
            key=lambda ev: (ev.quality_score, -len(ev.failures), -len(ev.warnings), ev.candidate_id),
            reverse=True,
        )[0]

    winner_id = winner.candidate_id if winner else None
    expectation_passed = _expected_case_passed(case, winner_id, evaluations)
    return {
        "case_id": case.get("id"),
        "fixture_class": case.get("fixture_class"),
        "prompt": case.get("prompt"),
        "current_scope": current_scope,
        "passed": expectation_passed,
        "winner_id": winner_id,
        "winner_score": winner.quality_score if winner else None,
        "expected_winner_id": case.get("expected_winner_id"),
        "expected_no_admissible": bool(case.get("expected_no_admissible")),
        "expected_rejected_candidate_ids": case.get("expected_rejected_candidate_ids", []),
        "candidate_evaluations": [ev.as_dict() for ev in evaluations],
    }


def _source_summary(case_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}
    for result in case_results:
        for ev in result.get("candidate_evaluations", []):
            source = str(ev.get("source_kind") or "unknown")
            bucket = summary.setdefault(source, {"admitted": 0, "rejected": 0})
            if ev.get("admissible"):
                bucket["admitted"] += 1
            else:
                bucket["rejected"] += 1
    return summary


def run_fixture(fixture: Dict[str, Any], *, now: Optional[str] = None) -> Dict[str, Any]:
    schema = fixture.get("schema_version")
    if schema != FIXTURE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported fixture schema_version: {schema!r}")

    defaults = fixture.get("defaults") or {}
    ts = _utc_now(now)
    case_results = [evaluate_case(case, defaults=defaults, now=ts) for case in fixture.get("cases", [])]
    total = len(case_results)
    passed = sum(1 for result in case_results if result["passed"])
    failed = total - passed
    pass_rate = round((passed / total), 3) if total else 1.0
    fixture_classes = sorted({str(result.get("fixture_class")) for result in case_results if result.get("fixture_class")})
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "fixture_schema_version": schema,
        "gate_active": True,
        "evaluated_at": ts.isoformat(),
        "summary": {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "pass_rate": pass_rate,
            "verdict": "pass" if failed == 0 else "fail",
            "fixture_classes": fixture_classes,
            "fixture_class_count": len(fixture_classes),
            "source_kind_summary": _source_summary(case_results),
        },
        "cases": case_results,
    }


def load_fixture(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replayable recall proof gate for Hermes memory/local-brain recall packets")
    parser.add_argument("fixture", type=Path, help="JSON fixture file to evaluate")
    parser.add_argument("--now", default=None, help="Override evaluation timestamp (ISO-8601)")
    parser.add_argument("--output", type=Path, default=None, help="Optional path to write the JSON report")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = run_fixture(load_fixture(args.fixture), now=args.now)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["summary"]["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
