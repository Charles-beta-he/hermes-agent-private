"""Default-off, proposal-only self-evolution doctor surface.

This module consumes local Raven/EverOS benchmark evidence and projects it into a
Hermes-governed proposal packet. It intentionally does not start runtimes, read
credentials, write memory, write skills, dispatch externally, schedule cron, or
promote proposals. Promotion remains a separate gate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "hermes.self_evolution.proposal_doctor.v1"
_DEFAULT_EVIDENCE = Path(
    "/Users/charles/wechat_video_analysis/project_absorption/"
    "raven-everos-absorption-ssot/evidence/upstream-self-evolution-benchmark.json"
)


def _execution_boundary() -> dict[str, bool]:
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


def build_self_evolution_report(evidence_path: str | Path | None = None) -> dict[str, Any]:
    """Build a side-effect-free self-evolution readiness projection."""
    path = Path(evidence_path).expanduser() if evidence_path else _DEFAULT_EVIDENCE
    execution = _execution_boundary()
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "proposal_only",
            "status": "missing_evidence",
            "evidence_path": str(path),
            "execution": execution,
            "capability_level": "not_proven",
            "proposal_packet": _proposal_packet(path, capability_level="not_proven"),
            "readiness": {
                "ready_for_default_off_local_harness": False,
                "ready_for_direct_skill_write": False,
                "ready_for_direct_memory_write": False,
            },
            "next_actions": [
                "run isolated Raven/EverOS benchmark first or pass --self-evolution-evidence",
            ],
        }

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "proposal_only",
            "status": "invalid_evidence",
            "evidence_path": str(path),
            "error": f"{type(exc).__name__}: {exc}",
            "execution": execution,
            "capability_level": "not_proven",
            "proposal_packet": _proposal_packet(path, capability_level="not_proven"),
            "readiness": {
                "ready_for_default_off_local_harness": False,
                "ready_for_direct_skill_write": False,
                "ready_for_direct_memory_write": False,
            },
        }

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
        "execution": execution,
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


def _dry_run_skill_diff(*, capability_level: str, evidence_path: str) -> dict[str, Any]:
    addendum = (
        "Reference addendum: `references/self-evolution-proposal-boundary.md` captures the "
        "default-off Raven/EverOS self-evolution absorption boundary: consume isolated "
        "benchmark evidence, emit proposal-only receipts, keep direct memory/skill writes "
        "closed, and require a separate promotion gate before applying generated skill diffs."
    )
    preview = "\n".join(
        [
            "```diff",
            "--- a/skills/autonomous-ai-agents/hermes-agent/SKILL.md",
            "+++ b/skills/autonomous-ai-agents/hermes-agent/SKILL.md",
            "@@",
            f"+{addendum}",
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


def build_self_evolution_patch_packet(candidate_pack_path: str | Path) -> dict[str, Any]:
    """Build a fixture-only patch packet from a candidate pack."""
    path = Path(candidate_pack_path).expanduser()
    candidate = json.loads(path.read_text(encoding="utf-8"))
    dry_run = candidate.get("dry_run_skill_diff") or {}
    text = (
        "Reference addendum: `references/self-evolution-proposal-boundary.md` captures the "
        "default-off Raven/EverOS self-evolution absorption boundary: consume isolated "
        "benchmark evidence, emit proposal-only receipts, keep direct memory/skill writes "
        "closed, and require a separate promotion gate before applying generated skill diffs."
    )
    return {
        "schema_version": "hermes.self_evolution.patch_packet.v1",
        "mode": "fixture_only",
        "status": candidate.get("status", "unknown"),
        "candidate_pack_path": str(path),
        "execution": _execution_boundary(),
        "patch": {
            "target_skill": dry_run.get("target_skill", "hermes-agent"),
            "target_reference": dry_run.get("target_reference", "references/self-evolution-proposal-boundary.md"),
            "real_target_write_enabled": False,
            "operations": [
                {
                    "op": "append_reference_addendum",
                    "file": "SKILL.md",
                    "text": text,
                }
            ],
        },
        "verification": {
            "requires_fixture_apply": True,
            "requires_real_target_gate": True,
        },
    }


def verify_self_evolution_patch_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate a patch packet keeps real skill/memory writes closed."""
    reasons: list[str] = []
    if packet.get("schema_version") != "hermes.self_evolution.patch_packet.v1":
        reasons.append("bad_schema")
    if packet.get("mode") != "fixture_only":
        reasons.append("mode_not_fixture_only")
    execution = packet.get("execution") or {}
    if execution.get("direct_skill_write") is not False:
        reasons.append("direct_skill_write_not_closed")
    if execution.get("direct_memory_write") is not False:
        reasons.append("direct_memory_write_not_closed")
    patch = packet.get("patch") or {}
    if patch.get("target_skill") != "hermes-agent":
        reasons.append("unsupported_target_skill")
    if patch.get("real_target_write_enabled") is not False:
        reasons.append("real_target_write_enabled")
    for op in patch.get("operations") or []:
        if op.get("op") != "append_reference_addendum":
            reasons.append("unsupported_operation")
        if "Reference addendum" not in str(op.get("text", "")):
            reasons.append("missing_reference_addendum_text")
    return {"ok": not reasons, "reasons": sorted(set(reasons))}


def materialize_self_evolution_patch_packet(
    *,
    candidate_pack_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Write a fixture-only patch packet without applying it."""
    packet = build_self_evolution_patch_packet(candidate_pack_path=candidate_pack_path)
    verdict = verify_self_evolution_patch_packet(packet)
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "written": True,
        "output_path": str(output),
        "status": packet["status"],
        "kind": "patch_packet",
        "verifier_ok": verdict["ok"],
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_self_evolution_patch_to_fixture(
    *,
    packet: dict[str, Any],
    fixture_skill_dir: str | Path,
) -> dict[str, Any]:
    """Apply an approved packet to a temporary skill fixture only."""
    verdict = verify_self_evolution_patch_packet(packet)
    if not verdict["ok"]:
        return {
            "schema_version": "hermes.self_evolution.fixture_apply_receipt.v1",
            "status": "rejected",
            "verifier": verdict,
            "real_target_touched": False,
            "fixture_touched": False,
        }
    root = Path(fixture_skill_dir).expanduser()
    skill_md = root / "SKILL.md"
    before = skill_md.read_text(encoding="utf-8")
    after = before
    for op in packet["patch"]["operations"]:
        text = op["text"].strip()
        if text not in after:
            after = after.rstrip() + "\n\n" + text + "\n"
    skill_md.write_text(after, encoding="utf-8")
    return {
        "schema_version": "hermes.self_evolution.fixture_apply_receipt.v1",
        "status": "ok",
        "verifier": verdict,
        "fixture_skill_dir": str(root),
        "file": str(skill_md),
        "before_sha256": _sha256_text(before),
        "after_sha256": _sha256_text(after),
        "real_target_touched": False,
        "fixture_touched": before != after,
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_self_evolution_promotion_gate(
    *,
    patch_packet_path: str | Path,
    target_skill_dir: str | Path,
    fixture_receipt_path: str | Path,
    execute_real_skill_write: bool = False,
) -> dict[str, Any]:
    """Build an exact gate packet for a later real-skill apply step."""
    packet_path = Path(patch_packet_path).expanduser()
    target_dir = Path(target_skill_dir).expanduser()
    skill_md = target_dir / "SKILL.md"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    return {
        "schema_version": "hermes.self_evolution.promotion_gate.v1",
        "mode": "exact_target_real_skill_write_gate",
        "authority": {
            "execute_real_skill_write": bool(execute_real_skill_write),
            "requires_cli_execute_flag": True,
        },
        "target": {
            "skill_name": packet.get("patch", {}).get("target_skill", "hermes-agent"),
            "skill_dir": str(target_dir),
            "skill_md": str(skill_md),
            "skill_md_sha256": _sha256_file(skill_md),
        },
        "preconditions": {
            "patch_packet_path": str(packet_path),
            "patch_packet_sha256": _sha256_file(packet_path),
            "fixture_receipt_path": str(fixture_receipt_path),
        },
        "rollback": {
            "backup_required": True,
            "backup_suffix": ".bak",
        },
        "post_action_readback": {
            "required": True,
            "assert_addendum_present": True,
        },
    }


def materialize_self_evolution_promotion_gate(
    *,
    patch_packet_path: str | Path,
    target_skill_dir: str | Path,
    fixture_receipt_path: str | Path,
    output_path: str | Path,
    execute_real_skill_write: bool = False,
) -> dict[str, Any]:
    gate = build_self_evolution_promotion_gate(
        patch_packet_path=patch_packet_path,
        target_skill_dir=target_skill_dir,
        fixture_receipt_path=fixture_receipt_path,
        execute_real_skill_write=execute_real_skill_write,
    )
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"written": True, "output_path": str(output), "kind": "promotion_gate"}


def apply_self_evolution_patch_to_real_skill(
    *,
    packet: dict[str, Any],
    gate: dict[str, Any],
    target_skill_dir: str | Path,
    execute_real_skill_write: bool = False,
) -> dict[str, Any]:
    """Apply a verified packet to an exact skill target only when explicitly enabled."""
    reasons: list[str] = []
    if not execute_real_skill_write:
        reasons.append("execute_flag_missing")
    if gate.get("schema_version") != "hermes.self_evolution.promotion_gate.v1":
        reasons.append("bad_gate_schema")
    if not ((gate.get("authority") or {}).get("execute_real_skill_write") is True):
        reasons.append("gate_authority_not_enabled")
    verdict = verify_self_evolution_patch_packet(packet)
    if not verdict["ok"]:
        reasons.extend(verdict["reasons"])
    target_dir = Path(target_skill_dir).expanduser()
    skill_md = target_dir / "SKILL.md"
    expected_target = ((gate.get("target") or {}).get("skill_md"))
    if expected_target and str(skill_md) != expected_target:
        reasons.append("target_mismatch")
    if reasons:
        return {
            "schema_version": "hermes.self_evolution.real_skill_apply_receipt.v1",
            "status": "blocked",
            "reasons": sorted(set(reasons)),
            "real_target_touched": False,
        }
    before = skill_md.read_text(encoding="utf-8")
    backup = skill_md.with_name(skill_md.name + ".bak")
    backup.write_text(before, encoding="utf-8")
    after = before
    for op in packet["patch"]["operations"]:
        text = op["text"].strip()
        if text not in after:
            after = after.rstrip() + "\n\n" + text + "\n"
    skill_md.write_text(after, encoding="utf-8")
    return {
        "schema_version": "hermes.self_evolution.real_skill_apply_receipt.v1",
        "status": "ok",
        "target_skill_dir": str(target_dir),
        "file": str(skill_md),
        "backup_path": str(backup),
        "before_sha256": _sha256_text(before),
        "after_sha256": _sha256_text(after),
        "real_target_touched": before != after,
        "post_action_readback": {
            "addendum_present": any(op["text"].strip() in after for op in packet["patch"]["operations"]),
        },
    }


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


__all__ = [
    "apply_self_evolution_patch_to_fixture",
    "apply_self_evolution_patch_to_real_skill",
    "build_self_evolution_candidate_pack",
    "build_self_evolution_patch_packet",
    "build_self_evolution_promotion_gate",
    "build_self_evolution_report",
    "materialize_self_evolution_candidate_pack",
    "materialize_self_evolution_patch_packet",
    "materialize_self_evolution_promotion_gate",
    "materialize_self_evolution_proposal",
    "print_self_evolution_report",
    "verify_self_evolution_patch_packet",
]
