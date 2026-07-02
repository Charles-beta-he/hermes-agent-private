"""``hermes complex-task-harness`` local readback commands.

The command is intentionally local-only: it reads/appends complex-task harness
receipts and cannot mark tasks done or perform external side effects.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from hermes_cli.complex_task_harness import (
    checkpoint,
    config_status,
    external_dispatch,
    status,
    taskpool_lifecycle,
    verifier_packets,
)


def _emit(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"mode: {payload.get('mode')}")
        print(f"ledger: {payload.get('ledger_path')}")
        if "event_count" in payload:
            print(f"events: {payload.get('event_count')}")
            print(f"latest_scan_id: {payload.get('latest_scan_id')}")
        if "receipt" in payload:
            receipt = payload.get("receipt") or {}
            print(f"receipt: {receipt.get('event_type')} {receipt.get('status')} {receipt.get('scan_id')}")
        print("external_side_effects_enabled: false")
        print("allowed_to_mark_done: false")
    return 0 if payload.get("ok", False) else 1


def cmd_complex_task_harness(args: argparse.Namespace) -> int:
    action = getattr(args, "complex_task_harness_action", None) or "status"
    as_json = bool(getattr(args, "json", False))
    ledger_path = getattr(args, "ledger", None)
    if action == "checkpoint":
        return _emit(
            checkpoint(
                scan_id=getattr(args, "scan_id", ""),
                objective=getattr(args, "objective", ""),
                completed_evidence=getattr(args, "completed_evidence", []) or [],
                pending_actions=getattr(args, "pending_action", []) or [],
                rollback=getattr(args, "rollback", ""),
                ledger_path=ledger_path,
            ),
            as_json=as_json,
        )
    if action == "config":
        from hermes_cli.config import load_config

        return _emit(config_status(load_config()), as_json=as_json)
    if action == "verifier":
        return _emit(
            verifier_packets(
                ledger_path=ledger_path,
                scan_id=getattr(args, "scan_id", "") or "",
                limit=getattr(args, "limit", 20),
            ),
            as_json=as_json,
        )
    if action == "taskpool-lifecycle":
        return _emit(
            taskpool_lifecycle(
                scan_id=getattr(args, "scan_id", ""),
                task_id=getattr(args, "task_id", ""),
                from_status=getattr(args, "from_status", ""),
                to_status=getattr(args, "to_status", ""),
                evidence_ref=getattr(args, "evidence_ref", ""),
                reason=getattr(args, "reason", ""),
                readback_ref=getattr(args, "readback_ref", ""),
                execute=bool(getattr(args, "execute", False)),
                ledger_path=ledger_path,
            ),
            as_json=as_json,
        )
    if action == "external-dispatch":
        return _emit(
            external_dispatch(
                scan_id=getattr(args, "scan_id", ""),
                platform=getattr(args, "platform", ""),
                target=getattr(args, "target", ""),
                payload=getattr(args, "payload", ""),
                payload_sha256=getattr(args, "payload_sha256", ""),
                readback_ref=getattr(args, "readback_ref", ""),
                execute=bool(getattr(args, "execute", False)),
                ledger_path=ledger_path,
            ),
            as_json=as_json,
        )
    return _emit(
        status(
            ledger_path=ledger_path,
            scan_id=getattr(args, "scan_id", "") or "",
            limit=getattr(args, "limit", 20),
        ),
        as_json=as_json,
    )


def build_complex_task_harness_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "complex-task-harness",
        help="Read local complex-task harness receipts",
        description="Local-only complex-task harness readback/checkpoint surface; no lifecycle or external side effects.",
    )
    parser.add_argument("--ledger", default=None, help="Ledger JSONL path; defaults to ~/.hermes/state/complex-task-harness/ledger.jsonl")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=cmd_complex_task_harness)
    subs = parser.add_subparsers(dest="complex_task_harness_action")

    p_status = subs.add_parser("status", help="Read back harness ledger status/events")
    p_status.add_argument("--ledger", default=None, help="Ledger JSONL path")
    p_status.add_argument("--scan-id", default="", help="Optional scan id filter")
    p_status.add_argument("--limit", type=int, default=20, help="Recent events to include, capped at 100")
    p_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_status.set_defaults(func=cmd_complex_task_harness)

    p_config = subs.add_parser("config", help="Read back effective complex-task harness config")
    p_config.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_config.set_defaults(func=cmd_complex_task_harness)

    p_verifier = subs.add_parser("verifier", help="Read back persisted verifier packets")
    p_verifier.add_argument("--ledger", default=None, help="Ledger JSONL path")
    p_verifier.add_argument("--scan-id", default="", help="Optional scan id filter")
    p_verifier.add_argument("--limit", type=int, default=20, help="Recent verifier packets to include, capped at 100")
    p_verifier.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_verifier.set_defaults(func=cmd_complex_task_harness)

    p_checkpoint = subs.add_parser("checkpoint", help="Append a local continuation/checkpoint receipt")
    p_checkpoint.add_argument("--ledger", default=None, help="Ledger JSONL path")
    p_checkpoint.add_argument("--scan-id", required=True, help="Existing complex scan id")
    p_checkpoint.add_argument("--objective", required=True, help="Resume objective")
    p_checkpoint.add_argument("--completed-evidence", action="append", default=[], help="Evidence ref; repeatable")
    p_checkpoint.add_argument("--pending-action", action="append", default=[], help="Pending action; repeatable")
    p_checkpoint.add_argument("--rollback", default="", help="Rollback / escape hatch text")
    p_checkpoint.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_checkpoint.set_defaults(func=cmd_complex_task_harness)

    p_taskpool = subs.add_parser("taskpool-lifecycle", help="Record or execute one exact taskpool lifecycle adapter intent")
    p_taskpool.add_argument("--ledger", default=None, help="Ledger JSONL path")
    p_taskpool.add_argument("--scan-id", required=True, help="Existing complex scan id")
    p_taskpool.add_argument("--task-id", required=True, help="Exact canonical task id")
    p_taskpool.add_argument("--from-status", required=True, help="Expected current status")
    p_taskpool.add_argument("--to-status", required=True, help="Exact target status")
    p_taskpool.add_argument("--evidence-ref", required=True, help="Evidence ref with proof:/artifact:/command:/file:/url: prefix")
    p_taskpool.add_argument("--reason", required=True, help="Lifecycle transition reason")
    p_taskpool.add_argument("--readback-ref", required=True, help="Readback evidence ref")
    p_taskpool.add_argument("--execute", action="store_true", help="Run the fixed brain_task_orchestrator.py transition command")
    p_taskpool.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_taskpool.set_defaults(func=cmd_complex_task_harness)

    p_external = subs.add_parser("external-dispatch", help="Record or execute one exact external-dispatch adapter intent")
    p_external.add_argument("--ledger", default=None, help="Ledger JSONL path")
    p_external.add_argument("--scan-id", required=True, help="Existing complex scan id")
    p_external.add_argument("--platform", required=True, help="Allowlisted platform, e.g. local_harness")
    p_external.add_argument("--target", required=True, help="Exact platform target")
    p_external.add_argument("--payload", required=True, help="Payload body; not persisted, only hashed")
    p_external.add_argument("--payload-sha256", required=True, help="sha256:<hex> digest of payload")
    p_external.add_argument("--readback-ref", required=True, help="Readback evidence ref")
    p_external.add_argument("--execute", action="store_true", help="Execute only the local_harness sender boundary")
    p_external.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_external.set_defaults(func=cmd_complex_task_harness)
