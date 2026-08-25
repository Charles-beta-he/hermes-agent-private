#!/usr/bin/env python3
"""Read-only candidate-change provenance request and verification utility."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from hermes_cli.change_provenance import (  # noqa: E402
    build_candidate_change_request,
    canonical_json_bytes,
    request_signed_receipt,
    verify_candidate_change,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--approve-bundle-sha256", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--now", default=None)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="print a canonical issuer request")
    _common(request)
    request.add_argument("--producer-principal-digest", required=True)
    request.add_argument("--producer-session-digest", required=True)
    issue = commands.add_parser("issue", help="request and verify an external receipt")
    _common(issue)
    issue.add_argument("--producer-principal-digest", required=True)
    issue.add_argument("--producer-session-digest", required=True)
    verify = commands.add_parser("verify", help="validate read-only receipt evidence")
    _common(verify)
    verify.add_argument("--receipts", required=True, type=Path)
    verify.add_argument("--checkpoint", required=True, type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    instant = args.now or _now()
    if args.command in {"request", "issue"}:
        value = build_candidate_change_request(
            repo=args.repo,
            bundle_path=args.bundle,
            approved_bundle_digest=args.approve_bundle_sha256,
            candidate_commit=args.candidate,
            producer_principal_digest=args.producer_principal_digest,
            producer_session_digest=args.producer_session_digest,
            now=instant,
        )
        if args.command == "issue":
            value = request_signed_receipt(
                bundle_path=args.bundle,
                approved_bundle_digest=args.approve_bundle_sha256,
                request=value,
                now=instant,
            )
    else:
        value = verify_candidate_change(
            repo=args.repo,
            bundle_path=args.bundle,
            approved_bundle_digest=args.approve_bundle_sha256,
            receipts_path=args.receipts,
            checkpoint_path=args.checkpoint,
            candidate_commit=args.candidate,
            now=instant,
        )
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
