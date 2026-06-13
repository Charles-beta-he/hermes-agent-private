#!/usr/bin/env python3
"""Compile Phase 47 external dispatch expansion candidate packet.

This packet enumerates expansion options and the gates required before any
unattended external side effect is enabled. It is a candidate/authorization
artifact only; it does not send messages, mutate external systems, read secrets,
or change scheduler behavior.
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

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-external-dispatch-expansion"

ADAPTER_OPTIONS: list[dict[str, Any]] = [
    {
        "adapter": "local_outbox_simulator",
        "mode": "local_record_only",
        "external_side_effects": False,
        "required_authority": "none beyond local evidence write",
        "required_inputs": ["purpose", "payload_digest", "idempotency_key"],
        "blast_radius": "local artifact only",
        "rollback": "not_needed_no_external_mutation",
        "recommended_first": True,
    },
    {
        "adapter": "telegram",
        "mode": "guarded_single_send_or_dry_run_preview",
        "external_side_effects": True,
        "required_authority": "explicit target + exact message/purpose + one-send authorization",
        "required_inputs": ["target", "message", "purpose", "idempotency_key", "rate_limit_key"],
        "blast_radius": "user-visible chat message",
        "rollback": "compensating correction message or deletion if platform supports it",
        "recommended_first": False,
    },
    {
        "adapter": "lark_feishu",
        "mode": "guarded_task_or_message_write",
        "external_side_effects": True,
        "required_authority": "explicit tenant/workspace target + object id + operation",
        "required_inputs": ["tenant_scope", "object_id", "operation", "payload_digest", "idempotency_key"],
        "blast_radius": "workspace-visible task/document/message mutation",
        "rollback": "adapter-specific revert/update receipt",
        "recommended_first": False,
    },
    {
        "adapter": "github",
        "mode": "guarded_issue_pr_comment_or_status_write",
        "external_side_effects": True,
        "required_authority": "explicit repo + issue/pr/ref + operation",
        "required_inputs": ["repo", "object_ref", "operation", "payload_digest", "idempotency_key"],
        "blast_radius": "repository-visible write",
        "rollback": "comment correction/delete or revert commit depending on operation",
        "recommended_first": False,
    },
]

POLICY_REQUIREMENTS = [
    "target_allowlist",
    "purpose_allowlist",
    "idempotency_key_schema",
    "duplicate_policy",
    "rate_limit_per_target",
    "dry_run_preview_required",
    "append_only_receipt_ledger",
    "compensating_action_path",
    "post_apply_readback",
    "rollback_evidence_schema",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def build_packet() -> dict[str, Any]:
    return {
        "report_id": "workflow_kernel_external_dispatch_expansion_20260613_phase47",
        "created_at": iso(utc_now()),
        "status": "EXTERNAL_DISPATCH_EXPANSION_CANDIDATE_READY",
        "objective": "enumerate external dispatch expansion paths after runtime auto-enable without executing external side effects",
        "default_safe_adapter": "local_outbox_simulator",
        "unattended_external_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "selection_status": "target_adapter_options_fully_enumerated_pending_selection",
        "adapter_options": ADAPTER_OPTIONS,
        "policy_requirements": POLICY_REQUIREMENTS,
        "candidate_tracks": [
            {
                "track": "P47A_LOCAL_OUTBOX_ONLY",
                "description": "record would-be external sends to local outbox with receipts only",
                "side_effects": False,
                "recommended": True,
            },
            {
                "track": "P47B_SINGLE_TARGET_DRY_RUN_POLICY",
                "description": "compile dry-run send instructions for exactly one selected external adapter/target",
                "side_effects": False,
                "recommended": False,
            },
            {
                "track": "P47C_GUARDED_SINGLE_SEND",
                "description": "consume one dry-run packet plus exact target/message authorization and send once",
                "side_effects": True,
                "recommended": False,
            },
            {
                "track": "P47D_UNATTENDED_POLICY_GATE",
                "description": "allow unattended sends only after allowlist/rate/idempotency/receipt/rollback gates pass",
                "side_effects": True,
                "recommended": False,
            },
        ],
        "safe_boundaries": [
            "candidate packet only",
            "no send_message call",
            "no lark/github mutation",
            "no credential read/write",
            "no scheduler mutation",
            "no model generation",
        ],
        "next_required_selection": {
            "adapter": "choose one adapter option",
            "target": "exact chat/task/repo/object id for any external side effect",
            "payload": "exact message or operation payload",
            "mode": "local_outbox_only, dry_run_preview, guarded_single_send, or unattended_policy_gate",
        },
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_external_dispatch_expansion_20260613_phase47.json"
    md_path = out_dir / "workflow_kernel_external_dispatch_expansion_20260613_phase47.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel External Dispatch Expansion — Phase 47",
        "",
        f"Status: **{report['status']}**",
        f"Default safe adapter: `{report['default_safe_adapter']}`",
        f"Unattended external dispatch enabled: `{report['unattended_external_dispatch_enabled']}`",
        "",
        "## Adapter options",
    ]
    for option in report["adapter_options"]:
        lines.append(f"- `{option['adapter']}` — mode=`{option['mode']}`, external_side_effects=`{option['external_side_effects']}`, authority=`{option['required_authority']}`")
    lines.extend(["", "## Policy requirements", *[f"- {x}" for x in report["policy_requirements"]]])
    lines.extend(["", "## Safe boundaries", *[f"- {x}" for x in report["safe_boundaries"]]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_packet()
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: adapters={len(report['adapter_options'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
