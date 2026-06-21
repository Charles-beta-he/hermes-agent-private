"""Project Portfolio OS inspection and verification helpers.

This module is intentionally read-only by default. It treats project profiles,
progress ledgers, and the portfolio registry as operator metadata; it never
modifies product/project code.
"""
from __future__ import annotations

import json
import stat
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

REQUIRED_PROFILE_FIELDS = [
    "project",
    "project_type",
    "ssot_root",
    "progress_ledger",
    "portfolio_profile",
    "promotion_policy",
    "relations",
    "current_components",
]
MAX_LEDGER_BYTES = 1_000_000
MAX_JSON_BYTES = 2_000_000

VALID_RELATIONS = {
    "same_product_as",
    "platform_for",
    "edge_runtime_of",
    "integration_boundary_for",
    "may_use_integration_boundary",
    "shares_module",
    "supersedes",
    "sibling",
    "evidence_for",
    "managed_by_agent_workflow",
    "knowledge_control_plane_for",
    "hosts_knowledge_control_plane",
    "governed_by",
}


@dataclass
class Finding:
    severity: str
    project: str
    check: str
    message: str


def _home(home: str | Path | None = None) -> Path:
    if home is None:
        return get_hermes_home()
    return Path(home).expanduser()


def default_paths(home: str | Path | None = None) -> dict[str, Path]:
    base = _home(home)
    root = base / "workflows" / "portfolio" / "project-portfolio-os"
    return {
        "home": base,
        "profile_dir": base / "project-profiles",
        "root": root,
        "registry": root / "01-registry" / "portfolio-registry.json",
        "latest_report": root / "05-status" / "portfolio-doctor-latest.json",
        "status": root / "05-status" / "portfolio-status.md",
        "production_receipt": root / "05-status" / "production-receipt.md",
    }


def _safe_read_json(path: Path) -> tuple[str | None, str | None]:
    try:
        st = path.stat()
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not stat.S_ISREG(st.st_mode):
        return None, "not a regular file"
    if st.st_size > MAX_JSON_BYTES:
        return None, f"JSON file too large: {st.st_size} bytes > {MAX_JSON_BYTES}"
    try:
        return path.read_text(encoding="utf-8"), None
    except Exception as exc:  # pragma: no cover - defensive CLI path
        return None, f"{type(exc).__name__}: {exc}"


def _load_json(path: Path) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    text, error = _safe_read_json(path)
    if error:
        return None, error
    try:
        return json.loads(text or ""), None
    except Exception as exc:  # pragma: no cover - defensive CLI path
        return None, f"{type(exc).__name__}: {exc}"


def _check_str_field(data: dict[str, Any], pid: str, field: str, findings: list[Finding]) -> str | None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        findings.append(Finding("error", pid, field, f"{field} must be a non-empty string"))
        return None
    return value


def _check_bool_field(data: dict[str, Any], pid: str, field: str, findings: list[Finding]) -> None:
    if not isinstance(data.get(field), bool):
        findings.append(Finding("error", pid, field, f"{field} must be boolean"))


def _check_dict_field(data: dict[str, Any], pid: str, field: str, findings: list[Finding]) -> None:
    if not isinstance(data.get(field), dict):
        findings.append(Finding("error", pid, field, f"{field} must be an object"))


def _validate_relations(relations: Any, pid: str, project_ids: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(relations, list):
        return [Finding("error", pid, "relations", "relations must be a list")]
    for index, rel in enumerate(relations):
        if not isinstance(rel, dict):
            findings.append(Finding("error", pid, "relations", f"relation[{index}] must be an object"))
            continue
        target = rel.get("target")
        rtype = rel.get("relation")
        if not isinstance(target, str) or not target.strip():
            findings.append(Finding("error", pid, "relation_target", f"relation[{index}].target must be a non-empty string"))
        elif target not in project_ids:
            findings.append(Finding("warning", pid, "relation_target", f"target not in profiles: {target}"))
        if not isinstance(rtype, str) or not rtype.strip():
            findings.append(Finding("error", pid, "relation_type", f"relation[{index}].relation must be a non-empty string"))
        elif rtype not in VALID_RELATIONS:
            findings.append(Finding("warning", pid, "relation_type", f"unknown relation type: {rtype}"))
        merge_policy = rel.get("merge_policy")
        if not isinstance(merge_policy, str) or not merge_policy.strip():
            findings.append(Finding("error", pid, "merge_policy", f"relation[{index}].merge_policy must be a non-empty string"))
    return findings


def _safe_read_ledger(path: Path) -> tuple[str | None, str | None]:
    try:
        st = path.stat()
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not stat.S_ISREG(st.st_mode):
        return None, "not a regular file"
    if st.st_size > MAX_LEDGER_BYTES:
        return None, f"ledger too large: {st.st_size} bytes > {MAX_LEDGER_BYTES}"
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), None
    except OSError as exc:
        return None, f"cannot read progress_ledger: {exc}"


def load_profiles(home: str | Path | None = None) -> dict[str, dict[str, Any]]:
    paths = default_paths(home)
    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(paths["profile_dir"].glob("*.json")):
        data, error = _load_json(path)
        if error or not isinstance(data, dict):
            profiles[path.stem] = {"project": path.stem, "_path": str(path), "_json_error": error or "not an object"}
            continue
        pid_raw = data.get("project")
        pid = pid_raw if isinstance(pid_raw, str) and pid_raw.strip() else path.stem
        item = dict(data)
        item["_path"] = str(path)
        profiles[pid] = item
    return profiles


def _check_profile(path: Path, project_ids: set[str]) -> list[Finding]:
    data, error = _load_json(path)
    pid = path.stem
    findings: list[Finding] = []
    if error or not isinstance(data, dict):
        return [Finding("error", pid, "json", error or "profile JSON must be an object")]
    raw_project = data.get("project")
    if isinstance(raw_project, str) and raw_project.strip():
        pid = raw_project
    else:
        findings.append(Finding("error", pid, "project", "project must be a non-empty string"))

    for field in REQUIRED_PROFILE_FIELDS:
        if field not in data:
            findings.append(Finding("error", pid, "required_field", f"missing {field}"))

    project_type = _check_str_field(data, pid, "project_type", findings)
    ssot_raw = _check_str_field(data, pid, "ssot_root", findings)
    ledger_raw = _check_str_field(data, pid, "progress_ledger", findings)
    promo = _check_str_field(data, pid, "promotion_policy", findings) or ""
    _check_bool_field(data, pid, "portfolio_profile", findings)
    _check_dict_field(data, pid, "current_components", findings)

    if project_type and not project_type.strip():  # defensive, _check_str_field already handles this
        findings.append(Finding("error", pid, "project_type", "project_type must not be blank"))

    ssot = Path(ssot_raw).expanduser() if ssot_raw else None
    if not ssot or not ssot.exists():
        findings.append(Finding("error", pid, "ssot_root", f"missing or nonexistent ssot_root: {ssot_raw!r}"))

    ledger = Path(ledger_raw).expanduser() if ledger_raw else None
    if not ledger or not ledger.exists():
        findings.append(Finding("error", pid, "progress_ledger", f"missing or nonexistent progress_ledger: {ledger_raw!r}"))
    else:
        ledger_text, ledger_error = _safe_read_ledger(ledger)
        if ledger_error:
            findings.append(Finding("error", pid, "progress_ledger", ledger_error))
        elif ledger_text is not None and "Progress Ledger" not in ledger_text:
            findings.append(Finding("warning", pid, "progress_ledger", "ledger does not contain title marker"))

    if "evidence" not in promo.lower() or "ratchet" not in promo.lower():
        findings.append(Finding("warning", pid, "promotion_policy", "promotion_policy should mention evidence and ratchet"))

    findings.extend(_validate_relations(data.get("relations"), pid, project_ids))

    if pid == "uvisa-app-2.0":
        guard = data.get("portfolio_code_guard")
        if not isinstance(guard, str) or "Do not modify UVISA project code" not in guard:
            findings.append(Finding("error", pid, "uvisa_code_guard", "missing portfolio code guard"))

    return findings


def _profile_project_id(path: Path) -> tuple[str | None, str | None]:
    data, error = _load_json(path)
    if error or not isinstance(data, dict):
        return None, error or "profile JSON must be an object"
    project = data.get("project")
    if not isinstance(project, str) or not project.strip():
        return None, "project must be a non-empty string"
    return project, None


def _check_registry(registry: dict[str, Any], project_ids: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    projects = registry.get("projects")
    if not isinstance(projects, list):
        findings.append(Finding("error", "portfolio", "registry_projects", "registry.projects must be a list"))
        registry_ids: set[str] = set()
    else:
        registry_ids = set()
        for index, item in enumerate(projects):
            if not isinstance(item, dict):
                findings.append(Finding("error", "portfolio", "registry_projects", f"projects[{index}] must be an object"))
                continue
            pid = item.get("id")
            if not isinstance(pid, str) or not pid.strip():
                findings.append(Finding("error", "portfolio", "registry_projects", f"projects[{index}].id must be a non-empty string"))
                continue
            if pid in registry_ids:
                findings.append(Finding("error", pid, "registry_projects", "duplicate registry project id"))
            registry_ids.add(pid)

    for pid in sorted(project_ids - registry_ids):
        findings.append(Finding("error", pid, "registry_membership", "profile project missing from portfolio registry"))

    relations = registry.get("relations", [])
    if not isinstance(relations, list):
        findings.append(Finding("error", "portfolio", "registry_relations", "registry.relations must be a list"))
        return findings
    for index, rel in enumerate(relations):
        if not isinstance(rel, dict):
            findings.append(Finding("error", "portfolio", "registry_relation", f"relations[{index}] must be an object"))
            continue
        src = rel.get("from")
        target = rel.get("target")
        rtype = rel.get("relation")
        if not isinstance(src, str) or not src.strip():
            findings.append(Finding("error", "portfolio", "registry_relation_source", f"relations[{index}].from must be a non-empty string"))
        elif src not in project_ids:
            findings.append(Finding("warning", src, "registry_relation_source", "source not in profiles"))
        if not isinstance(target, str) or not target.strip():
            findings.append(Finding("error", str(src), "registry_relation_target", f"relations[{index}].target must be a non-empty string"))
        elif target not in project_ids:
            findings.append(Finding("warning", str(src), "registry_relation_target", f"target not in profiles: {target}"))
        if not isinstance(rtype, str) or not rtype.strip():
            findings.append(Finding("error", str(src), "registry_relation_type", f"relations[{index}].relation must be a non-empty string"))
        elif rtype not in VALID_RELATIONS:
            findings.append(Finding("warning", str(src), "registry_relation_type", f"unknown relation type: {rtype}"))
    return findings


def run_doctor(home: str | Path | None = None) -> dict[str, Any]:
    paths = default_paths(home)
    profile_paths = sorted(paths["profile_dir"].glob("*.json"))
    project_ids: set[str] = set()
    seen_project_paths: dict[str, Path] = {}
    findings: list[Finding] = []

    if not paths["profile_dir"].exists():
        findings.append(Finding("error", "portfolio", "profile_dir", f"missing profile dir: {paths['profile_dir']}"))

    for path in profile_paths:
        project, error = _profile_project_id(path)
        if error:
            findings.append(Finding("error", path.stem, "project", error))
            continue
        assert project is not None
        if project in seen_project_paths:
            findings.append(
                Finding(
                    "error",
                    project,
                    "duplicate_project_id",
                    f"duplicate project id in {path} and {seen_project_paths[project]}",
                )
            )
        seen_project_paths[project] = path
        project_ids.add(project)

    for path in profile_paths:
        findings.extend(_check_profile(path, project_ids))

    registry, error = _load_json(paths["registry"])
    if error or not isinstance(registry, dict):
        findings.append(Finding("error", "portfolio", "registry_json", error or "registry JSON must be an object"))
    else:
        findings.extend(_check_registry(registry, project_ids))

    counts = {
        "error": sum(1 for f in findings if f.severity == "error"),
        "warning": sum(1 for f in findings if f.severity == "warning"),
        "info": sum(1 for f in findings if f.severity == "info"),
    }
    return {
        "ok": counts["error"] == 0 and counts["warning"] == 0,
        "home": str(paths["home"]),
        "profile_count": len(profile_paths),
        "counts": counts,
        "findings": [asdict(f) for f in findings],
    }


def portfolio_status(home: str | Path | None = None) -> dict[str, Any]:
    paths = default_paths(home)
    profiles = load_profiles(home)
    latest_report: dict[str, Any] | None = None
    if paths["latest_report"].exists():
        report, error = _load_json(paths["latest_report"])
        if isinstance(report, dict) and not error:
            latest_report = report
    doctor = run_doctor(home)
    return {
        "ok": doctor.get("ok", False),
        "checked_at": datetime.now().astimezone().isoformat(),
        "home": str(paths["home"]),
        "registry": str(paths["registry"]),
        "profile_count": len(profiles),
        "projects": [
            {
                "id": pid,
                "type": profile.get("project_type"),
                "progress_ledger": profile.get("progress_ledger"),
                "production_status": profile.get("production_status"),
            }
            for pid, profile in sorted(profiles.items())
        ],
        "doctor": doctor,
        "latest_report": latest_report,
    }


def strict_verify(home: str | Path | None = None) -> dict[str, Any]:
    paths = default_paths(home)
    required = [
        paths["root"] / "00-model" / "portfolio-os.md",
        paths["registry"],
        paths["root"] / "02-literature" / "core-works-and-arguments.md",
        paths["root"] / "03-governance" / "merge-and-abstraction-governance.md",
        paths["root"] / "04-merge-candidates" / "initial-project-relations.md",
        paths["status"],
        paths["root"] / "progress-ledger.md",
        paths["production_receipt"],
    ]
    findings: list[Finding] = []
    for path in required:
        if not path.exists():
            findings.append(Finding("error", "portfolio", "required_path", f"missing {path}"))
    doctor = run_doctor(home)
    if not doctor.get("ok"):
        findings.append(Finding("error", "portfolio", "doctor", "portfolio doctor is not ok"))
    profiles = load_profiles(home)
    uvisa = profiles.get("uvisa-app-2.0", {})
    if "Do not modify UVISA project code" not in str(uvisa.get("portfolio_code_guard", "")):
        findings.append(Finding("error", "uvisa-app-2.0", "uvisa_code_guard", "missing UVISA code guard"))
    counts = {
        "error": sum(1 for f in findings if f.severity == "error"),
        "warning": sum(1 for f in findings if f.severity == "warning"),
        "info": 0,
    }
    return {
        "ok": counts["error"] == 0,
        "counts": counts,
        "findings": [asdict(f) for f in findings],
        "profile_count": len(profiles),
        "doctor": doctor,
    }


def format_summary(payload: dict[str, Any]) -> str:
    ok = "OK" if payload.get("ok") else "NOT OK"
    lines = [f"Portfolio OS: {ok}"]
    if "profile_count" in payload:
        lines.append(f"Profiles: {payload['profile_count']}")
    doctor = payload.get("doctor") if isinstance(payload.get("doctor"), dict) else payload
    counts = doctor.get("counts", {}) if isinstance(doctor, dict) else {}
    if counts:
        lines.append(f"Findings: errors={counts.get('error', 0)} warnings={counts.get('warning', 0)} info={counts.get('info', 0)}")
    findings = doctor.get("findings", []) if isinstance(doctor, dict) else []
    for finding in findings[:20]:
        if isinstance(finding, dict):
            lines.append(f"- {finding.get('severity')} {finding.get('project')}::{finding.get('check')} — {finding.get('message')}")
    if len(findings) > 20:
        lines.append(f"... {len(findings) - 20} more findings")
    return "\n".join(lines)
