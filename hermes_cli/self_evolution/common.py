"""Shared constants and helpers for Hermes self-evolution absorption."""

from __future__ import annotations

import hashlib
from pathlib import Path

SCHEMA_VERSION = "hermes.self_evolution.proposal_doctor.v1"
DEFAULT_EVIDENCE = Path(
    "/Users/charles/wechat_video_analysis/project_absorption/"
    "raven-everos-absorption-ssot/evidence/upstream-self-evolution-benchmark.json"
)
REFERENCE_ADDENDUM = (
    "Reference addendum: `references/self-evolution-proposal-boundary.md` captures the "
    "default-off Raven/EverOS self-evolution absorption boundary: consume isolated "
    "benchmark evidence, emit proposal-only receipts, keep direct memory/skill writes "
    "closed, and require a separate promotion gate before applying generated skill diffs."
)


def execution_boundary() -> dict[str, bool]:
    return {
        "runtime_mutation": False,
        "external_dispatch": False,
        "daemon_started": False,
        "cron_created": False,
        "credential_read": False,
        "direct_memory_write": False,
        "direct_skill_write": False,
        "auto_promote": False,
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
