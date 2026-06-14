#!/usr/bin/env python3
"""Phase 62: unattended dispatch enablement packet.

Produces a fail-closed authorization packet for any future repeated/unattended
external dispatch. This script does not enable unattended dispatch, mutate cron,
or send messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-unattended-dispatch-enablement-packet")
REPORT_JSON = REPORT_DIR / "workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.md"
DECISION_SCHEMA = REPORT_DIR / "workflow_kernel_unattended_dispatch_enablement_decision_schema_20260613_phase62.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def build_packet(root: Path) -> dict[str, Any]:
    phase61 = read_json(root / "docs/private/workflow-kernel-post-send-correlation-watch/workflow_kernel_post_send_correlation_watch_20260613_phase61.json")
    policy = read_json(root / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json")
    cron = read_json(root / "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json")
    phase60 = read_json(root / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json")
    readiness_checks = {
        "phase61_correlation_ready": phase61.get("status") == "POST_SEND_CORRELATION_COMPENSATION_WATCH_READY",
        "phase60_one_shot_verified": phase60.get("status") == "ONE_SHOT_GUARDED_SEND_VERIFIED",
        "phase54_policy_ready": policy.get("status") == "UNATTENDED_DISPATCH_POLICY_GATE_READY",
        "phase57_dry_run_cron_applied": cron.get("status") == "DRY_RUN_CRON_ROLLOUT_APPLIED",
        "current_unattended_disabled": phase60.get("unattended_dispatch_enabled") is False and policy.get("unattended_dispatch_enabled") is False,
    }
    blockers = [k for k, ok in readiness_checks.items() if not ok]
    options = [
        {
            "option": "A_KEEP_MANUAL_ONLY",
            "risk": "low",
            "effect": "Keep Phase59 manual runner and Phase57 dry-run cron only; no repeated external sends.",
            "unattended_dispatch_enabled_after": False,
        },
        {
            "option": "B_APPROVE_DRY_RUN_ONLY_AUTOMATION",
            "risk": "low_medium",
            "effect": "Allow scheduled dry-run receipts to continue, still no external dispatch.",
            "unattended_dispatch_enabled_after": False,
        },
        {
            "option": "C_APPROVE_ONE_TARGET_LIMITED_UNATTENDED_SENDS",
            "risk": "high",
            "effect": "Future phase may enable repeated Telegram home sends with rate limit, idempotency, kill switch, and compensation watch.",
            "requires_future_apply_phase": True,
            "unattended_dispatch_enabled_after": False,
        },
    ]
    controls = {
        "target_allowlist": ["telegram"],
        "max_sends_per_day": 1,
        "requires_dry_run_preview_before_each_send": True,
        "requires_idempotency_key": True,
        "requires_receipt_ledger": True,
        "requires_compensation_path": True,
        "kill_switches": ["policy.unattended_dispatch_enabled=false", "cron job disabled", "manual runner execute disabled"],
        "credential_read_write_allowed": False,
        "cross_adapter_send_allowed": False,
    }
    packet = {
        "schema": "workflow_kernel_unattended_dispatch_enablement_packet_v1",
        "phase": 62,
        "status": "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY" if not blockers else "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_BLOCKED",
        "created_at": iso_now(),
        "readiness_checks": readiness_checks,
        "blockers": blockers,
        "options": options,
        "required_controls_for_future_enablement": controls,
        "recommended_default": "A_KEEP_MANUAL_ONLY",
        "selected_option": None,
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "message_sent": False,
        "next_boundary": "Phase63 explicit unattended enablement decision or Phase64 safe-default manual-only closure",
    }
    packet["packet_digest"] = sha256_obj({k: packet[k] for k in packet if k != "packet_digest"})
    return packet


def decision_schema(packet_digest: str) -> dict[str, Any]:
    return {
        "schema": "workflow_kernel_unattended_dispatch_enablement_decision_schema_v1",
        "phase": 62,
        "packet_digest": packet_digest,
        "allowed_decisions": ["keep_manual_only", "approve_dry_run_only", "approve_limited_unattended_future_phase", "reject", "request_changes"],
        "required_fields_for_limited_unattended_future_phase": ["target", "payload_template", "cadence", "daily_limit", "idempotency_basis", "rollback_path", "compensation_message_policy"],
        "execution_boundary": "This schema records a future decision only; it must not enable unattended dispatch by itself.",
    }


def write_packet(packet: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    schema = decision_schema(packet["packet_digest"])
    DECISION_SCHEMA.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    option_lines = [f"- `{o['option']}` — {o['effect']}" for o in packet["options"]]
    REPORT_MD.write_text("\n".join([
        "# Workflow Kernel Unattended Dispatch Enablement Packet — Phase 62",
        "",
        f"Status: **{packet['status']}**",
        f"Packet digest: `{packet['packet_digest']}`",
        f"Unattended dispatch enabled: `{packet['unattended_dispatch_enabled']}`",
        f"External side effects executed: `{packet['external_side_effects_executed']}`",
        "",
        "## Options",
        "",
        *option_lines,
        "",
        "## Default",
        "",
        f"`{packet['recommended_default']}`",
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
