"""Promotion-gate and exact real-skill apply helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import sha256_file, sha256_text
from .patch_packet import verify_self_evolution_patch_packet


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
            "skill_md_sha256": sha256_file(skill_md),
        },
        "preconditions": {
            "patch_packet_path": str(packet_path),
            "patch_packet_sha256": sha256_file(packet_path),
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
        "before_sha256": sha256_text(before),
        "after_sha256": sha256_text(after),
        "real_target_touched": before != after,
        "post_action_readback": {
            "addendum_present": any(op["text"].strip() in after for op in packet["patch"]["operations"]),
        },
    }
