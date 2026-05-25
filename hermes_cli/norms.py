"""CLI surface for Hermes norm governance.

The command is intentionally JSON-first so taskpool hooks, cron jobs, and future
interactive commands can consume it without scraping text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from agent.norm_governance import LeaseConflict, NormConflict, NormGovernanceStore


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _record_payload(record) -> dict[str, Any]:
    return {
        "key": record.key,
        "content": record.content,
        "scope": record.scope,
        "source": record.source,
        "revision": record.revision,
        "content_hash": record.content_hash,
        "updated_at": record.updated_at,
    }


def _lease_payload(lease) -> dict[str, Any]:
    return {
        "resource_key": lease.resource_key,
        "owner_session_id": lease.owner_session_id,
        "task_id": lease.task_id,
        "base_hash": lease.base_hash,
        "intent": lease.intent,
        "write_mode": lease.write_mode,
        "expires_at": lease.expires_at,
        "heartbeat_at": lease.heartbeat_at,
    }


def _load_json_arg(raw: str, *, field: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{field} must be valid JSON: {exc}") from exc


def register_cli(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--db", help="Override norm governance sqlite path")
    sub = parser.add_subparsers(dest="command", required=True)

    upsert = sub.add_parser("upsert", help="Create/update a durable norm with revision CAS")
    upsert.add_argument("key")
    upsert.add_argument("content")
    upsert.add_argument("--scope", default="global")
    upsert.add_argument("--source", default="cli")
    upsert.add_argument("--expected-revision", type=int, default=None)

    snapshot = sub.add_parser("snapshot", help="Emit a versioned NormSnapshot")
    snapshot.add_argument("--scope", action="append", dest="scopes", default=[])

    stale = sub.add_parser("stale", help="Compare a snapshot JSON file with current registry revisions")
    stale.add_argument("snapshot_path")

    lease = sub.add_parser("lease", help="Manage resource leases")
    lease_sub = lease.add_subparsers(dest="lease_command", required=True)
    acquire = lease_sub.add_parser("acquire", help="Acquire or refresh a resource lease")
    acquire.add_argument("resource_key")
    acquire.add_argument("--owner-session-id", required=True)
    acquire.add_argument("--task-id", default="")
    acquire.add_argument("--ttl", type=int, default=300)
    acquire.add_argument("--base-hash", default="")
    acquire.add_argument("--intent", default="")
    acquire.add_argument("--write-mode", default="patch")
    acquire.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    release = lease_sub.add_parser("release", help="Release a lease held by this session")
    release.add_argument("resource_key")
    release.add_argument("--owner-session-id", required=True)
    active = lease_sub.add_parser("active", help="List active leases")
    active.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)

    fusion = sub.add_parser("fusion", help="Create/read task fusion records")
    fusion_sub = fusion.add_subparsers(dest="fusion_command", required=True)
    create = fusion_sub.add_parser("create", help="Create a FusionRecord")
    create.add_argument("--parent-task-id", action="append", dest="parent_task_ids", required=True)
    create.add_argument("--write-sets-json", required=True)
    create.add_argument("--norm-revisions-json", default="{}")
    create.add_argument("--evidence-refs-json", default="[]")
    create.add_argument("--acceptance", action="append", dest="acceptance_criteria", required=True)
    create.add_argument("--active-writer", required=True)
    get = fusion_sub.add_parser("get", help="Read a FusionRecord")
    get.add_argument("fusion_id")
    parser.set_defaults(func=_run_from_cli_args)
    return parser


def _run_from_cli_args(args: argparse.Namespace) -> None:
    raise SystemExit(run_from_args(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-norms", description="Hermes norm governance control plane")
    return register_cli(parser)


def run_from_args(args: argparse.Namespace) -> int:
    store = NormGovernanceStore(args.db) if args.db else NormGovernanceStore()

    if args.command == "upsert":
        try:
            record = store.upsert_norm(
                args.key,
                args.content,
                scope=args.scope,
                source=args.source,
                expected_revision=args.expected_revision,
            )
        except NormConflict as exc:
            _print({
                "success": False,
                "error": "norm_conflict",
                "key": exc.key,
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            })
            return 2
        _print({"success": True, "record": _record_payload(record)})
        return 0

    if args.command == "snapshot":
        _print(store.build_snapshot(scopes=args.scopes or None))
        return 0

    if args.command == "stale":
        snapshot = json.loads(Path(args.snapshot_path).read_text(encoding="utf-8"))
        stale = store.detect_stale_snapshot(snapshot)
        _print({"success": True, "stale_count": len(stale), "stale": stale})
        return 0

    if args.command == "lease":
        if args.lease_command == "acquire":
            try:
                lease = store.acquire_lease(
                    resource_key=args.resource_key,
                    owner_session_id=args.owner_session_id,
                    task_id=args.task_id,
                    ttl_seconds=args.ttl,
                    base_hash=args.base_hash,
                    intent=args.intent,
                    write_mode=args.write_mode,
                    now=args.now,
                )
            except LeaseConflict as exc:
                _print({
                    "success": False,
                    "error": "lease_conflict",
                    "resource_key": exc.resource_key,
                    "active_owner_session_id": exc.active_owner_session_id,
                    "expires_at": exc.expires_at,
                })
                return 2
            _print({"success": True, "lease": _lease_payload(lease)})
            return 0
        if args.lease_command == "release":
            released = store.release_lease(args.resource_key, args.owner_session_id)
            _print({"success": True, "released": released})
            return 0
        if args.lease_command == "active":
            _print({"success": True, "leases": store.active_leases(now=args.now)})
            return 0

    if args.command == "fusion":
        if args.fusion_command == "create":
            fusion = store.create_fusion_record(
                parent_task_ids=args.parent_task_ids,
                write_sets=_load_json_arg(args.write_sets_json, field="--write-sets-json"),
                norm_revisions=_load_json_arg(args.norm_revisions_json, field="--norm-revisions-json"),
                evidence_refs=_load_json_arg(args.evidence_refs_json, field="--evidence-refs-json"),
                acceptance_criteria=args.acceptance_criteria,
                active_writer=args.active_writer,
            )
            _print({"success": True, "fusion": fusion})
            return 0
        if args.fusion_command == "get":
            fusion = store.get_fusion_record(args.fusion_id)
            _print({"success": fusion is not None, "fusion": fusion})
            return 0 if fusion else 1

    _print({"success": False, "error": "unknown_command"})
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    return run_from_args(build_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
