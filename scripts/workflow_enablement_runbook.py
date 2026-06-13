#!/usr/bin/env python3
"""Compile a read-only runtime enablement runbook and rollback drill.

Phase 39 boundary: commands and drill steps only. This script does not run the
commands, enable runtime, mutate cron, trigger jobs, call models, read
credentials, or send external messages.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import read_json_artifact, write_json_artifact

DEFAULT_SEQUENCE_GLOB = ROOT / "docs" / "private" / "workflow-kernel-sequence-map" / "workflow_kernel_sequence_map_*.json"
DEFAULT_PACKET_GLOB = ROOT / "docs" / "private" / "workflow-kernel-runtime-enablement-packet" / "workflow_kernel_runtime_enablement_packet_*.json"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-enablement-runbook"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def newest_path(pattern: str | Path) -> Path | None:
    matches = [Path(p) for p in glob.glob(str(pattern))]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def load_optional(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    obj = read_json_artifact(path)
    return obj if isinstance(obj, dict) else None


def preflight_commands() -> list[dict[str, str]]:
    return [
        {"id": "git-status", "command": "git -C /Users/charles/.hermes/hermes-agent status --short --branch", "expected": "identify unrelated dirty files before any scoped change"},
        {"id": "runtime-summary", "command": "cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_runtime_readiness_summary.py --write-report --json", "expected": "RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE and blocking_checks=[]"},
        {"id": "enablement-packet", "command": "cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_runtime_enablement_packet.py --write-report --json", "expected": "RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY"},
        {"id": "sequence-map", "command": "cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_sequence_map.py --write-report --json", "expected": "WORKFLOW_SEQUENCE_MAP_READY"},
    ]


def dry_run_commands() -> list[dict[str, str]]:
    return [
        {"id": "wrapper-plan", "command": "HERMES_MULTI_MODEL_SMOKE_EVERY4H_PLAN_ONLY=1 HERMES_CRON_WRAPPER_RECEIPT_LEDGER=/tmp/phase39_receipts.jsonl python3 /Users/charles/.hermes/scripts/multi-model-smoke-every4h-wrapper.py", "expected": "kernel_shim=true, runtime_mode=plan_only, cron_state_mutated=false"},
        {"id": "correlation-readback", "command": "cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_cron_run_outcome_correlation.py --write-report --json", "expected": "REAL_RUN_CORRELATION_PARTIAL_READY or RECEIPT_CORRELATION_PARTIAL_READY"},
        {"id": "focused-tests", "command": "cd /Users/charles/.hermes/hermes-agent && ./scripts/run_tests.sh tests/scripts/test_workflow_runtime_enablement_packet.py tests/scripts/test_workflow_runtime_readiness_summary.py tests/scripts/test_workflow_sequence_map.py", "expected": "all tests pass"},
    ]


def rollback_drill() -> list[dict[str, Any]]:
    return [
        {"step": 1, "name": "Capture pre-change state", "command": "git -C /Users/charles/.hermes/hermes-agent status --short --branch && python3 -m json.tool /Users/charles/.hermes/cron/jobs.json >/tmp/phase39_jobs_before.json", "verify": "jobs file parses; git state known"},
        {"step": 2, "name": "If B shadow artifact misbehaves", "command": "rm -f /tmp/phase39_receipts.jsonl /tmp/phase39_shadow_runtime*.json", "verify": "only temp shadow artifacts removed"},
        {"step": 3, "name": "If scoped code change misbehaves", "command": "git -C /Users/charles/.hermes/hermes-agent revert --no-edit <scoped_commit>", "verify": "focused tests pass after revert"},
        {"step": 4, "name": "If cron was mutated under explicit C", "command": "cp /tmp/phase39_jobs_before.json /Users/charles/.hermes/cron/jobs.json && python3 -m json.tool /Users/charles/.hermes/cron/jobs.json >/dev/null", "verify": "cron jobs json restored and parses"},
        {"step": 5, "name": "Post-rollback readback", "command": "cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_runtime_readiness_summary.py --write-report --json", "verify": "runtime_switch_enabled_by_default=false and no unexpected blocking checks"},
    ]


def build_runbook(sequence_path: Path | None = None, packet_path: Path | None = None) -> dict[str, Any]:
    sequence_path = sequence_path or newest_path(DEFAULT_SEQUENCE_GLOB)
    packet_path = packet_path or newest_path(DEFAULT_PACKET_GLOB)
    sequence = load_optional(sequence_path)
    packet = load_optional(packet_path)
    ready = (sequence or {}).get("status") == "WORKFLOW_SEQUENCE_MAP_READY" and (packet or {}).get("status") == "RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY"
    return {
        "report_id": "workflow_kernel_enablement_runbook_20260613_phase39",
        "created_at": iso(utc_now()),
        "status": "ENABLEMENT_RUNBOOK_READY" if ready else "ENABLEMENT_RUNBOOK_BLOCKED_BY_MISSING_INPUTS",
        "sequence_path": str(sequence_path) if sequence_path else None,
        "packet_path": str(packet_path) if packet_path else None,
        "safe_default_track": "A_READ_ONLY_CONTINUE",
        "scope": "operator runbook and rollback drill only; no runtime/scheduler action executed",
        "preflight_commands": preflight_commands(),
        "dry_run_commands": dry_run_commands(),
        "rollback_drill": rollback_drill(),
        "go_no_go_gates": [
            "GO only if runtime summary remains READY and blocking_checks=[]",
            "NO-GO if cron/jobs.json has unexpected dirty changes",
            "NO-GO if provider probe/gate artifacts are stale",
            "NO-GO if explicit B/C authorization is absent for runtime/scheduler boundary crossing",
        ],
        "closed_boundaries_without_explicit_authorization": (packet or {}).get("closed_boundaries_without_explicit_authorization", []),
        "next_recommended_phase": "Phase40 Receipt/Projection Drift Watcher under A_READ_ONLY_CONTINUE",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_enablement_runbook_20260613_phase39.json"
    md_path = out_dir / "workflow_kernel_enablement_runbook_20260613_phase39.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Enablement Runbook — Phase 39",
        "",
        f"Status: **{report['status']}**",
        f"Safe default track: `{report['safe_default_track']}`",
        "",
        "## Preflight commands",
    ]
    for row in report["preflight_commands"]:
        lines.extend([f"### {row['id']}", "```bash", row["command"], "```", f"Expected: {row['expected']}", ""])
    lines.append("## Dry-run commands")
    for row in report["dry_run_commands"]:
        lines.extend([f"### {row['id']}", "```bash", row["command"], "```", f"Expected: {row['expected']}", ""])
    lines.append("## Rollback drill")
    for row in report["rollback_drill"]:
        lines.extend([f"### Step {row['step']}: {row['name']}", "```bash", row["command"], "```", f"Verify: {row['verify']}", ""])
    lines.extend(["## Go / No-go gates", *[f"- {x}" for x in report["go_no_go_gates"]]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sequence", default=None)
    ap.add_argument("--packet", default=None)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_runbook(Path(args.sequence) if args.sequence else None, Path(args.packet) if args.packet else None)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: next={report['next_recommended_phase']}")
    return 0 if report["status"] == "ENABLEMENT_RUNBOOK_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
