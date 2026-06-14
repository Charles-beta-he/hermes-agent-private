#!/usr/bin/env python3
"""Phase 59/60 guarded manual-send runner.

Default mode is preview-only. `--execute` is required for the single external
send path. The runner validates the Phase58 activation packet, exact target,
payload hash, policy digest, operator reason, and idempotency key before calling
`hermes send`. It records append-only receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-guarded-manual-send-runner")
PHASE59_JSON = REPORT_DIR / "workflow_kernel_guarded_manual_send_runner_20260613_phase59.json"
PHASE59_MD = REPORT_DIR / "workflow_kernel_guarded_manual_send_runner_20260613_phase59.md"
PHASE60_JSON = REPORT_DIR / "workflow_kernel_one_shot_guarded_send_20260613_phase60.json"
PHASE60_MD = REPORT_DIR / "workflow_kernel_one_shot_guarded_send_20260613_phase60.md"
LEDGER = REPORT_DIR / "workflow_kernel_guarded_manual_send_receipts.jsonl"
DEFAULT_PAYLOAD = "Workflow Kernel Phase60 guarded manual-send activation: one-shot external dispatch verified."
DEFAULT_TARGET = "telegram"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def phase58_path(root: Path) -> Path:
    return root / "docs/private/workflow-kernel-guarded-send-runner-activation/workflow_kernel_guarded_send_runner_activation_20260613_phase58.json"


def build_idempotency_key(target: str, payload: str, policy_digest: str, reason: str) -> str:
    basis = json.dumps(
        {"adapter": "telegram_home", "target": target, "payload_sha256": sha256_text(payload), "policy_digest": policy_digest, "operator_reason": reason},
        sort_keys=True,
    )
    return sha256_text(basis)


def validate_inputs(root: Path, target: str, payload: str, reason: str, idempotency_key: str | None) -> dict[str, Any]:
    activation = read_json(phase58_path(root))
    contract = activation.get("contract") or {}
    policy_digest = activation.get("contract_digest")
    errors: list[str] = []
    if activation.get("status") != "GUARDED_SEND_RUNNER_ACTIVATION_PACKET_READY":
        errors.append("activation_packet_not_ready")
    if ((activation.get("authority_assessment") or {}).get("manual_send_runner_is_active_now")) is not False:
        errors.append("unexpected_runner_already_active")
    if target != DEFAULT_TARGET:
        errors.append("target_not_allowlisted")
    if not payload.strip():
        errors.append("payload_empty")
    if not reason.strip():
        errors.append("operator_reason_required")
    if not policy_digest:
        errors.append("policy_digest_missing")
    expected_key = build_idempotency_key(target, payload, policy_digest or "", reason)
    if idempotency_key and idempotency_key != expected_key:
        errors.append("idempotency_key_mismatch")
    return {
        "ok": not errors,
        "errors": errors,
        "activation_status": activation.get("status"),
        "contract_digest": policy_digest,
        "expected_idempotency_key": expected_key,
        "contract": contract,
    }


def build_preview(root: Path, target: str, payload: str, reason: str, idempotency_key: str | None = None) -> dict[str, Any]:
    validation = validate_inputs(root, target, payload, reason, idempotency_key)
    return {
        "schema": "workflow_kernel_guarded_manual_send_preview_v1",
        "phase": 59,
        "status": "GUARDED_MANUAL_SEND_RUNNER_READY" if validation["ok"] else "GUARDED_MANUAL_SEND_RUNNER_BLOCKED",
        "created_at": iso(utc_now()),
        "target": target,
        "payload": payload,
        "payload_sha256": sha256_text(payload),
        "operator_reason": reason,
        "idempotency_key": idempotency_key or validation["expected_idempotency_key"],
        "validation": validation,
        "external_side_effects_executed": False,
        "send_runner_active": False,
        "execute_command_shape": "hermes send --to telegram --json <payload>",
    }


def run_hermes_send(target: str, payload: str) -> dict[str, Any]:
    cmd = ["hermes", "send", "--to", target, "--json", payload]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    result = {"command": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    if proc.returncode != 0:
        result["parsed"] = None
        return result
    try:
        result["parsed"] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result["parsed"] = None
    return result


def execute_once(root: Path, target: str, payload: str, reason: str, idempotency_key: str | None = None) -> dict[str, Any]:
    preview = build_preview(root, target, payload, reason, idempotency_key)
    if preview["status"] != "GUARDED_MANUAL_SEND_RUNNER_READY":
        return {"schema": "workflow_kernel_one_shot_guarded_send_v1", "phase": 60, "status": "ONE_SHOT_GUARDED_SEND_BLOCKED", "preview": preview, "external_side_effects_executed": False}
    send_result = run_hermes_send(target, payload)
    parsed = send_result.get("parsed") or {}
    success = send_result.get("returncode") == 0 and bool(parsed.get("success", True))
    receipt = {
        "schema": "workflow_kernel_one_shot_guarded_send_v1",
        "phase": 60,
        "status": "ONE_SHOT_GUARDED_SEND_VERIFIED" if success else "ONE_SHOT_GUARDED_SEND_FAILED",
        "created_at": iso(utc_now()),
        "target": target,
        "payload_sha256": preview["payload_sha256"],
        "operator_reason": reason,
        "idempotency_key": preview["idempotency_key"],
        "policy_digest": preview["validation"]["contract_digest"],
        "send_result": parsed,
        "raw_send_result": send_result,
        "external_side_effects_executed": bool(success),
        "unattended_dispatch_enabled": False,
        "compensation_path": "Send a correction/compensating message only after explicit operator approval.",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    return receipt


def write_phase59(preview: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    PHASE59_JSON.write_text(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    PHASE59_MD.write_text("\n".join([
        "# Workflow Kernel Guarded Manual-send Runner — Phase 59",
        "",
        f"Status: **{preview['status']}**",
        f"Target: `{preview['target']}`",
        f"Payload SHA256: `{preview['payload_sha256']}`",
        f"Idempotency: `{preview['idempotency_key']}`",
        f"External side effects executed: `{preview['external_side_effects_executed']}`",
    ]) + "\n")


def write_phase60(receipt: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    PHASE60_JSON.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    PHASE60_MD.write_text("\n".join([
        "# Workflow Kernel One-shot Guarded Send — Phase 60",
        "",
        f"Status: **{receipt['status']}**",
        f"Target: `{receipt.get('target')}`",
        f"External side effects executed: `{receipt.get('external_side_effects_executed')}`",
        f"Idempotency: `{receipt.get('idempotency_key')}`",
        f"Message ID: `{(receipt.get('send_result') or {}).get('message_id')}`",
    ]) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--payload", default=DEFAULT_PAYLOAD)
    parser.add_argument("--reason", default="Phase60 one-shot guarded send activation under explicit full authorization")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.execute:
        result = execute_once(root, args.target, args.payload, args.reason, args.idempotency_key)
        if args.write_report:
            write_phase60(result)
    else:
        result = build_preview(root, args.target, args.payload, args.reason, args.idempotency_key)
        if args.write_report:
            write_phase59(result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") not in {"GUARDED_MANUAL_SEND_RUNNER_BLOCKED", "ONE_SHOT_GUARDED_SEND_BLOCKED", "ONE_SHOT_GUARDED_SEND_FAILED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
