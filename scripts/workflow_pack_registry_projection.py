#!/usr/bin/env python3
"""Phase 51: project Workflow/Agent Pack manifests into a read-only registry view.

This is an operator/control-tower projection only. It reads Phase49 pack
manifests and emits registry JSON/Markdown for humans and future dashboards. It
does not mutate runtime, cron, taskpool lifecycle, credentials, or external
systems.
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

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-pack-registry"
WORKFLOW_PACK = ROOT / "docs" / "private" / "workflow-kernel-pack-manifests" / "workflow_kernel_external_dispatch_governance.workflow_pack.json"
AGENT_PACK = ROOT / "docs" / "private" / "workflow-kernel-pack-manifests" / "workflow_kernel_operator.agent_pack.json"
PHASE49 = ROOT / "docs" / "private" / "workflow-kernel-pack-manifests" / "workflow_kernel_pack_manifests_20260613_phase49.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def classify_registry(workflow_pack: dict[str, Any], agent_pack: dict[str, Any]) -> dict[str, Any]:
    modules = workflow_pack.get("modules", [])
    external_modules = [m for m in modules if m.get("side_effects", {}).get("external_dispatch")]
    credential_modules = [m for m in modules if m.get("side_effects", {}).get("credential_access")]
    local_write_modules = [m for m in modules if m.get("risk_level") == "local_write"]
    side_policy = agent_pack.get("side_effect_policy", {})
    blockers = []
    if side_policy.get("external_side_effects_enabled"):
        blockers.append("agent_external_side_effects_enabled_unexpected")
    if side_policy.get("unattended_external_dispatch_enabled"):
        blockers.append("agent_unattended_dispatch_enabled_unexpected")
    if side_policy.get("credential_access_enabled"):
        blockers.append("agent_credential_access_enabled_unexpected")
    if external_modules:
        blockers.append("workflow_module_external_dispatch_true_unexpected")
    if credential_modules:
        blockers.append("workflow_module_credential_access_true_unexpected")
    status = "PACK_REGISTRY_PROJECTION_READY" if not blockers else "PACK_REGISTRY_PROJECTION_BLOCKED"
    return {
        "status": status,
        "blockers": blockers,
        "module_count": len(modules),
        "local_write_module_count": len(local_write_modules),
        "external_dispatch_module_count": len(external_modules),
        "credential_access_module_count": len(credential_modules),
    }


def build_registry(*, workflow_pack_path: Path = WORKFLOW_PACK, agent_pack_path: Path = AGENT_PACK, phase49_path: Path = PHASE49) -> dict[str, Any]:
    workflow_pack = read_json(workflow_pack_path)
    agent_pack = read_json(agent_pack_path)
    phase49 = read_json(phase49_path)
    classification = classify_registry(workflow_pack, agent_pack)
    module_rows = [
        {
            "module_id": m.get("module_id"),
            "layer": m.get("layer"),
            "entrypoint": m.get("entrypoint"),
            "risk_level": m.get("risk_level"),
            "human_gate": m.get("human_gate"),
            "side_effects": m.get("side_effects"),
        }
        for m in workflow_pack.get("modules", [])
    ]
    return {
        "report_id": "workflow_kernel_pack_registry_20260613_phase51",
        "created_at": iso(utc_now()),
        "status": classification["status"],
        "projection_type": "read_only_control_tower_registry",
        "workflow_pack_id": workflow_pack.get("pack_id"),
        "workflow_pack_version": workflow_pack.get("pack_version"),
        "agent_pack_id": agent_pack.get("agent_pack_id"),
        "agent_pack_version": agent_pack.get("agent_version"),
        "phase49_status": phase49.get("status"),
        "classification": classification,
        "module_rows": module_rows,
        "operator_panels": [
            {
                "panel_id": "pack_summary",
                "title": "Pack Summary",
                "fields": ["workflow_pack_id", "agent_pack_id", "status", "module_count"],
            },
            {
                "panel_id": "module_boundaries",
                "title": "Module Boundaries",
                "fields": ["module_id", "risk_level", "human_gate", "side_effects"],
            },
            {
                "panel_id": "next_boundary",
                "title": "Next Boundary",
                "fields": ["guarded_single_send_requirements", "pack_registry_projection"],
            },
        ],
        "guarded_single_send_requirements": {
            "required_before_phase50": [
                "exact_adapter",
                "exact_target",
                "exact_payload",
                "mode",
                "idempotency_key",
                "rollback_or_correction_path",
                "post_send_readback",
            ],
            "current_status": "not_selected",
        },
        "safe_boundaries": [
            "read-only registry projection",
            "no cron mutation",
            "no task lifecycle mutation",
            "no model generation",
            "no credential read/write",
            "no external message send",
            "no unattended dispatch enablement",
        ],
        "next_boundary": "major divergence: choose Phase50 guarded single external send with exact target/payload, or continue Phase52 control-tower UI/API projection",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_pack_registry_20260613_phase51.json"
    md_path = out_dir / "workflow_kernel_pack_registry_20260613_phase51.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Pack Registry — Phase 51",
        "",
        f"Status: **{report['status']}**",
        f"Workflow pack: `{report['workflow_pack_id']}`",
        f"Agent pack: `{report['agent_pack_id']}`",
        f"Modules: `{report['classification']['module_count']}`",
        "",
        "## Modules",
    ]
    for row in report["module_rows"]:
        lines.append(f"- `{row['module_id']}` — layer=`{row['layer']}`, risk=`{row['risk_level']}`, gate=`{row['human_gate']}`")
    lines.extend(["", "## Phase50 guarded send requirements", *[f"- {x}" for x in report["guarded_single_send_requirements"]["required_before_phase50"]]])
    lines.extend(["", "## Safe boundaries", *[f"- {x}" for x in report["safe_boundaries"]]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow-pack", type=Path, default=WORKFLOW_PACK)
    ap.add_argument("--agent-pack", type=Path, default=AGENT_PACK)
    ap.add_argument("--phase49", type=Path, default=PHASE49)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_registry(workflow_pack_path=args.workflow_pack, agent_pack_path=args.agent_pack, phase49_path=args.phase49)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: workflow={report['workflow_pack_id']} agent={report['agent_pack_id']}")
    return 0 if report["status"] == "PACK_REGISTRY_PROJECTION_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
