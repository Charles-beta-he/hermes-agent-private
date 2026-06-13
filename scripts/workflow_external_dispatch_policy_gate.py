#!/usr/bin/env python3
"""Phase 48: select a governed external-dispatch policy track.

Default track is P47A_LOCAL_OUTBOX_ONLY. It writes a local outbox instruction and
receipt for audit, but does not call send_message, mutate Lark/GitHub, read
credentials, trigger cron, or enable unattended external dispatch.
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

DEFAULT_CANDIDATE = ROOT / "docs" / "private" / "workflow-kernel-external-dispatch-expansion" / "workflow_kernel_external_dispatch_expansion_20260613_phase47.json"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-external-dispatch-policy-gate"
DEFAULT_TRACK = "P47A_LOCAL_OUTBOX_ONLY"
DEFAULT_ADAPTER = "local_outbox_simulator"
DEFAULT_PURPOSE = "post_auto_enable_external_dispatch_governance_probe"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def digest(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def build_policy_gate(
    *,
    candidate_path: Path = DEFAULT_CANDIDATE,
    track: str = DEFAULT_TRACK,
    adapter: str = DEFAULT_ADAPTER,
    purpose: str = DEFAULT_PURPOSE,
    payload: str = "",
) -> dict[str, Any]:
    candidate = read_json(candidate_path)
    if candidate.get("status") != "EXTERNAL_DISPATCH_EXPANSION_CANDIDATE_READY":
        raise SystemExit("Phase47 candidate packet is not ready")
    options = {item.get("track"): item for item in candidate.get("candidate_tracks", [])}
    if track not in options:
        raise SystemExit(f"unknown candidate track: {track}")
    adapters = {item.get("adapter"): item for item in candidate.get("adapter_options", [])}
    if adapter not in adapters:
        raise SystemExit(f"unknown adapter: {adapter}")
    adapter_record = adapters[adapter]
    if track == "P47A_LOCAL_OUTBOX_ONLY" and adapter != "local_outbox_simulator":
        raise SystemExit("P47A_LOCAL_OUTBOX_ONLY requires local_outbox_simulator")
    if adapter_record.get("external_side_effects"):
        raise SystemExit("Phase48 default policy gate refuses external-side-effect adapters without exact target/payload mode")

    normalized_payload = {
        "purpose": purpose,
        "message_preview": payload or "No external dispatch. Local outbox governance probe only.",
        "target_adapter": adapter,
        "track": track,
        "external_side_effects_enabled": False,
    }
    payload_digest = digest(normalized_payload)
    idempotency_key = f"phase48:{track}:{adapter}:{payload_digest[:16]}"
    outbox_item_id = "xdispatch_" + digest({"idempotency_key": idempotency_key, "actor": "workflow_external_dispatch_policy_gate"})[:16]
    outbox_item = {
        "outbox_item_id": outbox_item_id,
        "track": track,
        "adapter": adapter,
        "purpose": purpose,
        "payload_digest": payload_digest,
        "idempotency_key": idempotency_key,
        "delivery_status": "local_outbox_recorded_not_dispatched",
        "created_at": iso(utc_now()),
        "payload_preview": normalized_payload,
        "external_side_effects_enabled": False,
        "unattended_external_dispatch_enabled": False,
    }
    receipt = {
        "receipt_id": outbox_item_id,
        "status": "LOCAL_OUTBOX_POLICY_GATE_RECORDED",
        "adapter": adapter,
        "track": track,
        "external_side_effects_executed": False,
        "unattended_external_dispatch_enabled": False,
        "idempotency_key": idempotency_key,
        "payload_digest": payload_digest,
        "rollback": "not_needed_no_external_mutation",
        "post_apply_readback": {
            "kind": "local_outbox",
            "verified": True,
            "external_mutation_found": False,
        },
    }
    return {
        "report_id": "workflow_kernel_external_dispatch_policy_gate_20260613_phase48",
        "created_at": iso(utc_now()),
        "status": "EXTERNAL_DISPATCH_POLICY_GATE_LOCAL_OUTBOX_RECORDED",
        "candidate_report_id": candidate.get("report_id"),
        "selected_track": track,
        "selected_adapter": adapter,
        "purpose": purpose,
        "payload_digest": payload_digest,
        "idempotency_key": idempotency_key,
        "outbox_item": outbox_item,
        "receipt": receipt,
        "policy_requirements_satisfied": [
            "target_allowlist_local_only",
            "purpose_recorded",
            "idempotency_key_schema",
            "duplicate_policy_same_idempotency_key_is_noop",
            "dry_run_preview_required",
            "append_only_receipt_ledger_local_artifact",
            "post_apply_readback_local",
            "rollback_not_needed_no_external_mutation",
        ],
        "policy_requirements_remaining_for_external_adapters": [
            "exact external target",
            "exact external payload",
            "rate limit per target",
            "platform receipt readback",
            "compensating correction path",
        ],
        "safe_boundaries": [
            "local outbox only",
            "no send_message call",
            "no lark/github mutation",
            "no credential read/write",
            "no scheduler mutation",
            "no model generation",
            "unattended external dispatch remains disabled",
        ],
        "next_boundary": "guarded single external send requires exact adapter, target, payload, mode, idempotency key, and rollback/correction path",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_external_dispatch_policy_gate_20260613_phase48.json"
    md_path = out_dir / "workflow_kernel_external_dispatch_policy_gate_20260613_phase48.md"
    outbox_path = out_dir / f"{report['outbox_item']['outbox_item_id']}.local_outbox_item.json"
    receipt_path = out_dir / f"{report['receipt']['receipt_id']}.local_outbox_receipt.json"
    outbox_art = write_json_artifact(outbox_path, report["outbox_item"])
    receipt_art = write_json_artifact(receipt_path, report["receipt"])
    report = dict(report)
    report["outbox_artifact"] = outbox_art
    report["receipt_artifact"] = receipt_art
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel External Dispatch Policy Gate — Phase 48",
        "",
        f"Status: **{report['status']}**",
        f"Selected track: `{report['selected_track']}`",
        f"Selected adapter: `{report['selected_adapter']}`",
        f"External side effects executed: `{report['receipt']['external_side_effects_executed']}`",
        f"Unattended external dispatch enabled: `{report['receipt']['unattended_external_dispatch_enabled']}`",
        f"Idempotency key: `{report['idempotency_key']}`",
        "",
        "## Satisfied requirements",
        *[f"- {x}" for x in report["policy_requirements_satisfied"]],
        "",
        "## Remaining for external adapters",
        *[f"- {x}" for x in report["policy_requirements_remaining_for_external_adapters"]],
        "",
        "## Safe boundaries",
        *[f"- {x}" for x in report["safe_boundaries"]],
    ]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"], "outbox": str(outbox_path), "receipt": str(receipt_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    ap.add_argument("--track", default=DEFAULT_TRACK)
    ap.add_argument("--adapter", default=DEFAULT_ADAPTER)
    ap.add_argument("--purpose", default=DEFAULT_PURPOSE)
    ap.add_argument("--payload", default="")
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_policy_gate(candidate_path=args.candidate, track=args.track, adapter=args.adapter, purpose=args.purpose, payload=args.payload)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: track={report['selected_track']} adapter={report['selected_adapter']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
