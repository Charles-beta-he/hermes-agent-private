"""Read-only stage manifest for modular self-evolution absorption."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import execution_boundary

_STAGE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "proposal",
        "module": "hermes_cli.self_evolution.proposal",
        "schema_version": "hermes.self_evolution.proposal_receipt.v1",
        "default_enabled": True,
        "side_effect_class": "read_only_projection",
        "required_gate": "none",
        "produces": ["self_evolution_capability_proposal", "proposal_receipt"],
    },
    {
        "id": "candidate_pack",
        "module": "hermes_cli.self_evolution.candidate",
        "schema_version": "hermes.self_evolution.candidate_pack.v1",
        "default_enabled": True,
        "side_effect_class": "local_file_projection",
        "required_gate": "proposal_receipt_or_evidence",
        "produces": ["experience_atom", "dry_run_skill_diff"],
    },
    {
        "id": "patch_packet",
        "module": "hermes_cli.self_evolution.patch_packet",
        "schema_version": "hermes.self_evolution.patch_packet.v1",
        "default_enabled": True,
        "side_effect_class": "local_file_projection",
        "required_gate": "candidate_pack",
        "produces": ["fixture_only_patch_packet"],
    },
    {
        "id": "fixture_apply",
        "module": "hermes_cli.self_evolution.patch_packet",
        "schema_version": "hermes.self_evolution.fixture_apply_receipt.v1",
        "default_enabled": True,
        "side_effect_class": "temporary_fixture_write",
        "required_gate": "verified_patch_packet_and_temp_fixture",
        "produces": ["fixture_apply_receipt"],
    },
    {
        "id": "promotion_gate",
        "module": "hermes_cli.self_evolution.promotion_gate",
        "schema_version": "hermes.self_evolution.promotion_gate.v1",
        "default_enabled": True,
        "side_effect_class": "read_only_exact_gate_projection",
        "required_gate": "fixture_apply_receipt",
        "produces": ["exact_target_promotion_gate"],
    },
    {
        "id": "real_skill_apply",
        "module": "hermes_cli.self_evolution.promotion_gate",
        "schema_version": "hermes.self_evolution.real_skill_apply_receipt.v1",
        "default_enabled": False,
        "side_effect_class": "local_skill_write",
        "required_gate": "exact_promotion_gate_and_execute_flag",
        "produces": ["real_skill_apply_or_blocked_receipt", "rollback_backup"],
    },
]


def build_self_evolution_manifest() -> dict[str, Any]:
    """Return a read-only manifest of self-evolution absorption stages."""
    return {
        "schema_version": "hermes.self_evolution.stage_manifest.v1",
        "mode": "read_only_projection",
        "execution": execution_boundary(),
        "stages": [dict(stage) for stage in _STAGE_DEFINITIONS],
        "closed_boundaries": {
            "memory_write": False,
            "runtime_mutation": False,
            "daemon_start": False,
            "cron_create": False,
            "external_dispatch": False,
            "real_skill_apply_default_enabled": False,
        },
        "compatibility": {
            "legacy_module": "hermes_cli.self_evolution_doctor",
            "public_package": "hermes_cli.self_evolution",
        },
    }


def materialize_self_evolution_manifest(*, output_path: str | Path) -> dict[str, Any]:
    """Write the read-only stage manifest to a local JSON file."""
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_self_evolution_manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"written": True, "output_path": str(output), "kind": "stage_manifest"}
