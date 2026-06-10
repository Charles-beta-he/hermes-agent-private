"""Hermes-native skill candidate staging harness.

This module creates review-only skill patch artifacts. It deliberately never
applies candidate content to an installed skill; reviewers may inspect the JSON
artifact and use skill_manage only after separate approval.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.skills_tool import _parse_frontmatter

SCHEMA_VERSION = "hermes_skill_candidate_staging_v1"
APPLY_POLICY = "review_only_no_direct_apply"
_EVIDENCE_PREFIXES = ("artifact:", "command:", "file:", "proof:", "session:")
_REFERENCE_RE = re.compile(r"(?<![\w/.-])((?:references|templates|scripts|assets)/[A-Za-z0-9_./-]+)")
_BROAD_TRIGGER_RE = re.compile(
    r"\b(anything|everything|all tasks|all workflows|any task|general purpose|whatever|help with anything)\b",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_list(values: Iterable[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _check_frontmatter(frontmatter: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    missing = [key for key in ("name", "description") if not str(frontmatter.get(key) or "").strip()]
    status = "pass" if not missing else "fail"
    return {
        "status": status,
        "required": ["name", "description"],
        "missing": missing,
        "metadata_keys": sorted(str(key) for key in frontmatter.keys()),
    }, (["missing_frontmatter"] if missing else [])


def _check_trigger_breadth(description: str) -> tuple[dict[str, Any], list[str]]:
    description = description.strip()
    reasons: list[str] = []
    if len(description) < 30:
        reasons.append("trigger_too_short")
    if _BROAD_TRIGGER_RE.search(description):
        reasons.append("trigger_too_broad")
    if not description.lower().startswith("use when"):
        reasons.append("trigger_missing_use_when")
    return {
        "status": "pass" if not reasons else "fail",
        "description": description,
        "reason_codes": reasons,
    }, reasons


def _check_evidence(evidence: list[str]) -> tuple[dict[str, Any], list[str]]:
    invalid = [item for item in evidence if not item.startswith(_EVIDENCE_PREFIXES)]
    reasons: list[str] = []
    if not evidence:
        reasons.append("missing_evidence")
    if invalid:
        reasons.append("invalid_evidence_ref")
    return {
        "status": "pass" if not reasons else "fail",
        "accepted_prefixes": list(_EVIDENCE_PREFIXES),
        "count": len(evidence),
        "invalid": invalid,
    }, reasons


def _check_nonempty_list(name: str, values: list[str]) -> tuple[dict[str, Any], list[str]]:
    reason = f"missing_{name}"
    return {"status": "pass" if values else "fail", "count": len(values)}, ([] if values else [reason])


def _referenced_paths(content: str) -> list[str]:
    refs: set[str] = set()
    for match in _REFERENCE_RE.finditer(content):
        ref = match.group(1).rstrip(".,;)\n")
        if ".." not in Path(ref).parts:
            refs.add(ref)
    return sorted(refs)


def _check_references(
    candidate_skill: Path,
    candidate_content: str,
    *,
    base_content: str = "",
) -> tuple[dict[str, Any], list[str]]:
    # Stage-time checks should reject newly introduced dangling references
    # without making unrelated pre-existing references in the base skill block a
    # narrow candidate patch. This keeps the gate scoped to the staged delta.
    refs = _referenced_paths(candidate_content)
    base_refs = set(_referenced_paths(base_content))
    introduced = sorted(ref for ref in refs if ref not in base_refs)
    base_dir = candidate_skill.parent
    missing = [ref for ref in introduced if not (base_dir / ref).exists()]
    return {
        "status": "pass" if not missing else "fail",
        "referenced": refs,
        "introduced": introduced,
        "missing": missing,
    }, (["missing_referenced_file"] if missing else [])


def _unified_diff(base_skill: Path, candidate_skill: Path, base_content: str, candidate_content: str) -> str:
    return "".join(
        difflib.unified_diff(
            base_content.splitlines(keepends=True),
            candidate_content.splitlines(keepends=True),
            fromfile=str(base_skill),
            tofile=str(candidate_skill),
        )
    )


def stage_skill_candidate(
    *,
    base_skill: str | Path,
    candidate_skill: str | Path,
    evidence: Iterable[str] | None,
    verification: Iterable[str] | None,
    rollback: Iterable[str] | None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a review-only staged skill patch artifact.

    ``base_skill`` is read for diff context only. ``candidate_skill`` is read as
    the proposed replacement content. Neither file is modified.
    """

    base_path = Path(base_skill).expanduser()
    candidate_path = Path(candidate_skill).expanduser()
    evidence_refs = _as_list(evidence)
    verification_steps = _as_list(verification)
    rollback_steps = _as_list(rollback)

    base_content = base_path.read_text(encoding="utf-8")
    candidate_content = candidate_path.read_text(encoding="utf-8")
    candidate_frontmatter, _body = _parse_frontmatter(candidate_content)
    diff = _unified_diff(base_path, candidate_path, base_content, candidate_content)

    checks: dict[str, Any] = {}
    rejection_reasons: list[str] = []
    for key, check_result in (
        ("frontmatter", _check_frontmatter(candidate_frontmatter)),
        ("trigger_breadth", _check_trigger_breadth(str(candidate_frontmatter.get("description") or ""))),
        ("evidence", _check_evidence(evidence_refs)),
        ("verification", _check_nonempty_list("verification", verification_steps)),
        ("rollback", _check_nonempty_list("rollback", rollback_steps)),
        ("references", _check_references(candidate_path, candidate_content, base_content=base_content)),
    ):
        checks[key] = check_result[0]
        rejection_reasons.extend(check_result[1])

    if not diff:
        checks["diff"] = {"status": "fail", "reason_codes": ["empty_diff"]}
        rejection_reasons.append("empty_diff")
    else:
        checks["diff"] = {"status": "pass", "bytes": len(diff.encode("utf-8"))}

    rejection_reasons = sorted(set(rejection_reasons))
    status = "rejected" if rejection_reasons else "staged"
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now_iso(),
        "status": status,
        "apply_policy": APPLY_POLICY,
        "skill": {
            "name": str(candidate_frontmatter.get("name") or candidate_path.parent.name),
            "base_skill": str(base_path),
            "candidate_skill": str(candidate_path),
            "base_sha256": _sha256_text(base_content),
            "candidate_sha256": _sha256_text(candidate_content),
        },
        "patch": {
            "kind": "unified_diff",
            "unified_diff": diff,
        },
        "evidence": evidence_refs,
        "verification": verification_steps,
        "rollback": rollback_steps,
        "checks": checks,
        "rejection_reasons": rejection_reasons,
    }

    if output is not None:
        output_path = Path(output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        artifact["artifact_path"] = str(output_path)
        output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return artifact


def _split_csv(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage a review-only Hermes skill candidate patch")
    parser.add_argument("--base-skill", required=True, help="Installed/base SKILL.md path for diff context")
    parser.add_argument("--candidate-skill", required=True, help="Proposed candidate SKILL.md path")
    parser.add_argument("--evidence", action="append", default=[], help="Evidence ref; repeat or comma-separate")
    parser.add_argument("--verification", action="append", default=[], help="Verification step; repeat or comma-separate")
    parser.add_argument("--rollback", action="append", default=[], help="Rollback step; repeat or comma-separate")
    parser.add_argument("--output", required=True, help="Write staging artifact JSON to this path")
    args = parser.parse_args(argv)

    report = stage_skill_candidate(
        base_skill=args.base_skill,
        candidate_skill=args.candidate_skill,
        evidence=_split_csv(args.evidence),
        verification=_split_csv(args.verification),
        rollback=_split_csv(args.rollback),
        output=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "staged" else 2


if __name__ == "__main__":
    raise SystemExit(main())
