#!/usr/bin/env python3
"""Run governed limited unattended workflow-kernel dispatch.

Default is dry-run.  `--execute` sends only to enabled registry targets after
manifest, target, payload, idempotency, rate-limit, and kill-switch validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
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
from workflow_kernel.runtime_state import (  # noqa: E402
    LedgerAnchor,
    ensure_runtime_artifact_parent,
    exclusive_ledger_lock,
    limited_unattended_ledger_path,
    open_ledger_file,
    read_ledger_bytes,
    seed_jsonl_once,
)
from workflow_kernel.target_registry import enabled_targets, find_target, registry_snapshot, validate_registry  # noqa: E402

DEFAULT_MANIFEST = REPO_ROOT / "docs/private/workflow-kernel-open-gate/workflow_kernel_open_manifest_20260614.json"
LEGACY_LEDGER = REPO_ROOT / "docs/private/workflow-kernel-open-gate/workflow_kernel_limited_unattended_receipts.jsonl"
DEFAULT_LEDGER = limited_unattended_ledger_path()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_ledger(
    path: Path,
    *,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
) -> list[dict[str, Any]]:
    if anchor is None and not path.exists():
        return []
    try:
        text = read_ledger_bytes(path, private_root=private_root, anchor=anchor).decode("utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _private_root_for(path: Path) -> Path | None:
    return DEFAULT_LEDGER.parents[1] if path == DEFAULT_LEDGER else None


def _append_ledger_unlocked(
    path: Path,
    receipt: dict[str, Any],
    *,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
) -> None:
    private_runtime = private_root is not None if anchor is not None else ensure_runtime_artifact_parent(
        path,
        private_root=private_root,
    )
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    descriptor = open_ledger_file(
        path,
        flags,
        private_root=private_root,
        anchor=anchor,
        mode=0o600 if private_runtime else 0o666,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"refusing non-regular or multiply-linked ledger: {path}")
        if private_runtime:
            os.fchmod(descriptor, 0o600)
        payload = (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        while payload:
            written = os.write(descriptor, payload)
            payload = payload[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_ledger(path: Path, receipt: dict[str, Any]) -> None:
    private_root = _private_root_for(path)
    with exclusive_ledger_lock(path, private_root=private_root) as anchor:
        _append_ledger_unlocked(path, receipt, private_root=private_root, anchor=anchor)


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
    executed_keys = {
        str(receipt.get("idempotency_key"))
        for receipt in target_entries
        if receipt.get("executed") is True and receipt.get("idempotency_key")
    }
    pending = [
        receipt
        for receipt in target_entries
        if receipt.get("reservation_state") == "pending"
        and receipt.get("target_id") == tid
        and str(receipt.get("idempotency_key")) not in executed_keys
    ]
    consumed_keys = {
        str(receipt.get("idempotency_key"))
        for receipt in target_entries
        if receipt.get("day_bucket", day_bucket) == day_bucket
        and receipt.get("target_id") == tid
        and (receipt.get("executed") is True or receipt.get("reservation_state") == "pending")
        and receipt.get("idempotency_key")
    }
    duplicate_executed = any(
        receipt.get("idempotency_key") == key and receipt.get("executed") is True for receipt in target_entries
    )
    duplicate_pending = any(receipt.get("idempotency_key") == key for receipt in pending)
    daily_limit = int(target.get("daily_limit") or 0)
    allowed = not duplicate_executed and not duplicate_pending and len(consumed_keys) < daily_limit
    reasons: list[str] = []
    if duplicate_executed:
        reasons.append("duplicate_idempotency_key")
    if duplicate_pending:
        reasons.append("pending_idempotency_reservation")
    if len(consumed_keys) >= daily_limit:
        reasons.append("daily_limit_exhausted")
    return {
        "allowed": allowed,
        "sent_today": len(consumed_keys),
        "duplicate": duplicate_executed,
        "pending": duplicate_pending,
        "daily_limit": daily_limit,
        "reasons": reasons,
    }


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
    ledger_path: Path,
    private_root: Path | None,
    execute: bool,
    anchor: LedgerAnchor | None = None,
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
        reservation = {
            "schema": "workflow_kernel_limited_unattended_reservation_v1",
            "created_at": iso(utc_now()),
            "status": "LIMITED_UNATTENDED_SEND_PENDING",
            "reservation_state": "pending",
            "executed": False,
            "target_id": target.get("target_id"),
            "target": target.get("send_target"),
            "purpose": purpose,
            "payload_digest": payload_digest,
            "manifest_digest": manifest.get("manifest_digest"),
            "day_bucket": day_bucket,
            "idempotency_key": key,
            "external_side_effects_executed": False,
        }
        _append_ledger_unlocked(
            ledger_path,
            reservation,
            private_root=private_root,
            anchor=anchor,
        )
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


def _build_or_execute_unlocked(
    manifest_path: Path,
    ledger_path: Path,
    *,
    execute: bool = False,
    target_id: str | None = None,
    payload: str | None = None,
    purpose: str | None = None,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
) -> dict[str, Any]:
    now = utc_now()
    day_bucket = now.strftime("%Y%m%d")
    manifest = read_json(manifest_path)
    registry = registry_snapshot()
    validation = validate_open_manifest(manifest)
    registry_validation = validate_registry(registry)
    ledger = read_ledger(ledger_path, private_root=private_root, anchor=anchor)
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
    private_root = private_root if private_root is not None else _private_root_for(ledger_path)
    target_receipts = [] if blockers else [
        build_target_receipt(
            manifest=manifest,
            target=target,
            payload=payload,
            purpose=purpose,
            day_bucket=day_bucket,
            ledger=ledger,
            ledger_path=ledger_path,
            private_root=private_root,
            anchor=anchor,
            execute=execute,
        )
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
    if execute:
        _append_ledger_unlocked(
            ledger_path,
            receipt,
            private_root=private_root,
            anchor=anchor,
        )
    return receipt


def _ledger_path_security_blocker(ledger_path: Path) -> str | None:
    if os.name == "nt" or not int(getattr(os, "O_NOFOLLOW", 0)):
        return "production_runtime_permissions_unsupported"
    for ancestor in (ledger_path.parent, *ledger_path.parent.parents):
        try:
            ancestor_info = os.lstat(ancestor)
        except FileNotFoundError:
            continue
        except OSError:
            return "production_ledger_ancestor_unreadable"
        if stat.S_ISLNK(ancestor_info.st_mode):
            return "production_ledger_ancestor_symlink"
        if not stat.S_ISDIR(ancestor_info.st_mode):
            return "production_ledger_ancestor_not_directory"
    try:
        ledger_info = os.lstat(ledger_path)
    except FileNotFoundError:
        ledger_info = None
    except OSError:
        return "production_ledger_unreadable"
    if ledger_info is not None:
        if stat.S_ISLNK(ledger_info.st_mode):
            return "production_ledger_symlink"
        if not stat.S_ISREG(ledger_info.st_mode):
            return "production_ledger_not_regular_file"
        if ledger_info.st_nlink != 1:
            return "production_ledger_multiply_linked"
        if ledger_info.st_mode & 0o022:
            return "production_ledger_not_private"
    try:
        parent_info = os.lstat(ledger_path.parent)
    except FileNotFoundError:
        return None if ledger_path == DEFAULT_LEDGER else "production_ledger_parent_missing"
    except OSError:
        return "production_ledger_parent_unreadable"
    if stat.S_ISLNK(parent_info.st_mode):
        return "production_ledger_parent_symlink"
    if not stat.S_ISDIR(parent_info.st_mode):
        return "production_ledger_parent_not_directory"
    if parent_info.st_mode & 0o022:
        return "production_ledger_parent_not_private"
    if hasattr(os, "getuid") and parent_info.st_uid != os.getuid():
        return "production_ledger_parent_not_owned"
    return None


def _execute_ledger_blocker(
    ledger_path: Path,
    *,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
) -> str | None:
    if anchor is None:
        security_blocker = _ledger_path_security_blocker(ledger_path)
        if security_blocker:
            return security_blocker
        if not ledger_path.exists():
            return "production_ledger_missing"
        try:
            text = ledger_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "production_ledger_invalid_encoding"
        except OSError:
            return "production_ledger_unreadable"
    else:
        parent_info = os.fstat(anchor.directory_descriptor)
        if not stat.S_ISDIR(parent_info.st_mode):
            return "production_ledger_parent_not_directory"
        if parent_info.st_mode & 0o022:
            return "production_ledger_parent_not_private"
        if hasattr(os, "getuid") and parent_info.st_uid != os.getuid():
            return "production_ledger_parent_not_owned"
        try:
            descriptor = open_ledger_file(
                ledger_path,
                os.O_RDONLY,
                private_root=private_root,
                anchor=anchor,
            )
        except FileNotFoundError:
            return "production_ledger_missing"
        except OSError:
            return "production_ledger_unreadable"
        try:
            info = os.fstat(descriptor)
            if info.st_mode & 0o022:
                return "production_ledger_not_private"
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            try:
                text = b"".join(chunks).decode("utf-8")
            except UnicodeDecodeError:
                return "production_ledger_invalid_encoding"
        finally:
            os.close(descriptor)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "production_ledger_empty"
    try:
        entries = [json.loads(line) for line in lines]
    except json.JSONDecodeError:
        return "production_ledger_invalid_json"
    if not all(isinstance(entry, dict) for entry in entries):
        return "production_ledger_invalid_entry"
    return None


def _blocked_execute_receipt(blocker: str) -> dict[str, Any]:
    return {
        "schema": "workflow_kernel_limited_unattended_dispatch_receipt_v2",
        "created_at": iso(utc_now()),
        "status": "LIMITED_UNATTENDED_DISPATCH_BLOCKED",
        "executed": False,
        "dry_run": False,
        "target_count": 0,
        "executed_count": 0,
        "target_receipts": [],
        "blockers": [blocker],
        "external_side_effects_executed": False,
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
    if not execute:
        return _build_or_execute_unlocked(
            manifest_path,
            ledger_path,
            execute=False,
            target_id=target_id,
            payload=payload,
            purpose=purpose,
        )
    preflight_blocker = _ledger_path_security_blocker(ledger_path)
    if preflight_blocker == "production_runtime_permissions_unsupported":
        return _blocked_execute_receipt(preflight_blocker)
    if preflight_blocker and ledger_path != DEFAULT_LEDGER:
        return _blocked_execute_receipt(preflight_blocker)
    if preflight_blocker in {
        "production_ledger_symlink",
        "production_ledger_not_regular_file",
        "production_ledger_multiply_linked",
    }:
        return _blocked_execute_receipt(preflight_blocker)
    private_root = _private_root_for(ledger_path)
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        if ledger_path == DEFAULT_LEDGER:
            prepare_default_ledger(ledger_path, anchor=anchor)
        blocker = _execute_ledger_blocker(
            ledger_path,
            private_root=private_root,
            anchor=anchor,
        )
        if blocker:
            return _blocked_execute_receipt(blocker)
        return _build_or_execute_unlocked(
            manifest_path,
            ledger_path,
            execute=True,
            target_id=target_id,
            payload=payload,
            purpose=purpose,
            private_root=private_root,
            anchor=anchor,
        )


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
