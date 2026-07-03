"""Patch-packet and fixture-apply helpers for self-evolution absorption."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import REFERENCE_ADDENDUM, execution_boundary, sha256_text


def build_self_evolution_patch_packet(candidate_pack_path: str | Path) -> dict[str, Any]:
    """Build a fixture-only patch packet from a candidate pack."""
    path = Path(candidate_pack_path).expanduser()
    candidate = json.loads(path.read_text(encoding="utf-8"))
    dry_run = candidate.get("dry_run_skill_diff") or {}
    return {
        "schema_version": "hermes.self_evolution.patch_packet.v1",
        "mode": "fixture_only",
        "status": candidate.get("status", "unknown"),
        "candidate_pack_path": str(path),
        "execution": execution_boundary(),
        "patch": {
            "target_skill": dry_run.get("target_skill", "hermes-agent"),
            "target_reference": dry_run.get("target_reference", "references/self-evolution-proposal-boundary.md"),
            "real_target_write_enabled": False,
            "operations": [
                {
                    "op": "append_reference_addendum",
                    "file": "SKILL.md",
                    "text": REFERENCE_ADDENDUM,
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
        "before_sha256": sha256_text(before),
        "after_sha256": sha256_text(after),
        "real_target_touched": False,
        "fixture_touched": before != after,
    }
