#!/usr/bin/env python3
"""Phase 64: safe-default manual-only closure.

Closes the Workflow Kernel external-dispatch ladder on manual-only operation:
Phase59 guarded manual runner remains available, Phase57 dry-run cron remains
no-send, and unattended/repeated external dispatch remains disabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR = Path("docs/private/workflow-kernel-manual-only-closure")
REPORT_JSON = REPORT_DIR / "workflow_kernel_manual_only_closure_20260613_phase64.json"
REPORT_MD = REPORT_DIR / "workflow_kernel_manual_only_closure_20260613_phase64.md"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_closure(root: Path) -> dict[str, Any]:
    phase63 = read_json(root / "docs/private/workflow-kernel-unattended-decision-record/workflow_kernel_unattended_decision_record_20260613_phase63.json")
    phase62 = read_json(root / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json")
    phase61 = read_json(root / "docs/private/workflow-kernel-post-send-correlation-watch/workflow_kernel_post_send_correlation_watch_20260613_phase61.json")
    phase60 = read_json(root / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json")
    phase57 = read_json(root / "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json")
    checks = {
        "phase63_keep_manual_only": phase63.get("decision") == "keep_manual_only" and phase63.get("selected_option") == "A_KEEP_MANUAL_ONLY",
        "phase63_record_only": phase63.get("record_only") is True,
        "phase62_enablement_not_enabled": phase62.get("unattended_dispatch_enabled") is False,
        "phase61_no_new_message": ((phase61.get("compensation_watch") or {}).get("new_message_sent")) is False,
        "phase60_one_shot_verified": phase60.get("status") == "ONE_SHOT_GUARDED_SEND_VERIFIED",
        "phase57_dry_run_cron_no_send": phase57.get("would_send") is False,
    }
    blockers = [k for k, ok in checks.items() if not ok]
    closure = {
        "schema": "workflow_kernel_manual_only_closure_v1",
        "phase": 64,
        "status": "MANUAL_ONLY_CLOSURE_READY" if not blockers else "MANUAL_ONLY_CLOSURE_BLOCKED",
        "created_at": iso_now(),
        "decision_record_digest": phase63.get("record_digest"),
        "checks": checks,
        "blockers": blockers,
        "final_mode": "manual_only_with_no_send_dry_run_cron",
        "allowed_paths_after_closure": [
            "Phase59 guarded manual-send runner preview",
            "Phase57 no-agent local dry-run cron receipts",
            "Control Tower read-only projection",
        ],
        "closed_paths_without_new_authorization": [
            "repeated external sends",
            "unattended external dispatch",
            "cron-triggered external dispatch",
            "automatic compensation message",
            "cross-adapter external dispatch",
        ],
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "message_sent": False,
        "next_boundary": "Workflow Kernel external-dispatch ladder closed on manual-only safe default; future unattended enablement requires new explicit apply phase",
    }
    closure["closure_digest"] = sha256_obj({k: closure[k] for k in closure if k != "closure_digest"})
    return closure


def write_closure(closure: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(closure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    REPORT_MD.write_text("\n".join([
        "# Workflow Kernel Manual-only Closure — Phase 64",
        "",
        f"Status: **{closure['status']}**",
        f"Final mode: `{closure['final_mode']}`",
        f"Closure digest: `{closure['closure_digest']}`",
        f"Unattended dispatch enabled: `{closure['unattended_dispatch_enabled']}`",
        f"External side effects executed: `{closure['external_side_effects_executed']}`",
        "",
        "## Closed paths without new authorization",
        "",
        *(f"- {item}" for item in closure["closed_paths_without_new_authorization"]),
    ]) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    closure = build_closure(Path.cwd())
    if args.write_report:
        write_closure(closure)
    if args.json:
        print(json.dumps(closure, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if closure["status"].endswith("READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
