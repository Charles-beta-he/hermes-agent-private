#!/usr/bin/env python3
"""Phase 52: emit a read-only Control Tower UI/API projection.

This takes the Phase51 pack registry and Phase50 guarded-send receipt and emits
an operator-facing API payload plus a static HTML dashboard artifact. It does
not run a server, mutate runtime state, send messages, or enable unattended
dispatch.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-control-tower-projection"
REGISTRY = ROOT / "docs" / "private" / "workflow-kernel-pack-registry" / "workflow_kernel_pack_registry_20260613_phase51.json"
PHASE50 = ROOT / "docs" / "private" / "workflow-kernel-guarded-external-send" / "workflow_kernel_guarded_external_send_20260613_phase50.json"
SEQUENCE = ROOT / "docs" / "private" / "workflow-kernel-sequence-map" / "workflow_kernel_sequence_map_20260613_phase38.json"
READINESS = ROOT / "docs" / "private" / "workflow-kernel-runtime-readiness-summary" / "workflow_kernel_runtime_readiness_summary_20260613_phase36.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def loop_runtime_contract_projection(sequence: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    adoption_phase = next((p for p in sequence.get("phases", []) if p.get("phase") == 93), {})
    loop_artifact = ((readiness.get("artifact_states") or {}).get("loop_runtime_contract") or {})
    gate = str(adoption_phase.get("gate") or "")
    return {
        "doctor_passed": bool((readiness.get("checks") or {}).get("loop_runtime_contract_doctor_passed")),
        "doctor_status": loop_artifact.get("status"),
        "receipt_path": loop_artifact.get("path"),
        "adoption_phase": adoption_phase.get("phase"),
        "adoption_status": adoption_phase.get("status"),
        "gate": gate,
        "runtime_promotion_enabled": "loop_runtime_enabled=false" not in gate,
        "scheduler_enabled": "scheduler_enabled=false" not in gate,
        "worker_spawn_enabled": "worker_spawn_enabled=false" not in gate,
        "external_side_effects_enabled": "external_side_effects=false" not in gate,
    }


def build_projection(*, registry_path: Path = REGISTRY, phase50_path: Path = PHASE50, sequence_path: Path = SEQUENCE, readiness_path: Path = READINESS) -> dict[str, Any]:
    registry = read_json(registry_path)
    phase50 = read_json(phase50_path)
    sequence = read_json(sequence_path)
    readiness = read_json(readiness_path)
    loop_contract = loop_runtime_contract_projection(sequence, readiness)
    blockers: list[str] = []
    if registry.get("status") != "PACK_REGISTRY_PROJECTION_READY":
        blockers.append("phase51_registry_not_ready")
    if phase50.get("status") != "GUARDED_EXTERNAL_SEND_VERIFIED":
        blockers.append("phase50_guarded_send_not_verified")
    if phase50.get("unattended_external_dispatch_enabled") is not False:
        blockers.append("unattended_dispatch_unexpectedly_enabled")
    status = "CONTROL_TOWER_UI_API_PROJECTION_READY" if not blockers else "CONTROL_TOWER_UI_API_PROJECTION_BLOCKED"
    return {
        "report_id": "workflow_kernel_control_tower_projection_20260613_phase52",
        "created_at": iso(utc_now()),
        "status": status,
        "phase": 52,
        "projection_type": "read_only_ui_api_payload_and_static_html",
        "blockers": blockers,
        "api_version": "workflow-control-tower.v1",
        "operator_summary": {
            "workflow_pack_id": registry.get("workflow_pack_id"),
            "agent_pack_id": registry.get("agent_pack_id"),
            "registry_status": registry.get("status"),
            "guarded_send_status": phase50.get("status"),
            "last_external_send": {
                "adapter": phase50.get("adapter"),
                "target_type": (phase50.get("target") or {}).get("type"),
                "message_id": (phase50.get("send_result") or {}).get("message_id"),
                "external_side_effect_executed": phase50.get("external_side_effect_executed"),
            },
            "sequence_phase_count": sequence.get("phase_count"),
            "loop_runtime_contract": loop_contract,
        },
        "panels": [
            {
                "id": "pack_registry",
                "title": "Workflow / Agent Pack Registry",
                "status": registry.get("status"),
                "items": registry.get("module_rows", []),
            },
            {
                "id": "external_dispatch_receipt",
                "title": "Latest Guarded External Send",
                "status": phase50.get("status"),
                "items": [phase50.get("send_result", {})],
            },
            {
                "id": "loop_runtime_contract",
                "title": "LoopRuntimeContract Gate",
                "status": "doctor_passed" if loop_contract["doctor_passed"] else "doctor_failed_or_missing",
                "items": [loop_contract],
            },
            {
                "id": "next_boundary",
                "title": "Next Boundary",
                "status": "needs_product_direction_after_phase52",
                "items": [
                    {
                        "option": "control_tower_runtime_api",
                        "requires": "choose durable API/server integration path",
                    },
                    {
                        "option": "unattended_dispatch_policy_gate",
                        "requires": "target allowlist, rate limits, duplicate policy, correction path",
                    },
                ],
            },
        ],
        "safe_boundaries": [
            "static HTML only",
            "read-only API payload only",
            "loop runtime contract projected read-only",
            "no live server started",
            "no runtime mutation",
            "no unattended dispatch enablement",
            "no additional external send",
        ],
        "next_boundary": "larger divergence after Phase52: durable Control Tower runtime/API integration vs unattended dispatch policy gate",
    }


def render_html(report: dict[str, Any]) -> str:
    summary = report["operator_summary"]
    cards = "\n".join(
        f"<section class='card'><h2>{html.escape(panel['title'])}</h2><p class='status'>{html.escape(str(panel['status']))}</p><pre>{html.escape(json.dumps(panel['items'], ensure_ascii=False, indent=2))}</pre></section>"
        for panel in report["panels"]
    )
    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Workflow Kernel Control Tower Projection</title>
<style>
:root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #08111f; color: #e6edf7; }}
body {{ margin: 0; padding: 32px; background: radial-gradient(circle at top left, #17365f, #08111f 52%); }}
header {{ max-width: 1120px; margin: 0 auto 24px; }}
h1 {{ font-size: clamp(28px, 4vw, 48px); margin: 0 0 12px; }}
.grid {{ max-width: 1120px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
.card {{ border: 1px solid rgba(148, 163, 184, .28); border-radius: 20px; background: rgba(15, 23, 42, .78); box-shadow: 0 18px 60px rgba(0,0,0,.25); padding: 18px; }}
.status {{ color: #8ee6b5; font-weight: 700; letter-spacing: .02em; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }}
.badge {{ border: 1px solid rgba(125, 211, 252, .35); border-radius: 999px; padding: 8px 12px; color: #bae6fd; background: rgba(14, 116, 144, .16); }}
pre {{ overflow: auto; max-height: 420px; color: #cbd5e1; font-size: 12px; line-height: 1.45; }}
</style>
</head>
<body>
<header>
<h1>Workflow Kernel Control Tower Projection</h1>
<p class='status'>{html.escape(report['status'])}</p>
<div class='meta'>
<span class='badge'>Workflow: {html.escape(str(summary['workflow_pack_id']))}</span>
<span class='badge'>Agent: {html.escape(str(summary['agent_pack_id']))}</span>
<span class='badge'>Last message: {html.escape(str(summary['last_external_send']['message_id']))}</span>
<span class='badge'>Phase count: {html.escape(str(summary['sequence_phase_count']))}</span>
</div>
</header>
<main class='grid'>{cards}</main>
</body>
</html>
"""


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_control_tower_projection_20260613_phase52.json"
    html_path = out_dir / "workflow_kernel_control_tower_projection_20260613_phase52.html"
    md_path = out_dir / "workflow_kernel_control_tower_projection_20260613_phase52.md"
    artifact = write_json_artifact(json_path, report)
    html_path.write_text(render_html(report))
    md_path.write_text("\n".join([
        "# Workflow Kernel Control Tower Projection — Phase 52",
        "",
        f"Status: **{report['status']}**",
        f"Workflow pack: `{report['operator_summary']['workflow_pack_id']}`",
        f"Agent pack: `{report['operator_summary']['agent_pack_id']}`",
        f"Last Telegram message id: `{report['operator_summary']['last_external_send']['message_id']}`",
        "",
        "## Safe boundaries",
        *[f"- {x}" for x in report["safe_boundaries"]],
        "",
        f"Next boundary: {report['next_boundary']}",
    ]) + "\n")
    return {"json": str(json_path), "html": str(html_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    ap.add_argument("--phase50", type=Path, default=PHASE50)
    ap.add_argument("--sequence", type=Path, default=SEQUENCE)
    ap.add_argument("--readiness", type=Path, default=READINESS)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_projection(registry_path=args.registry, phase50_path=args.phase50, sequence_path=args.sequence, readiness_path=args.readiness)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: workflow={report['operator_summary']['workflow_pack_id']}")
    return 0 if report["status"] == "CONTROL_TOWER_UI_API_PROJECTION_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
