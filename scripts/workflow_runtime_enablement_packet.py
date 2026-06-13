#!/usr/bin/env python3
"""Compile an explicit human authorization packet for workflow runtime enablement.

Phase 37 boundary: produce a decision packet only. It does not enable runtime,
mutate cron, trigger jobs, call models, read credentials, or send messages.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import read_json_artifact, write_json_artifact

DEFAULT_SUMMARY_GLOB = ROOT / "docs" / "private" / "workflow-kernel-runtime-readiness-summary" / "workflow_kernel_runtime_readiness_summary_*.json"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-runtime-enablement-packet"
READY_SUMMARY = "RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def newest_path(pattern: str | Path) -> Path | None:
    matches = [Path(p) for p in glob.glob(str(pattern))]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def load_summary(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    obj = read_json_artifact(path)
    return obj if isinstance(obj, dict) else None


def option_matrix() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "A_READ_ONLY_CONTINUE",
            "label": "继续只读/可逆架构层",
            "authority_needed": "none beyond current bounded continuation",
            "side_effects": "local docs/tests/taskpool evidence only",
            "blast_radius": "lowest",
            "rollback": "git revert scoped commits; no runtime state mutation",
            "verification": ["tests pass", "readiness summary remains ready", "runtime switch remains false"],
            "recommended_default": True,
        },
        {
            "option_id": "B_SINGLE_WRAPPER_RUNTIME_ENABLE_DRY_RUN",
            "label": "授权单 wrapper runtime enable dry-run / shadow projection",
            "authority_needed": "explicit human authorization for runtime-switch-adjacent dry-run",
            "side_effects": "local runtime/config shadow artifact only; no cron mutation",
            "blast_radius": "low-medium",
            "rollback": "delete/ignore shadow artifact; git revert scoped code if needed",
            "verification": ["dry-run readback", "wrapper receipt", "cron state unchanged"],
            "recommended_default": False,
        },
        {
            "option_id": "C_SINGLE_WRAPPER_SCHEDULER_NATIVE_ROLLOUT",
            "label": "授权单 wrapper scheduler-native 接管",
            "authority_needed": "explicit human authorization to mutate scheduler/runtime boundary",
            "side_effects": "may modify scheduler/runtime execution path for exactly one opted-in job",
            "blast_radius": "medium",
            "rollback": "restore previous scheduler path/flag; verify cron jobs.json and wrapper logs",
            "verification": ["one scheduled/dry-run outcome", "receipt-backed correlation", "mission/taskpool evidence"],
            "recommended_default": False,
        },
        {
            "option_id": "D_WAIT_FOR_NATURAL_RECEIPTS",
            "label": "等待真实 scheduled runs 自然产生 receipt-backed full correlation",
            "authority_needed": "none",
            "side_effects": "none now; future scheduled wrappers append receipts",
            "blast_radius": "lowest but time-dependent",
            "rollback": "not needed; append-only receipts are audit evidence",
            "verification": ["receipt-aware correlation reports receipt-backed rows for both wrappers"],
            "recommended_default": False,
        },
    ]


def build_packet(summary_path: Path | None = None) -> dict[str, Any]:
    summary_path = summary_path or newest_path(DEFAULT_SUMMARY_GLOB)
    summary = load_summary(summary_path)
    summary_ready = (summary or {}).get("status") == READY_SUMMARY and not (summary or {}).get("blocking_checks")
    return {
        "report_id": "workflow_kernel_runtime_enablement_packet_20260613_phase37",
        "created_at": iso(utc_now()),
        "status": "RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY" if summary_ready else "RUNTIME_ENABLEMENT_PACKET_BLOCKED_BY_READINESS",
        "summary_path": str(summary_path) if summary_path else None,
        "summary_status": (summary or {}).get("status"),
        "summary_blocking_checks": (summary or {}).get("blocking_checks"),
        "runtime_switch_enabled_by_default": (summary or {}).get("runtime_switch_enabled_by_default"),
        "decision_required": True,
        "decision_question": "下一步是否授权跨越 runtime/scheduler enablement 边界，还是继续只读/等待自然 receipt？",
        "options": option_matrix(),
        "recommended_safe_default": "A_READ_ONLY_CONTINUE",
        "closed_boundaries_without_explicit_authorization": [
            "enable workflow_kernel.RUNTIME_SWITCH_ENABLED",
            "mutate ~/.hermes/cron/jobs.json",
            "trigger cron jobs solely to create evidence",
            "scheduler-native takeover for any job",
            "model generation smoke using credentials",
            "external message delivery",
        ],
        "minimum_authorization_to_continue_beyond_read_only": "choose option B or C explicitly with scope and rollback acceptance",
    }


def write_packet(packet: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_runtime_enablement_packet_20260613_phase37.json"
    md_path = out_dir / "workflow_kernel_runtime_enablement_packet_20260613_phase37.md"
    artifact = write_json_artifact(json_path, packet)
    lines = [
        "# Workflow Kernel Runtime Enablement Packet — Phase 37",
        "",
        f"Status: **{packet['status']}**",
        f"Decision required: `{packet['decision_required']}`",
        "",
        "## Decision question",
        packet["decision_question"],
        "",
        "## Options",
    ]
    for option in packet["options"]:
        lines.extend([
            f"### {option['option_id']}: {option['label']}",
            f"- Authority: {option['authority_needed']}",
            f"- Side effects: {option['side_effects']}",
            f"- Blast radius: {option['blast_radius']}",
            f"- Rollback: {option['rollback']}",
            f"- Recommended default: `{option['recommended_default']}`",
            "",
        ])
    lines.extend([
        "## Closed boundaries without explicit authorization",
        *[f"- {x}" for x in packet["closed_boundaries_without_explicit_authorization"]],
    ])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=None)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    packet = build_packet(Path(args.summary) if args.summary else None)
    if args.write_report:
        packet["artifacts"] = write_packet(packet, Path(args.out_dir))
    if args.json:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{packet['status']}: recommended={packet['recommended_safe_default']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
