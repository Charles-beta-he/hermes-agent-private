#!/usr/bin/env python3
"""Phase 50: record a guarded single external-send receipt.

This script does not send messages itself. The actual external side effect is
performed by an explicit operator/tool call (for example `hermes send`). This
script validates and records the resulting receipt with the exact adapter,
target, payload digest, idempotency key, rollback/correction path, and readback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-guarded-external-send"
DEFAULT_MESSAGE = "Workflow Kernel Phase50 guarded send smoke test: external dispatch path verified."
DEFAULT_ADAPTER = "telegram"
DEFAULT_MODE = "guarded_single_send"
DEFAULT_IDEMPOTENCY_KEY = "phase50-telegram-home-20260613-smoke-test"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_receipt(*, send_result: dict[str, Any], message: str = DEFAULT_MESSAGE, adapter: str = DEFAULT_ADAPTER, mode: str = DEFAULT_MODE, idempotency_key: str = DEFAULT_IDEMPOTENCY_KEY) -> dict[str, Any]:
    blockers: list[str] = []
    if adapter != "telegram":
        blockers.append("unsupported_adapter_for_phase50")
    if mode != "guarded_single_send":
        blockers.append("mode_must_be_guarded_single_send")
    if send_result.get("success") is not True:
        blockers.append("send_result_not_success")
    if send_result.get("platform") != adapter:
        blockers.append("adapter_mismatch")
    if not str(send_result.get("chat_id") or "").strip():
        blockers.append("chat_id_missing")
    if not str(send_result.get("message_id") or "").strip():
        blockers.append("message_id_missing")
    if not message:
        blockers.append("payload_missing")
    payload_digest = sha256_text(message)
    status = "GUARDED_EXTERNAL_SEND_VERIFIED" if not blockers else "GUARDED_EXTERNAL_SEND_BLOCKED"
    return {
        "report_id": "workflow_kernel_guarded_external_send_20260613_phase50",
        "created_at": iso(utc_now()),
        "status": status,
        "phase": 50,
        "adapter": adapter,
        "target": {
            "type": "telegram_home_channel",
            "chat_id": str(send_result.get("chat_id") or ""),
        },
        "mode": mode,
        "payload_digest": payload_digest,
        "payload_chars": len(message),
        "idempotency_key": idempotency_key,
        "external_side_effect_executed": send_result.get("success") is True,
        "unattended_external_dispatch_enabled": False,
        "send_result": {
            "success": send_result.get("success") is True,
            "platform": send_result.get("platform"),
            "chat_id": str(send_result.get("chat_id") or ""),
            "message_id": str(send_result.get("message_id") or ""),
            "mirrored": bool(send_result.get("mirrored")),
            "note": send_result.get("note"),
        },
        "rollback_or_correction_path": "send a compensating correction message to the same Telegram target if the smoke-test message is later deemed incorrect; no destructive rollback is possible for delivered messages",
        "post_send_readback": "hermes send returned success=true with Telegram message_id",
        "blockers": blockers,
        "safe_boundaries": [
            "single explicit Telegram home send only",
            "no repeated send",
            "no unattended dispatch enablement",
            "no credential read/write",
            "no scheduler mutation",
            "no Lark/GitHub mutation",
        ],
        "next_boundary": "Phase52 Control Tower UI/API projection may proceed after this receipt; unattended external dispatch remains closed",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_guarded_external_send_20260613_phase50.json"
    md_path = out_dir / "workflow_kernel_guarded_external_send_20260613_phase50.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Guarded External Send — Phase 50",
        "",
        f"Status: **{report['status']}**",
        f"Adapter: `{report['adapter']}`",
        f"Target: `{report['target']['type']}` chat_id=`{report['target']['chat_id']}`",
        f"Message ID: `{report['send_result']['message_id']}`",
        f"Payload digest: `{report['payload_digest']}`",
        "",
        "## Boundaries",
        *[f"- {x}" for x in report["safe_boundaries"]],
        "",
        "## Correction path",
        report["rollback_or_correction_path"],
    ]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-result", type=Path, required=True)
    ap.add_argument("--message", default=DEFAULT_MESSAGE)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_receipt(send_result=read_json(args.send_result), message=args.message)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: adapter={report['adapter']} message_id={report['send_result']['message_id']}")
    return 0 if report["status"] == "GUARDED_EXTERNAL_SEND_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
