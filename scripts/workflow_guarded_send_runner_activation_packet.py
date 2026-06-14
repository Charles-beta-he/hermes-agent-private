#!/usr/bin/env python3
"""Phase 58: guarded send runner activation packet.

This compiles the contract for a future guarded manual-send runner. It does not
send messages and does not enable unattended dispatch. The packet is the authority
boundary between approval-ledger intent and any future external side effect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-guarded-send-runner-activation")
REPORT_JSON = REPORT_DIR / "workflow_kernel_guarded_send_runner_activation_20260613_phase58.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_guarded_send_runner_activation_20260613_phase58.md"
CONTRACT_JSON = REPORT_DIR / "workflow_kernel_guarded_send_runner_contract_20260613_phase58.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_packet(root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    phase54 = read_json(root / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json")
    phase56 = read_json(root / "docs/private/workflow-kernel-policy-editor/workflow_kernel_policy_editor_projection_20260613_phase56.json")
    phase57 = read_json(root / "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json")
    latest_decision = phase56.get("latest_decision") or {}
    approved_dry_run_only = latest_decision.get("decision") == "approve_dry_run_only" or latest_decision.get("decision") == "draft"
    contract = {
        "schema": "workflow_kernel_guarded_send_runner_contract_v1",
        "phase": 58,
        "runner_mode": "manual_guarded_send_only",
        "activation_status": "packet_ready_not_activated",
        "allowed_adapter": "telegram_home",
        "required_manual_gate": {
            "requires_exact_target": True,
            "requires_exact_payload": True,
            "requires_operator_reason": True,
            "requires_policy_digest_match": True,
            "requires_idempotency_key": True,
            "requires_pre_send_preview": True,
        },
        "idempotency_fields": ["adapter", "target", "payload_sha256", "policy_digest", "operator_reason"],
        "rate_limit": phase54.get("rate_limits", {"telegram_home": "manual only"}),
        "receipt_required_fields": ["platform", "target", "message_id", "payload_sha256", "sent_at", "operator_reason", "policy_digest"],
        "correction_path": "send compensating correction message only after explicit operator approval",
        "rollback_path": "no automatic rollback for external messages; record correction/compensation receipt",
        "safe_defaults": {
            "unattended_dispatch_enabled": False,
            "external_side_effects_executed": False,
            "send_runner_activated": False,
            "automatic_send_allowed": False,
        },
    }
    digest = sha256_text(json.dumps(contract, ensure_ascii=False, sort_keys=True))
    packet = {
        "schema": "workflow_kernel_guarded_send_runner_activation_packet_v1",
        "phase": 58,
        "status": "GUARDED_SEND_RUNNER_ACTIVATION_PACKET_READY",
        "created_at": iso(utc_now()),
        "contract_digest": digest,
        "contract": contract,
        "inputs": {
            "phase54_status": phase54.get("status"),
            "phase56_status": phase56.get("status"),
            "phase56_latest_decision": latest_decision.get("decision"),
            "phase57_status": phase57.get("status"),
            "phase57_job_id": phase57.get("job_id"),
        },
        "authority_assessment": {
            "dry_run_policy_approved": bool(approved_dry_run_only),
            "manual_send_runner_can_be_implemented_next": True,
            "manual_send_runner_is_active_now": False,
            "unattended_dispatch_enabled": False,
        },
        "execution_boundary": {
            "external_side_effects_executed": False,
            "send_message_called": False,
            "hermes_send_called": False,
            "cron_state_mutated": False,
            "next_boundary": "Phase59 implement guarded manual-send runner vs Phase60 activate one manual guarded send from packet",
        },
    }
    return packet


def write_report(packet: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    CONTRACT_JSON.write_text(json.dumps(packet["contract"], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Workflow Kernel Guarded Send Runner Activation Packet — Phase 58",
        "",
        f"Status: **{packet['status']}**",
        f"Contract digest: `{packet['contract_digest']}`",
        "",
        "## Boundary",
        f"- send_runner_activated: `{packet['contract']['safe_defaults']['send_runner_activated']}`",
        f"- external_side_effects_executed: `{packet['execution_boundary']['external_side_effects_executed']}`",
        f"- unattended_dispatch_enabled: `{packet['authority_assessment']['unattended_dispatch_enabled']}`",
        "",
        "## Required manual gate",
    ]
    for key, value in packet["contract"]["required_manual_gate"].items():
        lines.append(f"- {key}: `{value}`")
    REPORT_MD.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    packet = build_packet()
    if args.write_report:
        write_report(packet)
    if args.json:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
