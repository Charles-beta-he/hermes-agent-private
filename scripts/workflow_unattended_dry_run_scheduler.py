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
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import canonical_json_bytes, write_json_artifact
from workflow_kernel.runtime_state import (
    LedgerAnchor,
    ensure_runtime_artifact_parent,
    exclusive_ledger_lock,
    open_ledger_file,
    seed_jsonl_once,
    unattended_dry_run_dir,
    unattended_dry_run_ledger_path,
    validate_private_file_target,
    write_private_file_atomic,
)

DEFAULT_POLICY = ROOT / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json"
LEGACY_OUT = ROOT / "docs/private/workflow-kernel-unattended-dry-run-scheduler"
LEGACY_LEDGER = LEGACY_OUT / "unattended_dispatch_dry_run_receipts.jsonl"
DEFAULT_OUT = unattended_dry_run_dir()
DEFAULT_LEDGER = unattended_dry_run_ledger_path()
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
    return json.loads(path.read_text(encoding="utf-8"))


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


def _private_root_for_ledger(ledger_path: Path) -> Path | None:
    return DEFAULT_LEDGER.parents[1] if ledger_path == DEFAULT_LEDGER else None


def _append_receipt_unlocked(
    ledger_path: Path,
    receipt: dict[str, Any],
    *,
    private_root: Path | None,
    anchor: LedgerAnchor | None = None,
) -> None:
    private_runtime = private_root is not None if anchor is not None else ensure_runtime_artifact_parent(
        ledger_path,
        private_root=private_root,
    )
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    descriptor = open_ledger_file(
        ledger_path,
        flags,
        private_root=private_root,
        anchor=anchor,
        mode=0o600 if private_runtime else 0o666,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"refusing non-regular or multiply-linked ledger: {ledger_path}")
        if private_runtime:
            os.fchmod(descriptor, 0o600)
        payload = (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        while payload:
            written = os.write(descriptor, payload)
            payload = payload[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_receipt(ledger_path: Path, receipt: dict[str, Any]) -> None:
    private_root = _private_root_for_ledger(ledger_path)
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        if ledger_path == DEFAULT_LEDGER:
            prepare_default_ledger(ledger_path, anchor=anchor)
        _append_receipt_unlocked(
            ledger_path,
            receipt,
            private_root=private_root,
            anchor=anchor,
        )


def prepare_default_ledger(
    ledger_path: Path,
    *,
    anchor: LedgerAnchor | None = None,
) -> dict[str, object]:
    if ledger_path != DEFAULT_LEDGER:
        return {"status": "explicit_path", "path": str(ledger_path)}
    return seed_jsonl_once(
        LEGACY_LEDGER,
        ledger_path,
        private_root=DEFAULT_LEDGER.parents[1],
        anchor=anchor,
    )


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
    json_path = out_dir / "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    md_path = out_dir / "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.md"
    private_root = DEFAULT_OUT.parent if out_dir == DEFAULT_OUT else None
    private_runtime = ensure_runtime_artifact_parent(json_path, private_root=private_root)
    if private_runtime:
        assert private_root is not None
        validate_private_file_target(json_path)
        validate_private_file_target(md_path)
        artifact = write_private_file_atomic(json_path, canonical_json_bytes(report), private_root=private_root)
    else:
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
    if private_runtime:
        write_private_file_atomic(md_path, ("\n".join(lines) + "\n").encode("utf-8"), private_root=private_root)
    else:
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
