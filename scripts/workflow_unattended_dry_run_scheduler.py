#!/usr/bin/env python3
"""Phase 55: unattended dispatch dry-run scheduler.

This runner exercises the Phase54 policy by writing dry-run preview receipts. It
never sends a message, never calls Telegram/Lark/GitHub adapters, and never
mutates cron state. A future cron job may invoke this script, but this phase only
lands the scheduler-safe runner and local receipt evidence.
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

DEFAULT_POLICY = ROOT / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json"
DEFAULT_OUT = ROOT / "docs/private/workflow-kernel-unattended-dry-run-scheduler"
DEFAULT_LEDGER = DEFAULT_OUT / "unattended_dispatch_dry_run_receipts.jsonl"
DEFAULT_PREVIEW_PAYLOAD = "Workflow Kernel unattended dispatch dry-run preview: no external send executed."


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def read_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_dry_run_receipt(policy_report: dict[str, Any], *, payload: str = DEFAULT_PREVIEW_PAYLOAD, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    policy = policy_report.get("policy") or {}
    targets = policy.get("target_allowlist") or []
    blockers: list[str] = []
    if policy_report.get("status") != "UNATTENDED_DISPATCH_POLICY_GATE_READY":
        blockers.append("policy_gate_not_ready")
    if policy_report.get("unattended_dispatch_enabled") is not False:
        blockers.append("policy_not_fail_closed")
    if not targets:
        blockers.append("target_allowlist_empty")
    target = targets[0] if targets else {}
    purpose = (target.get("allowed_purposes") or ["operator_control_tower_status"])[0]
    payload_digest = sha256_text(payload)
    hour_bucket = now.strftime("%Y%m%dT%H")
    idempotency_material = {
        "adapter": target.get("adapter"),
        "target_type": target.get("target_type"),
        "chat_id": target.get("chat_id"),
        "purpose": purpose,
        "payload_digest": payload_digest,
        "policy_version": policy_report.get("policy_version"),
        "time_bucket_utc_hour": hour_bucket,
    }
    return {
        "schema": "workflow_kernel_unattended_dry_run_receipt_v1",
        "phase": 55,
        "created_at": iso(now),
        "status": "UNATTENDED_DRY_RUN_RECEIPT_RECORDED" if not blockers else "UNATTENDED_DRY_RUN_BLOCKED",
        "mode": "dry_run_preview",
        "would_send": False,
        "external_side_effects_executed": False,
        "unattended_dispatch_enabled": False,
        "adapter": target.get("adapter"),
        "target_type": target.get("target_type"),
        "chat_id": target.get("chat_id"),
        "purpose": purpose,
        "payload_digest": payload_digest,
        "policy_digest": policy_report.get("policy_digest"),
        "policy_version": policy_report.get("policy_version"),
        "time_bucket_utc_hour": hour_bucket,
        "idempotency_key": sha256_obj(idempotency_material),
        "rate_limit_decision": {
            "decision": "dry_run_allowed" if not blockers else "blocked",
            "per_target_per_hour": (policy.get("rate_limits") or {}).get("per_target_per_hour"),
            "per_target_per_day": (policy.get("rate_limits") or {}).get("per_target_per_day"),
        },
        "blockers": blockers,
        "safe_boundaries": [
            "dry-run receipt only",
            "no send_message/hermes send invocation",
            "no credential access",
            "no cron mutation",
            "append-only local ledger",
        ],
    }


def append_receipt(ledger_path: Path, receipt: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def build_report(receipt: dict[str, Any], *, ledger_path: Path, cadence: str) -> dict[str, Any]:
    return {
        "report_id": "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55",
        "created_at": iso(utc_now()),
        "status": "UNATTENDED_DRY_RUN_SCHEDULER_READY" if receipt.get("status") == "UNATTENDED_DRY_RUN_RECEIPT_RECORDED" else "UNATTENDED_DRY_RUN_SCHEDULER_BLOCKED",
        "phase": 55,
        "cadence": cadence,
        "cron_state_mutated": False,
        "external_side_effects_executed": False,
        "unattended_dispatch_enabled": False,
        "receipt_ledger": str(ledger_path),
        "latest_receipt": receipt,
        "next_boundary": "Phase56 Control Tower policy editor / approval UI",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    md_path = out_dir / "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Unattended Dry-run Scheduler — Phase 55",
        "",
        f"Status: **{report['status']}**",
        f"Cadence: `{report['cadence']}`",
        f"Cron state mutated: `{report['cron_state_mutated']}`",
        f"External side effects executed: `{report['external_side_effects_executed']}`",
        f"Unattended dispatch enabled: `{report['unattended_dispatch_enabled']}`",
        f"Latest idempotency key: `{report['latest_receipt']['idempotency_key']}`",
        "",
        "## Safe boundaries",
        *[f"- {x}" for x in report["latest_receipt"]["safe_boundaries"]],
        "",
        f"Next boundary: {report['next_boundary']}",
    ]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--payload", default=DEFAULT_PREVIEW_PAYLOAD)
    ap.add_argument("--cadence", default="daily")
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--append-ledger", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    policy = read_policy(args.policy)
    receipt = build_dry_run_receipt(policy, payload=args.payload)
    if args.append_ledger:
        append_receipt(args.ledger, receipt)
    report = build_report(receipt, ledger_path=args.ledger, cadence=args.cadence)
    if args.write_report:
        report["artifacts"] = write_report(report, args.out_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: would_send={receipt['would_send']} ledger={args.ledger}")
    return 0 if report["status"] == "UNATTENDED_DRY_RUN_SCHEDULER_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
