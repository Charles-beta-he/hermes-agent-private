#!/usr/bin/env python3
"""Phase 54: compile unattended external-dispatch policy gate.

This policy gate is intentionally fail-closed. It defines allowlists, rate
limits, idempotency, duplicate handling, dry-run preview requirements, receipt
ledger, correction path, monitoring, and kill switch requirements, but it does
not enable unattended sends and does not call any external adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-unattended-dispatch-policy"
PHASE50 = ROOT / "docs" / "private" / "workflow-kernel-guarded-external-send" / "workflow_kernel_guarded_external_send_20260613_phase50.json"
PHASE52 = ROOT / "docs" / "private" / "workflow-kernel-control-tower-projection" / "workflow_kernel_control_tower_projection_20260613_phase52.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_policy(*, phase50_path: Path = PHASE50, phase52_path: Path = PHASE52) -> dict[str, Any]:
    phase50 = read_json(phase50_path)
    phase52 = read_json(phase52_path)
    blockers: list[str] = []
    if phase50.get("status") != "GUARDED_EXTERNAL_SEND_VERIFIED":
        blockers.append("phase50_guarded_send_not_verified")
    if phase52.get("status") != "CONTROL_TOWER_UI_API_PROJECTION_READY":
        blockers.append("phase52_control_tower_projection_not_ready")
    target = {
        "adapter": phase50.get("adapter"),
        "target_type": (phase50.get("target") or {}).get("type"),
        "chat_id": (phase50.get("send_result") or {}).get("chat_id"),
        "allowed_purposes": ["workflow_kernel_status_smoke", "operator_control_tower_status"],
        "human_review_required_for_payloads": True,
    }
    policy_core = {
        "schema": "workflow_kernel_unattended_dispatch_policy_v1",
        "target_allowlist": [target],
        "rate_limits": {
            "per_target_per_hour": 1,
            "per_target_per_day": 3,
            "burst": 1,
        },
        "idempotency_key_fields": [
            "adapter",
            "target_type",
            "chat_id",
            "purpose",
            "payload_digest",
            "policy_version",
            "time_bucket_utc_hour",
        ],
        "duplicate_policy": {
            "same_idempotency_key": "suppress_and_record_duplicate_receipt",
            "same_payload_same_target_within_24h": "require_human_review",
        },
        "dry_run_preview_required": True,
        "receipt_ledger": str(ROOT / "docs/private/workflow-kernel-unattended-dispatch-policy/unattended_dispatch_receipts.jsonl"),
        "correction_path": "send compensating correction message to the same target; destructive rollback is unavailable for delivered messages",
        "monitoring": [
            "Control Tower workflow_kernel_control_tower.unattended_dispatch_policy",
            "receipt ledger append result",
            "proof-check accepted evidence marker",
        ],
        "kill_switches": [
            "unattended_dispatch_enabled=false",
            "HERMES_WORKFLOW_KERNEL_UNATTENDED_DISPATCH=0",
            "target allowlist empty or target disabled",
        ],
    }
    policy_digest = sha256_obj(policy_core)
    return {
        "report_id": "workflow_kernel_unattended_dispatch_policy_20260613_phase54",
        "created_at": iso(utc_now()),
        "status": "UNATTENDED_DISPATCH_POLICY_GATE_READY" if not blockers else "UNATTENDED_DISPATCH_POLICY_GATE_BLOCKED",
        "phase": 54,
        "policy_version": "20260613.phase54.v1",
        "policy_digest": policy_digest,
        "default_mode": "dry_run_preview",
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "policy": policy_core,
        "preconditions": {
            "phase50_status": phase50.get("status"),
            "phase52_status": phase52.get("status"),
            "latest_guarded_message_id": (phase50.get("send_result") or {}).get("message_id"),
        },
        "blockers": blockers,
        "safe_boundaries": [
            "policy compiler only",
            "default dry-run preview",
            "unattended dispatch disabled",
            "no external send",
            "no credential read/write",
            "no scheduler mutation",
        ],
        "next_boundary": "larger divergence: enable a single unattended dry-run scheduler or integrate policy editor/approval UI",
    }


def build_dry_run_instruction(policy_report: dict[str, Any], *, purpose: str = "operator_control_tower_status", payload: str = "Workflow Kernel unattended dispatch dry-run preview") -> dict[str, Any]:
    target = policy_report["policy"]["target_allowlist"][0]
    payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    instruction = {
        "schema": "workflow_kernel_unattended_dispatch_dry_run_instruction_v1",
        "mode": "dry_run_preview",
        "adapter": target["adapter"],
        "target_type": target["target_type"],
        "chat_id": target["chat_id"],
        "purpose": purpose,
        "payload_digest": payload_digest,
        "policy_digest": policy_report["policy_digest"],
        "would_send": False,
        "requires_human_review_before_apply": True,
    }
    instruction["idempotency_key"] = sha256_obj({
        "adapter": instruction["adapter"],
        "target_type": instruction["target_type"],
        "chat_id": instruction["chat_id"],
        "purpose": instruction["purpose"],
        "payload_digest": instruction["payload_digest"],
        "policy_digest": instruction["policy_digest"],
        "time_bucket_utc_hour": "DRY_RUN_PREVIEW_ONLY",
    })
    return instruction


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_unattended_dispatch_policy_20260613_phase54.json"
    md_path = out_dir / "workflow_kernel_unattended_dispatch_policy_20260613_phase54.md"
    instruction_path = out_dir / "workflow_kernel_unattended_dispatch_dry_run_instruction_20260613_phase54.json"
    artifact = write_json_artifact(json_path, report)
    instruction = build_dry_run_instruction(report)
    write_json_artifact(instruction_path, instruction)
    md_path.write_text("\n".join([
        "# Workflow Kernel Unattended Dispatch Policy Gate — Phase 54",
        "",
        f"Status: **{report['status']}**",
        f"Default mode: `{report['default_mode']}`",
        f"Unattended dispatch enabled: `{report['unattended_dispatch_enabled']}`",
        f"Policy digest: `{report['policy_digest']}`",
        "",
        "## Rate limits",
        *[f"- {k}: {v}" for k, v in report["policy"]["rate_limits"].items()],
        "",
        "## Safe boundaries",
        *[f"- {x}" for x in report["safe_boundaries"]],
        "",
        f"Next boundary: {report['next_boundary']}",
    ]) + "\n")
    return {"json": str(json_path), "md": str(md_path), "dry_run_instruction": str(instruction_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase50", type=Path, default=PHASE50)
    ap.add_argument("--phase52", type=Path, default=PHASE52)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_policy(phase50_path=args.phase50, phase52_path=args.phase52)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: enabled={report['unattended_dispatch_enabled']} mode={report['default_mode']}")
    return 0 if report["status"] == "UNATTENDED_DISPATCH_POLICY_GATE_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
