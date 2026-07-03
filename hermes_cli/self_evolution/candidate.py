"""Candidate-pack generation for self-evolution absorption."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import REFERENCE_ADDENDUM
from .proposal import build_self_evolution_report


def _dry_run_skill_diff(*, capability_level: str, evidence_path: str) -> dict[str, Any]:
    preview = "\n".join(
        [
            "```diff",
            "--- a/skills/autonomous-ai-agents/hermes-agent/SKILL.md",
            "+++ b/skills/autonomous-ai-agents/hermes-agent/SKILL.md",
            "@@",
            f"+{REFERENCE_ADDENDUM}",
            "```",
        ]
    )
    return {
        "target_skill": "hermes-agent",
        "target_reference": "references/self-evolution-proposal-boundary.md",
        "write_enabled": False,
        "capability_level": capability_level,
        "evidence_path": evidence_path,
        "preview": preview,
        "instructions": [
            "Review this diff preview before any skill write.",
            "Open a separate exact promotion gate before applying to SKILL.md or references/.",
        ],
    }


def build_self_evolution_candidate_pack(evidence_path: str | Path | None = None) -> dict[str, Any]:
    """Build a proposal-only candidate pack for the experience-ratchet lane."""
    report = build_self_evolution_report(evidence_path=evidence_path)
    level = report["capability_level"]
    return {
        "schema_version": "hermes.self_evolution.candidate_pack.v1",
        "mode": "proposal_only",
        "status": report["status"],
        "execution": report["execution"],
        "evidence_path": report["evidence_path"],
        "capability_level": level,
        "experience_atom": {
            "status": "promoted_candidate",
            "landing_zone": "skill",
            "claim": (
                "Hermes can consume Raven/EverOS self-evolution benchmark evidence "
                "into a default-off proposal lane with memory roundtrip and agent-case proof."
            ),
            "applicability": [
                "Hermes self-evolution absorption",
                "isolated Raven/EverOS benchmark evidence",
                "proposal-only experience-ratchet lane",
            ],
            "anti_conditions": [
                "Do not direct-write Hermes memory or skills from this candidate pack.",
                "Do not claim agent skill SKILL.md is proven unless evidence says agent_skill_skill_md_proven=true.",
            ],
            "falsifiers": [
                "If agent skill SKILL.md extraction remains unproven, this candidate cannot promote direct skill automation.",
                "If evidence lacks no-daemon readback, this candidate cannot enable runtime scheduling.",
            ],
            "evidence_refs": [report["evidence_path"]],
            "counts": report.get("counts", {}),
            "claims": report.get("claims", {}),
        },
        "dry_run_skill_diff": _dry_run_skill_diff(
            capability_level=level,
            evidence_path=report["evidence_path"],
        ),
    }


def materialize_self_evolution_candidate_pack(
    *,
    evidence_path: str | Path | None = None,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write a candidate pack without writing memory or skills."""
    pack = build_self_evolution_candidate_pack(evidence_path=evidence_path)
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "written": True,
        "output_path": str(output),
        "status": pack["status"],
        "kind": "candidate_pack",
    }
