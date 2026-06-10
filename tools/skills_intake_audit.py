"""Governed, read-only intake audit for external skill candidates.

This module evaluates external skill identifiers / URLs before installation and
emits deterministic JSON suitable for taskpool evidence and future CLI wiring.

Scope of this bounded step:
- resolve external skill bundles via the existing skills hub source adapters
- support GitHub repo URLs by discovering skill directories from repo trees
- reuse skills_guard for security verdict + install-policy evaluation
- surface structural / prerequisite / runtime compatibility signals
- fail closed for Node engine lower bounds >=25

This is intentionally read-only: it never installs, rewrites, or mutates the
active skills directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import httpx

from hermes_cli.skills_hub import _resolve_source_meta_and_bundle
from tools.skills_guard import scan_skill, should_allow_install
from tools.skills_hub import GitHubAuth, GitHubSource, SkillBundle, create_source_router
from tools.skills_tool import (
    _collect_prerequisite_values,
    _get_required_environment_variables,
    _parse_frontmatter,
    _is_env_var_persisted,
    skill_matches_environment,
    skill_matches_platform,
)

SCHEMA_VERSION = "hermes_external_skill_intake_v1"
DEFAULT_MAX_SKILLS_PER_REPO = 5
_STATUS_RANK = {"compatible": 0, "review_needed": 1, "incompatible": 2}
_NODE_RANGE_RE = re.compile(r"(>=|>|\^|~)?\s*v?(\d+)")
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_NODE_RUNTIME_EXTENSIONS = (".js", ".mjs", ".cjs", ".ts", ".tsx")


@dataclass(frozen=True)
class CandidateRef:
    raw: str
    kind: str
    repo: str | None = None
    identifier: str | None = None
    url: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class NodeRuntimeSignal:
    detected: bool
    package_json_present: bool
    engine_range: str | None
    minimum_major: int | None
    fail_closed: bool
    reason_code: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_github_candidate(candidate: str) -> CandidateRef | None:
    try:
        parsed = urlparse(candidate)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in _GITHUB_HOSTS:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return CandidateRef(raw=candidate, kind="github_url", url=candidate)

    repo = f"{parts[0]}/{parts[1]}"
    if len(parts) == 2:
        return CandidateRef(raw=candidate, kind="github_repo_url", repo=repo, url=candidate)

    if parts[2] in {"tree", "blob"} and len(parts) >= 5:
        path = "/".join(parts[4:]).strip("/")
        if path.endswith("/SKILL.md"):
            path = path[: -len("/SKILL.md")]
        if path:
            return CandidateRef(
                raw=candidate,
                kind="github_skill_url",
                repo=repo,
                identifier=f"{repo}/{path}",
                path=path,
                url=candidate,
            )

    path = "/".join(parts[2:]).strip("/")
    if path.endswith("/SKILL.md"):
        path = path[: -len("/SKILL.md")]
    if path:
        return CandidateRef(
            raw=candidate,
            kind="github_skill_url",
            repo=repo,
            identifier=f"{repo}/{path}",
            path=path,
            url=candidate,
        )
    return CandidateRef(raw=candidate, kind="github_repo_url", repo=repo, url=candidate)


def _normalize_candidate(candidate: str) -> CandidateRef:
    candidate = str(candidate).strip()
    gh = _parse_github_candidate(candidate)
    if gh is not None:
        return gh
    if candidate.startswith(("http://", "https://")):
        return CandidateRef(raw=candidate, kind="url", url=candidate, identifier=candidate)
    if candidate.count("/") >= 2:
        return CandidateRef(raw=candidate, kind="identifier", identifier=candidate)
    return CandidateRef(raw=candidate, kind="unknown", identifier=candidate)


def _repo_skill_identifiers(
    repo: str,
    github_source: GitHubSource,
    *,
    max_skills_per_repo: int,
) -> tuple[list[str], int]:
    cached = github_source._get_repo_tree(repo)  # existing read-only hub helper
    if cached is None:
        return [], 0
    _default_branch, tree_entries = cached
    identifiers: list[str] = []
    seen: set[str] = set()
    for entry in tree_entries:
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path", ""))
        if not path.endswith("/SKILL.md") and path != "SKILL.md":
            continue
        skill_dir = path[: -len("/SKILL.md")].rstrip("/")
        identifier = f"{repo}/{skill_dir}" if skill_dir else None
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        identifiers.append(identifier)
    identifiers = sorted(identifiers)
    return identifiers[:max_skills_per_repo], len(identifiers)


def _write_bundle_to_dir(bundle: SkillBundle, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in bundle.files.items():
        out = target_dir / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            out.write_text(content.decode("utf-8", errors="replace"), encoding="utf-8")
        else:
            out.write_text(content, encoding="utf-8")
    return target_dir


def _bundle_text(bundle: SkillBundle, rel_path: str) -> str:
    content = bundle.files.get(rel_path, "")
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)


def _bundle_has_node_runtime_artifacts(bundle: SkillBundle) -> bool:
    for path in bundle.files:
        normalized = str(path).replace("\\", "/")
        if not normalized.endswith(_NODE_RUNTIME_EXTENSIONS):
            continue
        # Templates, references, and assets often contain example code snippets
        # or starter files but do not imply that the skill itself requires a
        # Node runtime to function at audit/load time.
        if normalized.startswith(("templates/", "references/", "assets/")):
            continue
        return True
    return False


def _node_runtime_signal(bundle: SkillBundle) -> NodeRuntimeSignal:
    package_json = _bundle_text(bundle, "package.json") if "package.json" in bundle.files else ""
    node_like = bool(package_json) or _bundle_has_node_runtime_artifacts(bundle)
    if not node_like:
        return NodeRuntimeSignal(False, False, None, None, False, None)

    engine_range: str | None = None
    minimum_major: int | None = None
    if package_json:
        try:
            package = json.loads(package_json)
        except Exception:
            package = {}
        engines = package.get("engines") if isinstance(package, dict) else None
        if isinstance(engines, dict):
            raw = engines.get("node")
            if isinstance(raw, str) and raw.strip():
                engine_range = raw.strip()
                majors = [int(match.group(2)) for match in _NODE_RANGE_RE.finditer(engine_range)]
                if majors:
                    minimum_major = min(majors)

    if engine_range and minimum_major is not None and minimum_major >= 25:
        return NodeRuntimeSignal(
            detected=True,
            package_json_present=bool(package_json),
            engine_range=engine_range,
            minimum_major=minimum_major,
            fail_closed=True,
            reason_code="node_engine_fail_closed",
        )

    if node_like and not engine_range:
        return NodeRuntimeSignal(
            detected=True,
            package_json_present=bool(package_json),
            engine_range=None,
            minimum_major=None,
            fail_closed=False,
            reason_code="node_runtime_unbounded",
        )

    return NodeRuntimeSignal(
        detected=True,
        package_json_present=bool(package_json),
        engine_range=engine_range,
        minimum_major=minimum_major,
        fail_closed=False,
        reason_code=None,
    )


def _bundle_structure(bundle: SkillBundle) -> dict[str, Any]:
    file_paths = sorted(bundle.files)
    return {
        "has_skill_md": "SKILL.md" in bundle.files,
        "file_count": len(bundle.files),
        "file_paths": file_paths,
        "reference_file_count": len([p for p in file_paths if p.startswith("references/")]),
        "template_file_count": len([p for p in file_paths if p.startswith("templates/")]),
        "script_file_count": len([p for p in file_paths if p.startswith("scripts/")]),
        "asset_file_count": len([p for p in file_paths if p.startswith("assets/")]),
        "total_chars": sum(len(v) for v in bundle.files.values()),
        "has_skillignore": any(p in {".skillignore", ".clawhubignore"} for p in file_paths),
        "has_package_json": "package.json" in bundle.files,
    }


def _skill_prerequisites(frontmatter: dict[str, Any]) -> dict[str, Any]:
    legacy_env_vars, legacy_commands = _collect_prerequisite_values(frontmatter)
    required_env_vars = _get_required_environment_variables(frontmatter, legacy_env_vars)
    missing_required_env_vars = [
        entry["name"]
        for entry in required_env_vars
        if not entry.get("optional") and not _is_env_var_persisted(entry["name"])
    ]
    required_commands = legacy_commands
    missing_required_commands = [cmd for cmd in required_commands if shutil.which(cmd) is None]
    return {
        "required_environment_variables": required_env_vars,
        "missing_required_environment_variables": missing_required_env_vars,
        "required_commands": required_commands,
        "missing_required_commands": missing_required_commands,
    }


def _summarize_findings(findings: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(
        findings,
        key=lambda f: (severity_rank.get(getattr(f, "severity", "low"), 9), getattr(f, "file", ""), getattr(f, "line", 0)),
    )
    out = []
    for finding in sorted_findings[:limit]:
        out.append(
            {
                "pattern_id": finding.pattern_id,
                "severity": finding.severity,
                "category": finding.category,
                "file": finding.file,
                "line": finding.line,
                "description": finding.description,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Compatibility scoring
# ---------------------------------------------------------------------------
# Each reason code maps to a point deduction from a base of 100.
# Hard-fail codes (platform, guard_dangerous, node_fail_closed) zero the score
# immediately.  Review codes carry graduated penalties so operators can set
# intake thresholds (e.g. "auto-install >= 80, queue for review >= 50").
_SCORE_DEDUCTIONS: dict[str, int] = {
    # --- hard-fail (handled by zeroing below, but listed for documentation) ---
    "unsupported_platform": 100,
    "guard_dangerous": 100,
    "node_engine_fail_closed": 100,
    "bundle_unresolvable": 100,
    # --- review-level penalties ---
    "guard_policy_blocked": 30,
    "guard_confirmation_required": 15,
    "missing_required_commands": 15,
    "missing_required_environment_variables": 10,
    "environment_filter_mismatch": 10,
    "node_runtime_unbounded": 10,
    "missing_commit_evidence": 10,
    "missing_license_evidence": 10,
    "license_missing_spdx": 5,
    "github_source_unavailable": 100,
    "no_skill_candidates_discovered": 100,
    "unsupported_candidate": 100,
}
_HARD_FAIL_CODES = {
    "unsupported_platform",
    "guard_dangerous",
    "node_engine_fail_closed",
    "bundle_unresolvable",
    "github_source_unavailable",
    "no_skill_candidates_discovered",
    "unsupported_candidate",
}


def _grade_for_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 50:
        return "C"
    if score >= 25:
        return "D"
    return "F"


def _compute_compatibility_score(
    reason_codes: list[str],
    *,
    missing_commands: list[str] | None = None,
    missing_env_vars: list[str] | None = None,
) -> dict[str, Any]:
    """Compute a 0-100 compatibility score from reason codes.

    Returns a dict with ``score`` (int), ``grade`` (str), and
    ``breakdown`` (list of ``{"code": str, "deduction": int}``).

    Hard-fail codes immediately zero the score.  Per-item penalties for
    ``missing_required_commands`` and ``missing_required_environment_variables``
    scale with the actual count of missing items (15 pts each for commands,
    10 pts each for env vars) rather than a flat deduction.
    """
    if not reason_codes:
        return {"score": 100, "grade": "A", "breakdown": []}

    # Check hard-fail first.
    hard_fail = [c for c in reason_codes if c in _HARD_FAIL_CODES]
    if hard_fail:
        return {
            "score": 0,
            "grade": "F",
            "breakdown": [{"code": c, "deduction": 100} for c in hard_fail],
        }

    total_deduction = 0
    breakdown: list[dict[str, Any]] = []
    for code in sorted(set(reason_codes)):
        if code == "missing_required_commands" and missing_commands:
            deduction = 15 * len(missing_commands)
        elif code == "missing_required_environment_variables" and missing_env_vars:
            deduction = 10 * len(missing_env_vars)
        else:
            deduction = _SCORE_DEDUCTIONS.get(code, 10)
        total_deduction += deduction
        breakdown.append({"code": code, "deduction": deduction})

    score = max(0, 100 - total_deduction)
    return {"score": score, "grade": _grade_for_score(score), "breakdown": breakdown}


def _aggregate_compatibility_score(
    audited_skills: list[dict[str, Any]],
    candidate_errors: list[str],
) -> dict[str, Any]:
    """Aggregate a candidate-level score from skill reports + candidate errors.

    The overall candidate score is the minimum of each audited skill score and
    the standalone score implied by candidate-level discovery/normalization
    errors. This stays fail-closed without double-penalizing repositories by
    summing every review code across every discovered skill.
    """
    components: list[dict[str, Any]] = []

    if candidate_errors:
        candidate_component = _compute_compatibility_score(candidate_errors)
        components.append(
            {
                "scope": "candidate",
                "label": "candidate_errors",
                **candidate_component,
            }
        )

    for item in audited_skills:
        item_score = item.get("compatibility_score") or {"score": 0, "grade": "F", "breakdown": []}
        components.append(
            {
                "scope": "skill",
                "label": item.get("name") or item.get("identifier") or "unknown",
                **item_score,
            }
        )

    if not components:
        return {
            "score": 0,
            "grade": "F",
            "aggregate_method": "min_component_score",
            "components": [],
        }

    score = min(int(component.get("score", 0)) for component in components)
    return {
        "score": score,
        "grade": _grade_for_score(score),
        "aggregate_method": "min_component_score",
        "components": components,
    }


def _status_from_reason_sets(incompatible: list[str], review: list[str]) -> str:
    if incompatible:
        return "incompatible"
    if review:
        return "review_needed"
    return "compatible"


def _aggregate_statuses(statuses: list[str]) -> str:
    unique = set(statuses)
    if not unique:
        return "incompatible"
    if len(unique) == 1:
        return next(iter(unique))
    if "review_needed" in unique:
        return "review_needed"
    if unique == {"compatible", "incompatible"}:
        return "review_needed"
    return max(unique, key=lambda s: _STATUS_RANK.get(s, 99))


def _filtered_sources_for_candidate(ref: CandidateRef, sources: list[Any]) -> list[Any]:
    if ref.kind in {"github_repo_url", "github_skill_url"}:
        filtered = [src for src in sources if getattr(src, "source_id", lambda: "")() == "github"]
        return filtered or sources
    if ref.kind != "url":
        return sources
    filtered = [src for src in sources if getattr(src, "source_id", lambda: "")() == "url"]
    return filtered or sources


def _github_json(url: str, auth: GitHubAuth) -> dict[str, Any] | None:
    try:
        resp = httpx.get(url, headers=auth.get_headers(), timeout=20, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _github_license_evidence(repo: str, auth: GitHubAuth) -> dict[str, Any] | None:
    data = _github_json(f"https://api.github.com/repos/{repo}/license", auth)
    if not data:
        return None
    raw_license_meta = data.get("license")
    license_meta: dict[str, Any] = raw_license_meta if isinstance(raw_license_meta, dict) else {}
    return {
        "spdx_id": license_meta.get("spdx_id") or license_meta.get("key"),
        "name": license_meta.get("name"),
        "api_url": data.get("url"),
        "html_url": data.get("html_url"),
        "path": data.get("path"),
        "sha": data.get("sha"),
        "download_url": data.get("download_url"),
    }


def _github_commit_evidence(repo: str, default_branch: str | None, auth: GitHubAuth) -> dict[str, Any] | None:
    if not default_branch:
        return None
    data = _github_json(f"https://api.github.com/repos/{repo}/commits/{default_branch}", auth)
    if not data:
        return None
    raw_commit = data.get("commit")
    commit: dict[str, Any] = raw_commit if isinstance(raw_commit, dict) else {}
    raw_committer = commit.get("committer")
    committer: dict[str, Any] = raw_committer if isinstance(raw_committer, dict) else {}
    return {
        "ref": default_branch,
        "sha": data.get("sha"),
        "html_url": data.get("html_url"),
        "api_url": data.get("url"),
        "committed_at": committer.get("date"),
    }


def _repo_evidence_for_bundle(
    bundle: SkillBundle,
    *,
    auth: GitHubAuth,
    github_source: GitHubSource | None,
    repo_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    identifier = bundle.identifier
    parts = identifier.split("/", 2)
    if bundle.source != "github" or len(parts) < 3:
        return {
            "repo_url": None,
            "skill_url": None,
            "repo": None,
            "path": None,
            "default_branch": None,
            "head_commit": None,
            "license": None,
            "repo_activity": None,
            "skill_blob": None,
        }

    repo = f"{parts[0]}/{parts[1]}"
    skill_path = parts[2].rstrip("/")
    cached = repo_cache.get(repo)
    if cached is None:
        repo_data = _github_json(f"https://api.github.com/repos/{repo}", auth) or {}
        default_branch = repo_data.get("default_branch") if isinstance(repo_data, dict) else None
        cached = {
            "default_branch": default_branch,
            "license": _github_license_evidence(repo, auth),
            "head_commit": _github_commit_evidence(repo, default_branch, auth),
            "repo_activity": {
                "updated_at": repo_data.get("updated_at"),
                "pushed_at": repo_data.get("pushed_at"),
            },
            "skill_blobs": {},
        }
        if github_source is not None:
            tree = github_source._get_repo_tree(repo)
            if tree is not None:
                _default_branch, entries = tree
                for entry in entries:
                    if entry.get("type") != "blob":
                        continue
                    entry_path = entry.get("path")
                    if not isinstance(entry_path, str) or not entry_path.endswith("/SKILL.md"):
                        continue
                    cached["skill_blobs"][entry_path] = {
                        "path": entry_path,
                        "sha": entry.get("sha"),
                        "url": entry.get("url"),
                    }
        repo_cache[repo] = cached

    skill_md_path = f"{skill_path}/SKILL.md"
    return {
        "repo": repo,
        "path": skill_path,
        "repo_url": f"https://github.com/{repo}",
        "skill_url": f"https://github.com/{repo}/tree/{cached.get('default_branch') or 'main'}/{skill_path}",
        "default_branch": cached.get("default_branch"),
        "head_commit": cached.get("head_commit"),
        "license": cached.get("license"),
        "repo_activity": cached.get("repo_activity"),
        "skill_blob": (cached.get("skill_blobs") or {}).get(skill_md_path),
    }


def _source_evidence_review_codes(evidence: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if evidence.get("repo"):
        if not evidence.get("head_commit"):
            codes.append("missing_commit_evidence")
        if not evidence.get("license"):
            codes.append("missing_license_evidence")
        license_meta = evidence.get("license") or {}
        if license_meta and not license_meta.get("spdx_id"):
            codes.append("license_missing_spdx")
    return codes


def evaluate_bundle(
    bundle: SkillBundle,
    *,
    meta: Any = None,
    source_label: str | None = None,
    github_auth: GitHubAuth | None = None,
    github_source: GitHubSource | None = None,
    repo_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    structure = _bundle_structure(bundle)
    repo_cache = {} if repo_cache is None else repo_cache
    skill_md = _bundle_text(bundle, "SKILL.md")
    frontmatter, _body = _parse_frontmatter(skill_md)
    prerequisites = _skill_prerequisites(frontmatter)
    node_runtime = _node_runtime_signal(bundle)
    source_evidence = _repo_evidence_for_bundle(
        bundle,
        auth=github_auth or GitHubAuth(),
        github_source=github_source,
        repo_cache=repo_cache,
    )
    incompatible_codes: list[str] = []
    review_codes: list[str] = []

    platform_supported = skill_matches_platform(frontmatter)
    environment_supported = skill_matches_environment(frontmatter)
    if not platform_supported:
        incompatible_codes.append("unsupported_platform")
    if not environment_supported:
        review_codes.append("environment_filter_mismatch")
    if prerequisites["missing_required_commands"]:
        review_codes.append("missing_required_commands")
    if prerequisites["missing_required_environment_variables"]:
        review_codes.append("missing_required_environment_variables")
    if node_runtime.fail_closed and node_runtime.reason_code:
        incompatible_codes.append(node_runtime.reason_code)
    elif node_runtime.reason_code:
        review_codes.append(node_runtime.reason_code)
    review_codes.extend(_source_evidence_review_codes(source_evidence))

    with tempfile.TemporaryDirectory(prefix="skills-intake-") as td:
        skill_dir = _write_bundle_to_dir(bundle, Path(td) / bundle.name)
        scan = scan_skill(skill_dir, source=source_label or bundle.identifier)
        allow, allow_reason = should_allow_install(scan)

    if scan.verdict == "dangerous":
        incompatible_codes.append("guard_dangerous")
    elif allow is False:
        review_codes.append("guard_policy_blocked")
    elif allow is None:
        review_codes.append("guard_confirmation_required")

    status = _status_from_reason_sets(incompatible_codes, review_codes)
    skill_name = str(frontmatter.get("name") or getattr(meta, "name", "") or bundle.name)
    description = str(frontmatter.get("description") or getattr(meta, "description", "") or "")
    platforms = frontmatter.get("platforms") if isinstance(frontmatter.get("platforms"), list) else []
    reason_codes = sorted(set(incompatible_codes + review_codes))
    compatibility_score = _compute_compatibility_score(
        reason_codes,
        missing_commands=prerequisites["missing_required_commands"],
        missing_env_vars=prerequisites["missing_required_environment_variables"],
    )

    return {
        "name": skill_name,
        "description": description,
        "identifier": bundle.identifier,
        "source": bundle.source,
        "trust_level": bundle.trust_level,
        "structure": structure,
        "frontmatter": {
            "platforms": platforms,
            "metadata_keys": sorted(frontmatter.keys()),
        },
        "compatibility": {
            "platform_supported": platform_supported,
            "environment_supported": environment_supported,
            "node_runtime": {
                "detected": node_runtime.detected,
                "package_json_present": node_runtime.package_json_present,
                "engine_range": node_runtime.engine_range,
                "minimum_major": node_runtime.minimum_major,
                "fail_closed": node_runtime.fail_closed,
            },
        },
        "source_evidence": source_evidence,
        "prerequisites": prerequisites,
        "security": {
            "verdict": scan.verdict,
            "findings_count": len(scan.findings),
            "findings": _summarize_findings(scan.findings),
            "install_allowed": allow,
            "install_reason": allow_reason,
        },
        "compatibility_score": compatibility_score,
        "evaluation_status": status,
        "reason_codes": reason_codes,
    }


def audit_candidates(
    candidates: Iterable[str],
    *,
    max_skills_per_repo: int = DEFAULT_MAX_SKILLS_PER_REPO,
    sources: Optional[list[Any]] = None,
    github_source: Any = None,
) -> dict[str, Any]:
    auth = GitHubAuth()
    sources = sources or create_source_router(auth)
    if github_source is None:
        github_source = next((src for src in sources if isinstance(src, GitHubSource)), None)
    repo_cache: dict[str, dict[str, Any]] = {}

    results: list[dict[str, Any]] = []
    for raw_candidate in candidates:
        ref = _normalize_candidate(raw_candidate)
        candidate_sources = _filtered_sources_for_candidate(ref, sources)
        identifiers: list[str] = []
        total_discovered = None
        candidate_errors: list[str] = []

        if ref.kind == "github_repo_url":
            if github_source is None or not ref.repo:
                candidate_errors.append("github_source_unavailable")
            else:
                identifiers, total_discovered = _repo_skill_identifiers(
                    ref.repo,
                    github_source,
                    max_skills_per_repo=max_skills_per_repo,
                )
                if not identifiers:
                    candidate_errors.append("no_skill_candidates_discovered")
        elif ref.identifier:
            identifiers = [ref.identifier]
        else:
            candidate_errors.append("unsupported_candidate")

        audited_skills: list[dict[str, Any]] = []
        for identifier in identifiers:
            meta, bundle, _matched_source = _resolve_source_meta_and_bundle(identifier, candidate_sources)
            if not bundle:
                audited_skills.append(
                    {
                        "identifier": identifier,
                        "evaluation_status": "incompatible",
                        "reason_codes": ["bundle_unresolvable"],
                    }
                )
                continue
            audited_skills.append(
                evaluate_bundle(
                    bundle,
                    meta=meta,
                    source_label=identifier,
                    github_auth=auth,
                    github_source=github_source,
                    repo_cache=repo_cache,
                )
            )

        aggregate_reasons = set(candidate_errors)
        aggregate_statuses = ["incompatible"] if candidate_errors and not audited_skills else []
        for item in audited_skills:
            aggregate_statuses.append(item["evaluation_status"])
            aggregate_reasons.update(item.get("reason_codes", []))

        results.append(
            {
                "candidate": raw_candidate,
                "normalized": {
                    "kind": ref.kind,
                    "repo": ref.repo,
                    "identifier": ref.identifier,
                    "url": ref.url,
                    "path": ref.path,
                },
                "skill_discovery": {
                    "discovered_count": total_discovered,
                    "audited_count": len(audited_skills),
                    "max_skills_per_repo": max_skills_per_repo,
                },
                "evaluation_status": _aggregate_statuses(aggregate_statuses),
                "compatibility_score": _aggregate_compatibility_score(audited_skills, candidate_errors),
                "reason_codes": sorted(aggregate_reasons),
                "skills": audited_skills,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "audited_at": _now_iso(),
        "environment": {
            "platform": os.uname().sysname.lower() if hasattr(os, "uname") else os.name,
            "node_on_host": shutil.which("node") is not None,
        },
        "candidate_count": len(list(candidates)) if not isinstance(candidates, list) else len(candidates),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only intake audit for external skill candidates")
    parser.add_argument("candidates", nargs="+", help="skill identifiers or external skill URLs")
    parser.add_argument("--max-skills-per-repo", type=int, default=DEFAULT_MAX_SKILLS_PER_REPO)
    args = parser.parse_args(argv)
    report = audit_candidates(args.candidates, max_skills_per_repo=args.max_skills_per_repo)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
