#!/usr/bin/env python3
"""Phase 61: post-send correlation and compensation watch.

Read-only: correlates Phase59 preview, Phase60 one-shot receipt, receipt ledger,
and Control Tower-compatible fields. It does not send messages, mutate cron, or
enable unattended dispatch.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-post-send-correlation-watch")
REPORT_JSON = REPORT_DIR / "workflow_kernel_post_send_correlation_watch_20260613_phase61.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_post_send_correlation_watch_20260613_phase61.md"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_report(root: Path) -> dict[str, Any]:
    runner_dir = root / "docs/private/workflow-kernel-guarded-manual-send-runner"
    phase59 = read_json(runner_dir / "workflow_kernel_guarded_manual_send_runner_20260613_phase59.json")
    phase60 = read_json(runner_dir / "workflow_kernel_one_shot_guarded_send_20260613_phase60.json")
    ledger = read_ledger(runner_dir / "workflow_kernel_guarded_manual_send_receipts.jsonl")
    send_result = phase60.get("send_result") or {}
    message_id = send_result.get("message_id")
    chat_id = send_result.get("chat_id")
    matching_ledger_rows = [r for r in ledger if (r.get("send_result") or {}).get("message_id") == message_id]
    checks = {
        "phase59_runner_ready": phase59.get("status") == "GUARDED_MANUAL_SEND_RUNNER_READY",
        "phase59_no_external_side_effect": phase59.get("external_side_effects_executed") is False,
        "phase60_verified": phase60.get("status") == "ONE_SHOT_GUARDED_SEND_VERIFIED",
        "message_id_present": bool(message_id),
        "chat_id_present": bool(chat_id),
        "idempotency_correlated": bool(phase59.get("idempotency_key")) and phase59.get("idempotency_key") == phase60.get("idempotency_key"),
        "payload_hash_correlated": bool(phase59.get("payload_sha256")) and phase59.get("payload_sha256") == phase60.get("payload_sha256"),
        "ledger_contains_receipt": bool(matching_ledger_rows),
        "unattended_still_disabled": phase60.get("unattended_dispatch_enabled") is False,
        "compensation_path_present": bool(phase60.get("compensation_path")),
    }
    blockers = [k for k, ok in checks.items() if not ok]
    return {
        "schema": "workflow_kernel_post_send_correlation_watch_v1",
        "phase": 61,
        "status": "POST_SEND_CORRELATION_COMPENSATION_WATCH_READY" if not blockers else "POST_SEND_CORRELATION_COMPENSATION_WATCH_BLOCKED",
        "created_at": iso_now(),
        "message_id": message_id,
        "chat_id": chat_id,
        "idempotency_key": phase60.get("idempotency_key"),
        "payload_sha256": phase60.get("payload_sha256"),
        "checks": checks,
        "blockers": blockers,
        "ledger_rows": len(ledger),
        "matching_ledger_rows": len(matching_ledger_rows),
        "compensation_watch": {
            "status": "ready" if checks["compensation_path_present"] else "missing",
            "path": phase60.get("compensation_path"),
            "automatic_compensation_enabled": False,
            "new_message_sent": False,
        },
        "external_side_effects_executed": False,
        "unattended_dispatch_enabled": False,
        "next_boundary": "Phase62 unattended dispatch enablement packet",
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    blocker_lines = [f"- {b}" for b in report.get("blockers", [])] or ["- none"]
    lines = [
        "# Workflow Kernel Post-send Correlation Watch — Phase 61",
        "",
        f"Status: **{report['status']}**",
        f"Message ID: `{report.get('message_id')}`",
        f"Chat ID: `{report.get('chat_id')}`",
        f"Ledger rows: `{report.get('ledger_rows')}`",
        f"Matching ledger rows: `{report.get('matching_ledger_rows')}`",
        f"Unattended dispatch enabled: `{report.get('unattended_dispatch_enabled')}`",
        f"New message sent: `{report['compensation_watch']['new_message_sent']}`",
        "",
        "## Blockers",
        "",
        *blocker_lines,
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(Path.cwd())
    if args.write_report:
        write_report(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"].endswith("READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
