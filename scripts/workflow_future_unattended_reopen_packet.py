#!/usr/bin/env python3
"""Phase 66: future unattended apply reopen packet.

Packet-only design for a possible future explicit reopen/apply phase. It does
not reopen, apply, enable unattended dispatch, mutate cron, or send messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-future-unattended-reopen-packet")
REPORT_JSON = REPORT_DIR / "workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_future_unattended_reopen_packet_20260613_phase66.md"
SCHEMA_JSON = REPORT_DIR / "workflow_kernel_future_unattended_reopen_schema_20260613_phase66.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_packet(root: Path) -> dict[str, Any]:
    monitor = read_json(root / "docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json")
    closure = read_json(root / "docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json")
    checks = {
        "phase65_monitor_clear": monitor.get("status") == "MANUAL_ONLY_MONITORING_CLEAR",
        "phase64_closure_ready": closure.get("status") == "MANUAL_ONLY_CLOSURE_READY",
        "manual_only_unattended_disabled": closure.get("unattended_dispatch_enabled") is False and monitor.get("unattended_dispatch_enabled") is False,
        "monitor_read_only": monitor.get("read_only") is True,
    }
    blockers = [k for k, ok in checks.items() if not ok]
    reopen_options = [
        {
            "option": "A_NO_REOPEN_KEEP_MANUAL_ONLY",
            "effect": "keep the Phase64 closure unchanged",
            "apply_allowed_now": False,
            "required_future_authority": "none",
        },
        {
            "option": "B_REOPEN_DRY_RUN_ONLY_AUTOMATION_DESIGN",
            "effect": "design dry-run-only unattended monitoring without external sends",
            "apply_allowed_now": False,
            "required_future_authority": "explicit design/apply phase with rollback evidence",
        },
        {
            "option": "C_REOPEN_LIMITED_UNATTENDED_SEND_APPLY_DESIGN",
            "effect": "design a limited one-target unattended send apply path",
            "apply_allowed_now": False,
            "required_future_authority": "fresh exact target/payload/rate-limit/idempotency/rollback authorization",
        },
    ]
    packet = {
        "schema": "workflow_kernel_future_unattended_reopen_packet_v1",
        "phase": 66,
        "status": "FUTURE_UNATTENDED_REOPEN_PACKET_READY" if not blockers else "FUTURE_UNATTENDED_REOPEN_PACKET_BLOCKED",
        "created_at": iso_now(),
        "checks": checks,
        "blockers": blockers,
        "phase65_monitor_digest": monitor.get("monitor_digest"),
        "phase64_closure_digest": closure.get("closure_digest"),
        "recommended_default": "A_NO_REOPEN_KEEP_MANUAL_ONLY",
        "selected_option": None,
        "reopen_options": reopen_options,
        "future_apply_requirements": [
            "fresh explicit operator authorization at a future apply phase",
            "exact target adapter and target identifier",
            "exact payload or payload generator with preview evidence",
            "rate limit and idempotency key contract",
            "pre-send dry-run receipt and duplicate check",
            "append-only receipt ledger and Control Tower readback",
            "rollback/kill-switch and compensation/correction path",
        ],
        "reopen_apply_enabled": False,
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "message_sent": False,
        "packet_only": True,
        "next_boundary": "Phase67 explicit single implementation-point choice only if reopening is requested",
    }
    packet["packet_digest"] = sha256_obj({k: packet[k] for k in packet if k != "packet_digest"})
    return packet


def build_schema(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "workflow_kernel_future_unattended_reopen_decision_schema_v1",
        "phase": 66,
        "packet_digest": packet.get("packet_digest"),
        "allowed_decisions": ["keep_closed", "design_dry_run_only_reopen", "design_limited_unattended_send_reopen", "reject", "request_changes"],
        "apply_allowed_by_this_schema": False,
        "required_fields_for_future_apply": ["decision", "operator_reason", "target_adapter", "target_identifier", "payload_contract", "rate_limit", "rollback_plan"],
    }


def write_packet(packet: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    schema = build_schema(packet)
    REPORT_JSON.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    SCHEMA_JSON.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    options = [f"- {item['option']}: {item['effect']}" for item in packet["reopen_options"]]
    REPORT_MD.write_text("\n".join([
        "# Workflow Kernel Future Unattended Reopen Packet — Phase 66",
        "",
        f"Status: **{packet['status']}**",
        f"Packet digest: `{packet['packet_digest']}`",
        f"Recommended default: `{packet['recommended_default']}`",
        f"Reopen apply enabled: `{packet['reopen_apply_enabled']}`",
        f"Unattended dispatch enabled: `{packet['unattended_dispatch_enabled']}`",
        "",
        "## Options",
        *options,
    ]) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    packet = build_packet(Path.cwd())
    if args.write_report:
        write_packet(packet)
    if args.json:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if packet["status"].endswith("READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
