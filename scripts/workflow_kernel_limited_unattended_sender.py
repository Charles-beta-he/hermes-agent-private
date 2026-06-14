#!/usr/bin/env python3
"""Run one governed limited unattended workflow-kernel send.

Default is dry-run.  `--execute` performs exactly one Telegram home send only if
an opened manifest is valid, rate limits pass, idempotency has not already been
used, and the manifest kill switch is open.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workflow_kernel.open_gate import validate_open_manifest  # noqa: E402

DEFAULT_MANIFEST = REPO_ROOT / "docs/private/workflow-kernel-open-gate/workflow_kernel_open_manifest_20260614.json"
DEFAULT_LEDGER = REPO_ROOT / "docs/private/workflow-kernel-open-gate/workflow_kernel_limited_unattended_receipts.jsonl"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_ledger(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def idempotency_key(manifest: dict[str, Any], day_bucket: str) -> str:
    basis = {
        "target": manifest.get("target"),
        "purpose": manifest.get("purpose"),
        "payload_digest": manifest.get("payload_digest"),
        "manifest_digest": manifest.get("manifest_digest"),
        "day_bucket": day_bucket,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()


def rate_limit_decision(manifest: dict[str, Any], ledger: list[dict[str, Any]], day_bucket: str, key: str) -> dict[str, Any]:
    sent_today = [r for r in ledger if r.get("day_bucket") == day_bucket and r.get("executed") is True and r.get("target") == manifest.get("target")]
    duplicate = any(r.get("idempotency_key") == key and r.get("executed") is True for r in ledger)
    allowed = not duplicate and len(sent_today) < int(manifest.get("daily_limit") or 0)
    reasons: list[str] = []
    if duplicate:
        reasons.append("duplicate_idempotency_key")
    if len(sent_today) >= int(manifest.get("daily_limit") or 0):
        reasons.append("daily_limit_exhausted")
    return {"allowed": allowed, "sent_today": len(sent_today), "duplicate": duplicate, "reasons": reasons}


def run_send(target: str, payload: str) -> dict[str, Any]:
    cmd = ["hermes", "send", "--to", target, "--json", payload]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    out: dict[str, Any] = {"command": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    try:
        out["parsed"] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out["parsed"] = None
    return out


def build_or_execute(manifest_path: Path, ledger_path: Path, *, execute: bool = False) -> dict[str, Any]:
    now = utc_now()
    day_bucket = now.strftime("%Y%m%d")
    manifest = read_json(manifest_path)
    validation = validate_open_manifest(manifest)
    ledger = read_ledger(ledger_path)
    key = idempotency_key(manifest, day_bucket)
    rate = rate_limit_decision(manifest, ledger, day_bucket, key)
    blockers: list[str] = []
    if validation["status"] != "PASS":
        blockers.extend(validation["errors"])
    if manifest.get("opened") is not True:
        blockers.append("manifest_not_opened")
    if manifest.get("kill_switch_enabled") is not False:
        blockers.append("kill_switch_enabled")
    if not rate["allowed"]:
        blockers.extend(rate["reasons"])
    status = "LIMITED_UNATTENDED_SEND_READY" if not blockers else "LIMITED_UNATTENDED_SEND_BLOCKED"
    send_result = None
    executed = False
    if execute and not blockers:
        send_result = run_send(str(manifest["target"]), str(manifest["payload_template"] ))
        parsed = send_result.get("parsed") or {}
        executed = send_result.get("returncode") == 0 and bool(parsed.get("success", True))
        status = "LIMITED_UNATTENDED_SEND_EXECUTED" if executed else "LIMITED_UNATTENDED_SEND_FAILED"
    receipt = {
        "schema": "workflow_kernel_limited_unattended_send_receipt_v1",
        "created_at": iso(now),
        "status": status,
        "executed": executed,
        "dry_run": not execute,
        "target": manifest.get("target"),
        "purpose": manifest.get("purpose"),
        "payload_digest": manifest.get("payload_digest"),
        "manifest_digest": manifest.get("manifest_digest"),
        "day_bucket": day_bucket,
        "idempotency_key": key,
        "rate_limit": rate,
        "blockers": blockers,
        "external_side_effects_executed": executed,
        "send_result": (send_result or {}).get("parsed"),
        "raw_send_result": send_result,
        "compensation_path": "Send correction only after explicit operator approval.",
    }
    if execute or blockers:
        append_ledger(ledger_path, receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = build_or_execute(args.manifest, args.ledger, execute=args.execute)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"workflow_kernel_limited_unattended_sender: {result['status']} executed={result['executed']}")
    return 0 if result["status"] in {"LIMITED_UNATTENDED_SEND_READY", "LIMITED_UNATTENDED_SEND_EXECUTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
