#!/usr/bin/env python3
"""Run governed limited unattended workflow-kernel dispatch.

Default is dry-run.  `--execute` sends only to enabled registry targets after
manifest, target, payload, idempotency, rate-limit, and kill-switch validation.
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
from workflow_kernel.payload_guard import classify_payload  # noqa: E402
from workflow_kernel.target_registry import enabled_targets, find_target, registry_snapshot, validate_registry  # noqa: E402

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


def idempotency_key(*, target_id: str, purpose: str, payload_digest: str, manifest_digest: str, day_bucket: str) -> str:
    basis = {
        "target_id": target_id,
        "purpose": purpose,
        "payload_digest": payload_digest,
        "manifest_digest": manifest_digest,
        "day_bucket": day_bucket,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()


def _iter_target_receipts(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for entry in ledger:
        if "target_receipts" in entry:
            receipts.extend(entry.get("target_receipts") or [])
        else:
            receipts.append(entry)
    return receipts


def rate_limit_decision(target: dict[str, Any], ledger: list[dict[str, Any]], day_bucket: str, key: str) -> dict[str, Any]:
    tid = target.get("target_id")
    target_entries = _iter_target_receipts(ledger)
    sent_today = [r for r in target_entries if r.get("day_bucket", day_bucket) == day_bucket and r.get("executed") is True and r.get("target_id") == tid]
    duplicate = any(r.get("idempotency_key") == key and r.get("executed") is True for r in target_entries)
    daily_limit = int(target.get("daily_limit") or 0)
    allowed = not duplicate and len(sent_today) < daily_limit
    reasons: list[str] = []
    if duplicate:
        reasons.append("duplicate_idempotency_key")
    if len(sent_today) >= daily_limit:
        reasons.append("daily_limit_exhausted")
    return {"allowed": allowed, "sent_today": len(sent_today), "duplicate": duplicate, "daily_limit": daily_limit, "reasons": reasons}


def run_send(target: str, payload: str) -> dict[str, Any]:
    cmd = ["hermes", "send", "--to", target, "--json", payload]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    out: dict[str, Any] = {"command": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    try:
        out["parsed"] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out["parsed"] = None
    return out


def build_target_receipt(
    *,
    manifest: dict[str, Any],
    target: dict[str, Any],
    payload: str,
    purpose: str,
    day_bucket: str,
    ledger: list[dict[str, Any]],
    execute: bool,
) -> dict[str, Any]:
    payload_report = classify_payload(payload, purpose=purpose)
    payload_digest = payload_report["payload_sha256"]
    key = idempotency_key(
        target_id=str(target.get("target_id")),
        purpose=purpose,
        payload_digest=payload_digest,
        manifest_digest=str(manifest.get("manifest_digest")),
        day_bucket=day_bucket,
    )
    rate = rate_limit_decision(target, ledger, day_bucket, key)
    blockers: list[str] = []
    if target.get("enabled") is not True:
        blockers.append("target_not_enabled")
    if target.get("kill_switch_enabled") is not False:
        blockers.append("target_kill_switch_enabled")
    if purpose not in (target.get("purpose_allowlist") or []):
        blockers.append("purpose_not_allowlisted_for_target")
    if payload_report["status"] != "PASS":
        blockers.extend(payload_report["errors"])
    if not rate["allowed"]:
        blockers.extend(rate["reasons"])
    status = "LIMITED_UNATTENDED_SEND_READY" if not blockers else "LIMITED_UNATTENDED_SEND_BLOCKED"
    send_result = None
    executed = False
    if execute and not blockers:
        send_result = run_send(str(target["send_target"]), payload)
        parsed = send_result.get("parsed") or {}
        executed = send_result.get("returncode") == 0 and bool(parsed.get("success", True))
        status = "LIMITED_UNATTENDED_SEND_EXECUTED" if executed else "LIMITED_UNATTENDED_SEND_FAILED"
    return {
        "schema": "workflow_kernel_limited_unattended_target_receipt_v2",
        "status": status,
        "executed": executed,
        "target_id": target.get("target_id"),
        "target": target.get("send_target"),
        "adapter": target.get("adapter"),
        "purpose": purpose,
        "payload_digest": payload_digest,
        "payload_guard": payload_report,
        "manifest_digest": manifest.get("manifest_digest"),
        "day_bucket": day_bucket,
        "idempotency_key": key,
        "rate_limit": rate,
        "blockers": blockers,
        "external_side_effects_executed": executed,
        "send_result": (send_result or {}).get("parsed"),
        "raw_send_result": send_result,
        "compensation_path": target.get("compensation_path"),
    }


def build_or_execute(
    manifest_path: Path,
    ledger_path: Path,
    *,
    execute: bool = False,
    target_id: str | None = None,
    payload: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    day_bucket = now.strftime("%Y%m%d")
    manifest = read_json(manifest_path)
    registry = registry_snapshot()
    validation = validate_open_manifest(manifest)
    registry_validation = validate_registry(registry)
    ledger = read_ledger(ledger_path)
    purpose = purpose or str(manifest.get("purpose"))
    payload = payload if payload is not None else str(manifest.get("payload_template"))
    blockers: list[str] = []
    if validation["status"] != "PASS":
        blockers.extend(validation["errors"])
    if registry_validation["status"] != "PASS":
        blockers.extend(registry_validation["errors"])
    if manifest.get("opened") is not True:
        blockers.append("manifest_not_opened")
    if manifest.get("kill_switch_enabled") is not False:
        blockers.append("manifest_kill_switch_enabled")
    targets = [find_target(target_id, registry)] if target_id else enabled_targets(registry)
    targets = [t for t in targets if t]
    if not targets:
        blockers.append("no_selected_enabled_targets")
    target_receipts = [] if blockers else [
        build_target_receipt(manifest=manifest, target=target, payload=payload, purpose=purpose, day_bucket=day_bucket, ledger=ledger, execute=execute)
        for target in targets
    ]
    executed_count = sum(1 for r in target_receipts if r.get("executed") is True)
    blocked_targets = [r for r in target_receipts if r.get("blockers")]
    if blockers:
        status = "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    elif executed_count:
        status = "LIMITED_UNATTENDED_DISPATCH_EXECUTED"
    elif blocked_targets:
        status = "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    else:
        status = "LIMITED_UNATTENDED_DISPATCH_READY"
    receipt = {
        "schema": "workflow_kernel_limited_unattended_dispatch_receipt_v2",
        "created_at": iso(now),
        "status": status,
        "executed": executed_count > 0,
        "dry_run": not execute,
        "day_bucket": day_bucket,
        "manifest_digest": manifest.get("manifest_digest"),
        "registry_digest": registry.get("registry_digest"),
        "target_count": len(targets),
        "executed_count": executed_count,
        "target_receipts": target_receipts,
        "blockers": blockers,
        "external_side_effects_executed": executed_count > 0,
        "enterprise_controls": {
            "multi_target_registry_enabled": registry.get("multi_target_enabled"),
            "cross_adapter_enabled": registry.get("cross_adapter_enabled"),
            "payload_guard_enabled": True,
            "per_target_rate_limit_enabled": True,
        },
    }
    if execute or blockers or blocked_targets:
        append_ledger(ledger_path, receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--target-id")
    parser.add_argument("--payload")
    parser.add_argument("--purpose")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = build_or_execute(args.manifest, args.ledger, execute=args.execute, target_id=args.target_id, payload=args.payload, purpose=args.purpose)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"workflow_kernel_limited_unattended_dispatch: {result['status']} executed={result['executed']} targets={result['target_count']}")
    return 0 if result["status"] in {"LIMITED_UNATTENDED_DISPATCH_READY", "LIMITED_UNATTENDED_DISPATCH_EXECUTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
