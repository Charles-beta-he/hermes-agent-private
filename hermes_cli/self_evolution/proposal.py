"""Proposal-only self-evolution evidence projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import DEFAULT_EVIDENCE, SCHEMA_VERSION, execution_boundary


def _proposal_packet(evidence_path: Path, *, capability_level: str) -> dict[str, Any]:
    return {
        "kind": "self_evolution_capability_proposal",
        "landing_zone": "experience_ratchet_proposal",
        "promotion_target": "skill_or_memory_after_separate_gate",
        "capability_level": capability_level,
        "evidence_path": str(evidence_path),
        "auto_promote": False,
        "required_next_gate": "separate_authority_for_direct_skill_or_memory_write",
    }


def _counts_from_evidence(data: dict[str, Any]) -> dict[str, int]:
    readback = data.get("authorized_test_final_readback") or {}
    artifacts = readback.get("qwen25_mxbai_artifacts") or {}
    counts = artifacts.get("counts") or {}
    return {
        "user_profiles": int(counts.get("user_profiles") or 0),
        "agent_cases": int(counts.get("agent_cases") or 0),
        "agent_skills": int(counts.get("agent_skills") or 0),
    }


def _strict_claims(data: dict[str, Any]) -> dict[str, bool]:
    readback = data.get("authorized_test_final_readback") or {}
    strict = readback.get("strict_claims") or {}
    claims = data.get("claims") or {}
    return {
        "real_user_memory_roundtrip_proven": bool(
            strict.get("real_user_memory_roundtrip_proven")
            or claims.get("authorized_test_real_user_memory_roundtrip_proven")
        ),
        "agent_case_pipeline_proven": bool(
            strict.get("agent_case_pipeline_proven")
            or claims.get("authorized_test_agent_case_pipeline_proven")
        ),
        "agent_skill_skill_md_proven": bool(
            strict.get("agent_skill_skil_md_proven")
            or strict.get("agent_skill_skill_md_proven")
            or claims.get("authorized_test_agent_skill_skill_md_proven")
        ),
        "no_benchmark_daemon_retained": bool(strict.get("no_benchmark_daemon_retained")),
    }


def _capability_level(claims: dict[str, bool]) -> str:
    if claims["agent_skill_skill_md_proven"]:
        return "agent_skill_artifact_proven"
    if claims["real_user_memory_roundtrip_proven"] and claims["agent_case_pipeline_proven"]:
        return "memory_roundtrip_and_agent_case_proven"
    if claims["real_user_memory_roundtrip_proven"]:
        return "memory_roundtrip_proven"
    return "not_proven"


def _safe_off_report(path: Path, *, status: str, error: str | None = None) -> dict[str, Any]:
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": "proposal_only",
        "status": status,
        "evidence_path": str(path),
        "execution": execution_boundary(),
        "capability_level": "not_proven",
        "proposal_packet": _proposal_packet(path, capability_level="not_proven"),
        "readiness": {
            "ready_for_default_off_local_harness": False,
            "ready_for_direct_skill_write": False,
            "ready_for_direct_memory_write": False,
        },
    }
    if error is not None:
        report["error"] = error
    else:
        report["next_actions"] = [
            "run isolated Raven/EverOS benchmark first or pass --self-evolution-evidence",
        ]
    return report


def build_self_evolution_report(evidence_path: str | Path | None = None) -> dict[str, Any]:
    """Build a side-effect-free self-evolution readiness projection."""
    path = Path(evidence_path).expanduser() if evidence_path else DEFAULT_EVIDENCE
    if not path.exists():
        return _safe_off_report(path, status="missing_evidence")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _safe_off_report(path, status="invalid_evidence", error=f"{type(exc).__name__}: {exc}")

    claims = _strict_claims(data)
    counts = _counts_from_evidence(data)
    level = _capability_level(claims)
    ready_local = level in {
        "memory_roundtrip_proven",
        "memory_roundtrip_and_agent_case_proven",
        "agent_skill_artifact_proven",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "proposal_only",
        "status": "ok" if ready_local else "not_ready",
        "evidence_path": str(path),
        "execution": execution_boundary(),
        "capability_level": level,
        "counts": counts,
        "claims": claims,
        "proposal_packet": _proposal_packet(path, capability_level=level),
        "readiness": {
            "ready_for_default_off_local_harness": ready_local,
            "ready_for_direct_skill_write": False,
            "ready_for_direct_memory_write": False,
        },
        "next_actions": [
            "review proposal packet in experience-ratchet lane",
            "open separate exact gate before any direct skill or memory write",
        ],
    }


def materialize_self_evolution_proposal(
    *,
    evidence_path: str | Path | None = None,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write a local proposal receipt without promoting it to memory/skills."""
    report = build_self_evolution_report(evidence_path=evidence_path)
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "hermes.self_evolution.proposal_receipt.v1",
        "mode": "proposal_only",
        "status": report["status"],
        "evidence_path": report["evidence_path"],
        "execution": report["execution"],
        "capability_level": report["capability_level"],
        "counts": report.get("counts", {}),
        "claims": report.get("claims", {}),
        "proposal_packet": report["proposal_packet"],
        "readiness": report["readiness"],
    }
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"written": True, "output_path": str(output), "status": receipt["status"]}


def print_self_evolution_report(*, as_json: bool = False, evidence_path: str | Path | None = None) -> None:
    report = build_self_evolution_report(evidence_path=evidence_path)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"Hermes self-evolution doctor: {report['status']}")
    print(f"  mode: {report['mode']}")
    print(f"  capability_level: {report['capability_level']}")
    print(f"  evidence: {report['evidence_path']}")
    print("  execution: runtime_mutation=false external_dispatch=false direct_memory_write=false direct_skill_write=false")
