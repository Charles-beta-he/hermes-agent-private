#!/usr/bin/env python3
"""Build and validate a sanitized private-experience bundle.

The bundle is for reproducing Charles' Hermes *capability shape* for another
user without copying Charles' private memory, secrets, runtime databases, logs,
project profiles, or task evidence.
"""
from __future__ import annotations

import argparse
import json
import re
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
}
FORBIDDEN_TEXT_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|refresh[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-]{12,}"),
    re.compile(r"/Users/charles(/|\b)"),
    re.compile(r"Charles-beta-he/(?!hermes-agent-private)"),
]


@dataclass(frozen=True)
class BundleFile:
    path: str
    purpose: str
    installed_to: str


BUNDLE_FILES = [
    BundleFile("README.md", "operator guide for the sanitized experience pack", "$HERMES_HOME/templates/private-experience/README.md"),
    BundleFile("config.example.yaml", "non-secret feature-shape config template", "$HERMES_HOME/templates/private-experience/config.example.yaml"),
    BundleFile("hermes-agent-persona.example.md", "persona style template with no personal memory", "$HERMES_HOME/templates/private-experience/hermes-agent-persona.example.md"),
    BundleFile("project-profile.example.json", "project-profile schema example without project facts", "$HERMES_HOME/templates/private-experience/project-profile.example.json"),
    BundleFile("cron.examples.md", "scheduled-job examples that require explicit user targets", "$HERMES_HOME/templates/private-experience/cron.examples.md"),
    BundleFile("post-install-checklist.md", "manual checklist to reach Charles-like capability coverage", "$HERMES_HOME/templates/private-experience/post-install-checklist.md"),
]


def build_manifest() -> dict[str, Any]:
    return {
        "name": "charles-private-hermes-sanitized-experience-pack",
        "version": 1,
        "policy": "templates_only_no_secrets_no_memory_no_runtime_state",
        "source_model": {
            "copied": [
                "safe templates",
                "capability-shape documentation",
                "post-install checklist",
                "example config structures with placeholders",
            ],
            "not_copied": [
                "live config.yaml or .env",
                "auth tokens and OAuth sessions",
                "memory/user profile/session databases",
                "project-profiles with real client/project facts",
                "logs/checkpoints/runtime state/task evidence",
                "local brain vault or private notes",
            ],
        },
        "files": [file.__dict__ for file in BUNDLE_FILES],
    }


def _iter_bundle_files() -> list[Path]:
    if not BUNDLE_ROOT.exists():
        return []
    return [p for p in BUNDLE_ROOT.rglob("*") if p.is_file()]


def validate_bundle() -> dict[str, Any]:
    findings: list[str] = []
    files = _iter_bundle_files()
    for path in files:
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
    manifest = build_manifest()
    expected = {f.path for f in BUNDLE_FILES} | {"manifest.json"}
    present = {str(p.relative_to(BUNDLE_ROOT)) for p in files}
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    if missing:
        findings.append("missing expected files: " + ", ".join(missing))
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.write_manifest:
        write_manifest()
    result = validate_bundle()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result["status"])
        for finding in result["findings"]:
            print(f"- {finding}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
