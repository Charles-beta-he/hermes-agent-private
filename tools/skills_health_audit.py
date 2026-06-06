"""Deterministic, read-only health audit for Hermes skill files.

This module intentionally does not rewrite skills.  It emits schema-versioned
JSON that can be used as taskpool evidence before separately reviewed fixes.
The audit fails closed when the active Hermes profile context is missing or
when a supplied skills root points at another profile's skills directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "hermes_skills_health_audit_v1"
DEFAULT_MAX_DESCRIPTION_CHARS = 900
DEFAULT_MAX_SKILL_CHARS = 32000
_TRIGGER_RE = re.compile(r"\b[a-z][a-z0-9_-]{2,}\b", re.IGNORECASE)


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    path: Path
    size_chars: int
    metadata: dict[str, Any]


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser().resolve()
    except Exception:
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()


def _active_profile_name(hermes_home: Path | None = None) -> str:
    explicit = os.environ.get("HERMES_PROFILE") or os.environ.get("HERMES_ACTIVE_PROFILE")
    if explicit:
        return explicit
    if hermes_home is not None:
        home = hermes_home.expanduser().resolve()
        if home.parent.name == "profiles" and home.name:
            return home.name
    return "default"


def _default_skill_roots(hermes_home: Path) -> list[Path]:
    return [hermes_home / "skills"]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + len("\n---") :].lstrip("\n")
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, body


def _discover_skill_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file() and root.name == "SKILL.md":
            files.append(root)
            continue
        files.extend(root.rglob("SKILL.md"))
    return sorted({p.resolve() for p in files})


def _read_skill(path: Path) -> SkillRecord:
    text = path.read_text(encoding="utf-8", errors="replace")
    metadata, _body = _frontmatter(text)
    name = metadata.get("name") or path.parent.name
    description = metadata.get("description", "")
    return SkillRecord(
        name=name,
        description=description,
        path=path.resolve(),
        size_chars=len(text),
        metadata=dict(metadata),
    )


def _trigger_terms(description: str) -> set[str]:
    stop = {
        "and",
        "for",
        "the",
        "with",
        "when",
        "use",
        "using",
        "skill",
        "skills",
        "agent",
        "hermes",
        "from",
        "into",
        "this",
        "that",
    }
    return {m.group(0).lower() for m in _TRIGGER_RE.finditer(description) if m.group(0).lower() not in stop}


def _profiles_dir_for_home(hermes_home: Path) -> Path:
    """Return the profile container for both root-home and profile-home layouts."""
    home = hermes_home.expanduser().resolve()
    if home.parent.name == "profiles":
        return home.parent
    return home / "profiles"


def _classify_root(root: Path, hermes_home: Path, active_profile: str) -> dict[str, Any]:
    root = root.expanduser().resolve()
    profiles_dir = _profiles_dir_for_home(hermes_home)
    info = {"path": str(root), "exists": root.exists(), "profile": active_profile, "cross_profile": False}
    try:
        rel = root.relative_to(profiles_dir)
    except ValueError:
        return info
    parts = rel.parts
    if not parts:
        info["profile"] = "*"
        info["cross_profile"] = True
        return info
    profile = parts[0]
    info["profile"] = profile
    info["cross_profile"] = profile != active_profile
    return info


def audit_skills(
    roots: Iterable[str | Path] | None = None,
    *,
    hermes_home: str | Path | None = None,
    active_profile: str | None = None,
    max_description_chars: int = DEFAULT_MAX_DESCRIPTION_CHARS,
    max_skill_chars: int = DEFAULT_MAX_SKILL_CHARS,
) -> dict[str, Any]:
    """Return a schema-versioned read-only skill health audit."""

    home = Path(hermes_home).expanduser().resolve() if hermes_home else _hermes_home()
    profile = active_profile or _active_profile_name(home)
    root_paths = [Path(r).expanduser() for r in roots] if roots is not None else _default_skill_roots(home)

    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    root_reports = [_classify_root(root, home, profile) for root in root_paths]

    if not profile:
        violations.append({"code": "missing_active_profile", "message": "active Hermes profile context is required"})
    if not home.exists():
        violations.append({"code": "missing_hermes_home", "path": str(home)})

    safe_root_paths: list[Path] = []
    for root, root_info in zip(root_paths, root_reports, strict=False):
        if root_info["cross_profile"]:
            violations.append(
                {
                    "code": "cross_profile_skill_root",
                    "path": root_info["path"],
                    "profile": root_info["profile"],
                    "active_profile": profile,
                    "message": "refusing to audit another profile as if it were the active profile",
                }
            )
        elif not root_info["exists"]:
            warnings.append({"code": "missing_skill_root", "path": root_info["path"]})
        else:
            safe_root_paths.append(root)

    safe_skill_files: list[Path] = []
    for skill_file in _discover_skill_files(safe_root_paths):
        skill_info = _classify_root(skill_file, home, profile)
        if skill_info["cross_profile"]:
            violations.append(
                {
                    "code": "cross_profile_skill_root",
                    "path": skill_info["path"],
                    "profile": skill_info["profile"],
                    "active_profile": profile,
                    "message": "refusing to read a skill file from another profile",
                }
            )
            continue
        safe_skill_files.append(skill_file)

    records = [_read_skill(path) for path in safe_skill_files]
    checked = []
    by_name: dict[str, list[SkillRecord]] = {}
    terms_by_skill: dict[str, set[str]] = {}

    for rec in records:
        checked.append(
            {
                "name": rec.name,
                "path": str(rec.path),
                "description_chars": len(rec.description),
                "size_chars": rec.size_chars,
            }
        )
        by_name.setdefault(rec.name, []).append(rec)
        terms_by_skill[str(rec.path)] = _trigger_terms(rec.description)
        if not rec.metadata.get("name"):
            violations.append({"code": "missing_name", "path": str(rec.path)})
        if not rec.description:
            violations.append({"code": "missing_description", "path": str(rec.path), "name": rec.name})
        if len(rec.description) > max_description_chars:
            violations.append(
                {
                    "code": "description_token_budget_exceeded",
                    "path": str(rec.path),
                    "name": rec.name,
                    "description_chars": len(rec.description),
                    "limit": max_description_chars,
                }
            )
        if rec.size_chars > max_skill_chars:
            warnings.append(
                {
                    "code": "skill_file_large",
                    "path": str(rec.path),
                    "name": rec.name,
                    "size_chars": rec.size_chars,
                    "limit": max_skill_chars,
                }
            )

    for name, matches in sorted(by_name.items()):
        if len(matches) > 1:
            violations.append(
                {
                    "code": "duplicate_skill_name",
                    "name": name,
                    "paths": [str(m.path) for m in matches],
                }
            )

    recs = records
    for idx, left in enumerate(recs):
        left_terms = terms_by_skill[str(left.path)]
        if len(left_terms) < 5:
            continue
        for right in recs[idx + 1 :]:
            right_terms = terms_by_skill[str(right.path)]
            if len(right_terms) < 5:
                continue
            overlap = sorted(left_terms & right_terms)
            if len(overlap) >= 8:
                warnings.append(
                    {
                        "code": "duplicate_trigger_overlap",
                        "left": str(left.path),
                        "right": str(right.path),
                        "overlap_terms": overlap[:20],
                        "overlap_count": len(overlap),
                    }
                )

    status = "FAIL" if violations else "WARN" if warnings else "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "read_only": True,
        "policy": "fail-closed on missing profile context or cross-profile skill roots; no skill content is modified",
        "hermes_home": str(home),
        "active_profile": profile,
        "roots": root_reports,
        "checked_count": len(checked),
        "checked_skills": checked,
        "violation_count": len(violations),
        "warning_count": len(warnings),
        "violations": violations,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Hermes skills health audit")
    parser.add_argument("--root", action="append", dest="roots", help="Skill root to audit; may be repeated")
    parser.add_argument("--hermes-home", help="Hermes home for profile-boundary checks")
    parser.add_argument("--active-profile", help="Active Hermes profile name (default: env/default)")
    parser.add_argument("--max-description-chars", type=int, default=DEFAULT_MAX_DESCRIPTION_CHARS)
    parser.add_argument("--max-skill-chars", type=int, default=DEFAULT_MAX_SKILL_CHARS)
    parser.add_argument("--json", action="store_true", help="Emit JSON (default; kept for CLI symmetry)")
    args = parser.parse_args(argv)
    report = audit_skills(
        args.roots,
        hermes_home=args.hermes_home,
        active_profile=args.active_profile,
        max_description_chars=args.max_description_chars,
        max_skill_chars=args.max_skill_chars,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
