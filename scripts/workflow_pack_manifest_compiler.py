#!/usr/bin/env python3
"""Phase 49: compile Workflow Pack and Agent Pack manifests.

This productizes the proven Phase17-48 workflow-kernel chain as reusable pack
metadata. It is schema/projection only: no cron mutation, model generation,
credential access, external dispatch, or task lifecycle transition.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-pack-manifests"
WORKFLOW_SCHEMA = ROOT / "schemas" / "workflow_pack_manifest.schema.json"
AGENT_SCHEMA = ROOT / "schemas" / "agent_pack_manifest.schema.json"
PHASE45 = ROOT / "docs" / "private" / "workflow-kernel-runtime-auto-enable" / "workflow_kernel_runtime_auto_enable_20260613_phase45.json"
PHASE46 = ROOT / "docs" / "private" / "workflow-kernel-post-auto-enable-observation" / "workflow_kernel_post_auto_enable_observation_20260613_phase46.json"
PHASE47 = ROOT / "docs" / "private" / "workflow-kernel-external-dispatch-expansion" / "workflow_kernel_external_dispatch_expansion_20260613_phase47.json"
PHASE48 = ROOT / "docs" / "private" / "workflow-kernel-external-dispatch-policy-gate" / "workflow_kernel_external_dispatch_policy_gate_20260613_phase48.json"
SEQUENCE = ROOT / "docs" / "private" / "workflow-kernel-sequence-map" / "workflow_kernel_sequence_map_20260613_phase38.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def require_keys(obj: dict[str, Any], keys: list[str], label: str) -> list[str]:
    return [f"{label}_missing_{key}" for key in keys if key not in obj]


def validate_workflow_pack(manifest: dict[str, Any]) -> list[str]:
    errors = require_keys(manifest, ["schema_version", "pack_id", "pack_name", "pack_version", "source_phase_range", "modules", "evidence_policy", "authority_boundaries", "runtime_flags", "verification_commands", "artifacts"], "workflow_pack")
    if manifest.get("schema_version") != "1.0.0":
        errors.append("workflow_pack_schema_version_invalid")
    modules = manifest.get("modules")
    if not isinstance(modules, list) or not modules:
        errors.append("workflow_pack_modules_empty")
    else:
        for idx, module in enumerate(modules):
            errors.extend(require_keys(module, ["module_id", "layer", "entrypoint", "inputs", "outputs", "risk_level", "human_gate", "side_effects"], f"workflow_module_{idx}"))
            side = module.get("side_effects", {})
            for key in ["cron_mutation", "model_generation", "credential_access", "external_dispatch"]:
                if key not in side or not isinstance(side.get(key), bool):
                    errors.append(f"workflow_module_{idx}_side_effect_{key}_invalid")
    return errors


def validate_agent_pack(manifest: dict[str, Any]) -> list[str]:
    errors = require_keys(manifest, ["schema_version", "agent_pack_id", "agent_name", "agent_version", "backing_workflow_pack_id", "role", "allowed_tools", "default_mode", "decision_policy", "human_gate_policy", "side_effect_policy", "verification_commands", "artifacts"], "agent_pack")
    if manifest.get("schema_version") != "1.0.0":
        errors.append("agent_pack_schema_version_invalid")
    side = manifest.get("side_effect_policy", {})
    for key in ["unattended_external_dispatch_enabled", "external_side_effects_enabled", "credential_access_enabled"]:
        if key not in side or not isinstance(side.get(key), bool):
            errors.append(f"agent_pack_side_effect_{key}_invalid")
    return errors


def build_manifests() -> dict[str, Any]:
    phase45 = read_json(PHASE45)
    phase46 = read_json(PHASE46)
    phase47 = read_json(PHASE47)
    phase48 = read_json(PHASE48)
    sequence = read_json(SEQUENCE)
    modules = [
        {
            "module_id": "workflow_kernel.runtime_auto_enable.v1",
            "layer": "runtime_switch",
            "entrypoint": "workflow_kernel.runtime.check_runtime_switch_readiness",
            "inputs": [str(PHASE45)],
            "outputs": ["runtime_auto_enabled_readiness"],
            "risk_level": "local_write",
            "human_gate": "completed_phase45_full_authorization",
            "side_effects": {"cron_mutation": False, "model_generation": False, "credential_access": False, "external_dispatch": False},
        },
        {
            "module_id": "workflow_kernel.post_auto_enable_observation.v1",
            "layer": "runtime_observation",
            "entrypoint": "scripts/workflow_post_auto_enable_observation.py",
            "inputs": [str(PHASE46)],
            "outputs": ["projection_correlation_readiness_drift_summary"],
            "risk_level": "read_only",
            "human_gate": "none_for_readback",
            "side_effects": {"cron_mutation": False, "model_generation": False, "credential_access": False, "external_dispatch": False},
        },
        {
            "module_id": "workflow_kernel.external_dispatch_candidate.v1",
            "layer": "external_dispatch_governance",
            "entrypoint": "scripts/workflow_external_dispatch_expansion_packet.py",
            "inputs": [str(PHASE47)],
            "outputs": ["adapter_options", "candidate_tracks", "policy_requirements"],
            "risk_level": "read_only",
            "human_gate": "exact_adapter_target_payload_required_for_external_effect",
            "side_effects": {"cron_mutation": False, "model_generation": False, "credential_access": False, "external_dispatch": False},
        },
        {
            "module_id": "workflow_kernel.external_dispatch_local_outbox.v1",
            "layer": "external_dispatch_policy_gate",
            "entrypoint": "scripts/workflow_external_dispatch_policy_gate.py",
            "inputs": [str(PHASE47), str(PHASE48)],
            "outputs": ["local_outbox_item", "local_outbox_receipt", "policy_gate_report"],
            "risk_level": "local_write",
            "human_gate": "default_safe_local_outbox_when_no_external_target",
            "side_effects": {"cron_mutation": False, "model_generation": False, "credential_access": False, "external_dispatch": False},
        },
    ]
    workflow_pack = {
        "schema_version": "1.0.0",
        "pack_id": "workflow_kernel_external_dispatch_governance",
        "pack_name": "Workflow Kernel External Dispatch Governance",
        "pack_version": "2026.06.13-phase49",
        "source_phase_range": "Phase17-48",
        "modules": modules,
        "evidence_policy": {
            "required_prefixes": ["artifact:", "command:", "proof:", "classification:"],
            "receipt_required_for_local_write": True,
            "post_apply_readback_required": True,
            "natural_scheduled_observation_policy": "do_not_trigger_cron_solely_for_evidence",
        },
        "authority_boundaries": [
            "external_send_requires_exact_adapter_target_payload_mode",
            "credential_access_not_in_pack_scope",
            "broad_scheduler_rewrite_not_in_pack_scope",
            "unattended_dispatch_requires_future_policy_gate",
        ],
        "runtime_flags": {
            "runtime_switch_enabled": bool(phase45.get("runtime_switch_enabled_by_default") or phase45.get("runtime_switch_enabled")),
            "phase46_status": phase46.get("status"),
            "phase47_status": phase47.get("status"),
            "phase48_status": phase48.get("status"),
            "external_side_effects_executed": bool(phase48.get("receipt", {}).get("external_side_effects_executed")),
            "unattended_external_dispatch_enabled": bool(phase48.get("receipt", {}).get("unattended_external_dispatch_enabled")),
        },
        "verification_commands": [
            "python3 -m py_compile scripts/workflow_pack_manifest_compiler.py",
            "./scripts/run_tests.sh tests/scripts/test_workflow_pack_manifest_compiler.py",
            "python3 scripts/workflow_pack_manifest_compiler.py --write-report --json",
        ],
        "artifacts": {
            "phase45": str(PHASE45),
            "phase46": str(PHASE46),
            "phase47": str(PHASE47),
            "phase48": str(PHASE48),
            "sequence": str(SEQUENCE),
            "workflow_schema": str(WORKFLOW_SCHEMA),
        },
    }
    agent_pack = {
        "schema_version": "1.0.0",
        "agent_pack_id": "agent.workflow_kernel_operator",
        "agent_name": "Workflow Kernel Operator",
        "agent_version": "2026.06.13-phase49",
        "backing_workflow_pack_id": workflow_pack["pack_id"],
        "role": "Operate workflow-kernel evidence, observation, and external-dispatch governance without inventing external targets.",
        "allowed_tools": ["terminal", "read_file", "write_file", "patch", "skill_manage", "todo"],
        "default_mode": "local_outbox_only",
        "decision_policy": {
            "when_no_external_target": "select_local_outbox_only",
            "when_exact_external_target_supplied": "prepare_guarded_single_send_packet_before_send",
            "when_natural_schedule_missing": "observation_pending_not_failure",
        },
        "human_gate_policy": {
            "external_send": "requires exact adapter, target, payload, mode, idempotency key, rollback/correction path",
            "unattended_dispatch": "requires future policy gate and rate/allowlist/readback evidence",
        },
        "side_effect_policy": {
            "unattended_external_dispatch_enabled": False,
            "external_side_effects_enabled": False,
            "credential_access_enabled": False,
            "local_outbox_write_enabled": True,
        },
        "verification_commands": workflow_pack["verification_commands"],
        "artifacts": {
            "workflow_pack_id": workflow_pack["pack_id"],
            "agent_schema": str(AGENT_SCHEMA),
            "sequence_status": sequence.get("status"),
            "recommended_next": sequence.get("recommended_next"),
        },
    }
    workflow_errors = validate_workflow_pack(workflow_pack)
    agent_errors = validate_agent_pack(agent_pack)
    return {
        "report_id": "workflow_kernel_pack_manifests_20260613_phase49",
        "created_at": iso(utc_now()),
        "status": "WORKFLOW_AGENT_PACK_MANIFESTS_READY" if not workflow_errors and not agent_errors else "WORKFLOW_AGENT_PACK_MANIFESTS_INVALID",
        "workflow_pack": workflow_pack,
        "agent_pack": agent_pack,
        "validation_errors": workflow_errors + agent_errors,
        "safe_boundaries": [
            "schema/projection only",
            "no cron mutation",
            "no model generation",
            "no credential read/write",
            "no external message send",
            "no unattended dispatch enablement",
        ],
        "next_boundary": "Phase50 guarded single external send requires exact external adapter/target/payload/mode or continue to pack registry/control tower projection",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = out_dir / "workflow_kernel_external_dispatch_governance.workflow_pack.json"
    agent_path = out_dir / "workflow_kernel_operator.agent_pack.json"
    json_path = out_dir / "workflow_kernel_pack_manifests_20260613_phase49.json"
    md_path = out_dir / "workflow_kernel_pack_manifests_20260613_phase49.md"
    workflow_art = write_json_artifact(workflow_path, report["workflow_pack"])
    agent_art = write_json_artifact(agent_path, report["agent_pack"])
    report = dict(report)
    report["workflow_pack_artifact"] = workflow_art
    report["agent_pack_artifact"] = agent_art
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Pack Manifests — Phase 49",
        "",
        f"Status: **{report['status']}**",
        f"Workflow pack: `{report['workflow_pack']['pack_id']}`",
        f"Agent pack: `{report['agent_pack']['agent_pack_id']}`",
        "",
        "## Modules",
    ]
    for module in report["workflow_pack"]["modules"]:
        lines.append(f"- `{module['module_id']}` — layer=`{module['layer']}`, risk=`{module['risk_level']}`, gate=`{module['human_gate']}`")
    lines.extend(["", "## Boundaries", *[f"- {x}" for x in report["safe_boundaries"]]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "workflow_pack": str(workflow_path), "agent_pack": str(agent_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_manifests()
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: workflow={report['workflow_pack']['pack_id']} agent={report['agent_pack']['agent_pack_id']}")
    return 0 if report["status"] == "WORKFLOW_AGENT_PACK_MANIFESTS_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
