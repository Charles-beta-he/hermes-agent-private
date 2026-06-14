#!/usr/bin/env python3
"""Production cron wrapper for workflow-kernel limited unattended dispatch.

Designed for Hermes cron no_agent mode:
- output a compact receipt only when a governed send executes;
- stay silent when today's idempotency/daily-limit guard already suppresses send;
- emit an alert for unexpected blockers or runner failures.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path("/Users/charles/.hermes/hermes-agent")
RUNNER = REPO / "scripts" / "workflow_kernel_limited_unattended_sender.py"
EXPECTED_SILENT_BLOCKERS = {"duplicate_idempotency_key", "daily_limit_exhausted"}


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--execute", "--json"],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=180,
    )
    raw = (proc.stdout or "").strip()
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({
            "status": "WORKFLOW_KERNEL_LIMITED_UNATTENDED_WRAPPER_ERROR",
            "reason": "runner_json_parse_failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-1000:],
            "stderr": proc.stderr[-1000:],
        }, ensure_ascii=False, sort_keys=True))
        return 2

    status = receipt.get("status")
    blockers = set(receipt.get("blockers") or [])
    target_receipts = receipt.get("target_receipts") or []
    flattened_blockers = set(blockers)
    for target_receipt in target_receipts:
        flattened_blockers.update(target_receipt.get("blockers") or [])

    if status == "LIMITED_UNATTENDED_DISPATCH_EXECUTED":
        sent = [r for r in target_receipts if r.get("executed") is True]
        print(json.dumps({
            "status": status,
            "executed_count": receipt.get("executed_count"),
            "targets": [r.get("target_id") for r in sent],
            "message_ids": [(r.get("send_result") or {}).get("message_id") for r in sent],
            "day_bucket": receipt.get("day_bucket"),
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if status == "LIMITED_UNATTENDED_DISPATCH_BLOCKED" and flattened_blockers and flattened_blockers.issubset(EXPECTED_SILENT_BLOCKERS):
        # Already sent today or duplicate run in same day; production steady-state should be silent.
        return 0

    if proc.returncode != 0 or status != "LIMITED_UNATTENDED_DISPATCH_READY":
        print(json.dumps({
            "status": "WORKFLOW_KERNEL_LIMITED_UNATTENDED_ALERT",
            "runner_status": status,
            "returncode": proc.returncode,
            "blockers": sorted(flattened_blockers),
            "target_statuses": [{"target_id": r.get("target_id"), "status": r.get("status"), "blockers": r.get("blockers")} for r in target_receipts],
        }, ensure_ascii=False, sort_keys=True))
        return 2

    # Dry-run-ready is not expected in --execute mode, but not dangerous.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
