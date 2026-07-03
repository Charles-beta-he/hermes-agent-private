#!/usr/bin/env python3
"""Build, validate, and install a sanitized private-experience bundle.

The bundle reproduces the owner's Hermes *capability shape* for another user
without copying private memory, secrets, runtime databases, logs, project
profiles, or task evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = REPO_ROOT / "templates" / "private-experience"
MANIFEST_PATH = BUNDLE_ROOT / "manifest.json"

FORBIDDEN_BASENAMES = {
    ".env",
    "auth.json",
    "config.yaml",
    "state.db",
    "sessions.db",
    "route_outcomes.db",
    "kanban.db",
    "missions.db",
    "control_tower.db",
}
FORBIDDEN_PARTS = {
    "logs",
    "auth",
    "cache",
    "checkpoints",
    "sessions",
    "state",
    "profiles",
    "project-profiles",
    "memories",
    "memory",
    "taskpool_evidence",
    "terminal-sessions",
    "docs-private",
    "private-docs",
}
FORBIDDEN_TEXT_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|refresh[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-]{12,}"),
    re.compile(r"/Users/charles(/|\b)"),
    re.compile(r"Charles-beta-he/(?!hermes-agent-private)"),
    re.compile(r"贺程"),
    re.compile(r"(?i)bot[_-]?token\s*[:=]"),
    re.compile(r"(?i)chat[_-]?id\s*[:=]\s*-?\d{5,}"),
]
PLACEHOLDER_ALLOWLIST = {
    "$HERMES_HOME",
    "YOUR_API_KEY_HERE",
    "example-project",
    "example-user",
    "example.invalid",
}


@dataclass(frozen=True)
class BundleFile:
    path: str
    purpose: str
    installed_to: str


BUNDLE_FILES = [
    BundleFile("README.md", "operator guide for the sanitized experience pack", "$HERMES_HOME/templates/private-experience/README.md"),
    BundleFile("config.example.yaml", "non-secret feature-shape config template", "$HERMES_HOME/templates/private-experience/config.example.yaml"),
    BundleFile("hermes-agent-persona.example.md", "persona style template with no personal memory", "$HERMES_HOME/templates/private-experience/hermes-agent-persona.example.md"),
    BundleFile("user-profile.schema.json", "user profile schema; not a profile export", "$HERMES_HOME/templates/private-experience/user-profile.schema.json"),
    BundleFile("user-profile.example.json", "replaceable example user profile", "$HERMES_HOME/templates/private-experience/user-profile.example.json"),
    BundleFile("project-profile.schema.json", "project-profile schema; no project facts", "$HERMES_HOME/templates/private-experience/project-profile.schema.json"),
    BundleFile("project-profile.example.json", "project-profile example without project facts", "$HERMES_HOME/templates/private-experience/project-profile.example.json"),
    BundleFile("ssot.template.md", "project SSOT template", "$HERMES_HOME/templates/private-experience/ssot.template.md"),
    BundleFile("evidence-ledger.template.json", "claim/evidence ledger template", "$HERMES_HOME/templates/private-experience/evidence-ledger.template.json"),
    BundleFile("receipt.template.json", "side-effect receipt/readback template", "$HERMES_HOME/templates/private-experience/receipt.template.json"),
    BundleFile("authority-matrix.template.yaml", "default-off authority matrix", "$HERMES_HOME/templates/private-experience/authority-matrix.template.yaml"),
    BundleFile("side-effect-gates.template.yaml", "side-effect gate outcomes", "$HERMES_HOME/templates/private-experience/side-effect-gates.template.yaml"),
    BundleFile("agent-template-profile.example.json", "governed local-only agent template profile", "$HERMES_HOME/templates/private-experience/agent-template-profile.example.json"),
    BundleFile("taskpool-task.template.yaml", "taskpool task template with evidence gates", "$HERMES_HOME/templates/private-experience/taskpool-task.template.yaml"),
    BundleFile("cron.examples.md", "scheduled-job examples that require explicit user targets", "$HERMES_HOME/templates/private-experience/cron.examples.md"),
    BundleFile("gateway.example.yaml.disabled", "disabled external-delivery example", "$HERMES_HOME/templates/private-experience/gateway.example.yaml.disabled"),
    BundleFile("memory-policy.md", "durable-memory landing-zone policy", "$HERMES_HOME/templates/private-experience/memory-policy.md"),
    BundleFile("evidence-first-completion.md", "fresh-evidence completion rule", "$HERMES_HOME/templates/private-experience/evidence-first-completion.md"),
    BundleFile("capability-graph-primer.md", "workflow/capability graph default abstraction", "$HERMES_HOME/templates/private-experience/capability-graph-primer.md"),
    BundleFile("post-install-checklist.md", "manual checklist to reach strong capability coverage", "$HERMES_HOME/templates/private-experience/post-install-checklist.md"),
]


def build_manifest() -> dict[str, Any]:
    return {
        "name": "private-hermes-sanitized-experience-pack",
        "version": 2,
        "package": "capability_shape",
        "exclude": "lived_state",
        "overwrite_existing": False,
        "default_enabled": False,
        "policy": "templates_only_no_secrets_no_memory_no_runtime_state",
        "source_model": {
            "copied": [
                "safe templates",
                "capability-shape documentation",
                "post-install checklist",
                "example config structures with placeholders",
                "replaceable persona/profile/project schemas",
                "default-off side-effect gates",
            ],
            "not_copied": [
                "live config.yaml or .env",
                "auth tokens and OAuth sessions",
                "memory/user profile/session databases",
                "project-profiles with real client/project facts",
                "logs/checkpoints/runtime state/task evidence",
                "local brain vault or private notes",
                "gateway chat IDs or delivery targets",
            ],
        },
        "activation_gates": {
            "cron": "manual_enable",
            "gateway_delivery": "manual_configure",
            "shell_hooks": "user_approval_required",
            "taskpool_mutation": "explicit_authority",
            "external_write": "exact_target_required",
            "publish_deploy_push": "exact_target_rollback_receipt_required",
            "destructive": "exact_target_backup_readback_required",
            "credentials": "never_agent_typed",
            "broad_fanout": "exact_allowlist_required",
        },
        "files": [file.__dict__ for file in BUNDLE_FILES],
    }


def _iter_bundle_files() -> list[Path]:
    if not BUNDLE_ROOT.exists():
        return []
    return [p for p in BUNDLE_ROOT.rglob("*") if p.is_file()]


def _scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    rel = path.relative_to(BUNDLE_ROOT)
    parts = set(rel.parts)
    if path.name in FORBIDDEN_BASENAMES:
        findings.append(f"forbidden basename: {rel}")
    if parts & FORBIDDEN_PARTS:
        findings.append(f"forbidden path part: {rel}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    for pattern in FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(text):
            findings.append(f"forbidden text pattern {pattern.pattern!r}: {rel}")
    return findings


def validate_bundle() -> dict[str, Any]:
    findings: list[str] = []
    files = _iter_bundle_files()
    for path in files:
        findings.extend(_scan_file(path))
    manifest = build_manifest()
    expected = {f.path for f in BUNDLE_FILES} | {"manifest.json"}
    present = {str(p.relative_to(BUNDLE_ROOT)) for p in files}
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    if missing:
        findings.append("missing expected files: " + ", ".join(missing))
    if manifest["default_enabled"] is not False:
        findings.append("manifest default_enabled must remain false")
    if manifest["overwrite_existing"] is not False:
        findings.append("manifest overwrite_existing must remain false")
    return {
        "status": "ok" if not findings else "failed",
        "bundle_root": str(BUNDLE_ROOT),
        "file_count": len(files),
        "missing": missing,
        "extra": extra,
        "findings": findings,
        "manifest": manifest,
    }


def write_manifest() -> None:
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install_bundle(target_home: Path, dry_run: bool = False) -> dict[str, Any]:
    result = validate_bundle()
    if result["status"] != "ok":
        return {"status": "failed", "reason": "bundle_validation_failed", "findings": result["findings"]}
    dst = target_home / "templates" / "private-experience"
    actions: list[dict[str, str]] = []
    for file in BUNDLE_FILES + [BundleFile("manifest.json", "manifest", "$HERMES_HOME/templates/private-experience/manifest.json")]:
        src = BUNDLE_ROOT / file.path
        target = dst / file.path
        if target.exists():
            actions.append({"action": "skip", "path": str(target)})
            continue
        actions.append({"action": "copy", "path": str(target)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    receipt = target_home / "install-receipts" / "private-experience-pack.json"
    receipt_payload = {
        "pack": build_manifest()["name"],
        "default_enabled": False,
        "side_effects_enabled": False,
        "installed_to": str(dst),
        "actions": actions,
    }
    if not dry_run:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(receipt_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"status": "ok", "target": str(dst), "receipt": str(receipt), "actions": actions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--install-to", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.write_manifest:
        write_manifest()
    if args.install_to:
        result = install_bundle(args.install_to, dry_run=args.dry_run)
    else:
        result = validate_bundle()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result["status"])
        for finding in result.get("findings", []):
            print(f"- {finding}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
