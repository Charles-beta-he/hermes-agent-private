#!/usr/bin/env python3
"""Phase 63: explicit unattended enablement decision record.

Records a decision from the Phase62 decision schema. Safe default is
`keep_manual_only`. This script is record-only: it does not enable unattended
dispatch, mutate cron, or send messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-unattended-decision-record")
REPORT_JSON = REPORT_DIR / "workflow_kernel_unattended_decision_record_20260613_phase63.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_unattended_decision_record_20260613_phase63.md"
LEDGER = REPORT_DIR / "workflow_kernel_unattended_decisions.jsonl"
DEFAULT_DECISION = "keep_manual_only"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_record(root: Path, decision: str = DEFAULT_DECISION, reason: str = "Safe-default manual-only closure path") -> dict[str, Any]:
    packet_path = root / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json"
    schema_path = root / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_decision_schema_20260613_phase62.json"
    packet = read_json(packet_path)
    schema = read_json(schema_path)
    allowed = schema.get("allowed_decisions") or []
    checks = {
        "phase62_packet_ready": packet.get("status") == "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY",
        "decision_allowed": decision in allowed,
        "packet_digest_matches_schema": bool(packet.get("packet_digest")) and packet.get("packet_digest") == schema.get("packet_digest"),
        "packet_unattended_disabled": packet.get("unattended_dispatch_enabled") is False,
    }
    blockers = [k for k, ok in checks.items() if not ok]
    record = {
        "schema": "workflow_kernel_unattended_decision_record_v1",
        "phase": 63,
        "status": "UNATTENDED_DECISION_RECORD_READY" if not blockers else "UNATTENDED_DECISION_RECORD_BLOCKED",
        "created_at": iso_now(),
        "decision": decision,
        "reason": reason,
        "phase62_packet_digest": packet.get("packet_digest"),
        "checks": checks,
        "blockers": blockers,
        "selected_option": "A_KEEP_MANUAL_ONLY" if decision == "keep_manual_only" else decision,
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "message_sent": False,
        "record_only": True,
        "next_boundary": "Phase64 safe-default manual-only closure",
    }
    record["record_digest"] = sha256_obj({k: record[k] for k in record if k != "record_digest"})
    return record


def write_record(record: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    REPORT_MD.write_text("\n".join([
        "# Workflow Kernel Unattended Decision Record — Phase 63",
        "",
        f"Status: **{record['status']}**",
        f"Decision: `{record['decision']}`",
        f"Selected option: `{record['selected_option']}`",
        f"Record digest: `{record['record_digest']}`",
        f"Unattended dispatch enabled: `{record['unattended_dispatch_enabled']}`",
        f"External side effects executed: `{record['external_side_effects_executed']}`",
    ]) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", default=DEFAULT_DECISION)
    parser.add_argument("--reason", default="Safe-default manual-only closure path")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    record = build_record(Path.cwd(), args.decision, args.reason)
    if args.write_report:
        write_record(record)
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"].endswith("READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
