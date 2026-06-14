#!/usr/bin/env python3
"""Compile a human-gated workflow-kernel local runtime dry-run plan.

This command is local and deterministic.  It records a human-gate packet and a
runtime plan artifact, but it does not call models, mutate adapters, write
through to external systems, or perform external side effects.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workflow_kernel.io import write_json_artifact  # noqa: E402
from workflow_kernel.runtime_gate import (  # noqa: E402
    LOCAL_DRY_RUN_AUTHORITY,
    LOCAL_DRY_RUN_SCOPE,
    build_human_gate_packet,
    compile_local_dry_run_plan,
)

DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-local-runtime-gate"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-id", default="telegram-authorization-20260614-local-runtime-dry-run")
    parser.add_argument("--operator", default="he-cheng")
    parser.add_argument("--authority", default=LOCAL_DRY_RUN_AUTHORITY)
    parser.add_argument("--scope", default=LOCAL_DRY_RUN_SCOPE)
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    packet = build_human_gate_packet(
        decision_id=args.decision_id,
        scope=args.scope,
        requested_authority=args.authority,
        operator=args.operator,
        evidence_refs=args.evidence_ref,
    )
    plan = compile_local_dry_run_plan(packet)
    result = {"packet": packet, "plan": plan}

    if args.write_report:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        packet_artifact = write_json_artifact(args.out_dir / "workflow_kernel_local_runtime_gate_packet_20260614.json", packet)
        plan_artifact = write_json_artifact(args.out_dir / "workflow_kernel_local_runtime_dry_run_plan_20260614.json", plan)
        result["artifacts"] = {"packet": packet_artifact, "plan": plan_artifact}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"workflow_kernel_local_runtime_gate: {plan['status']}")
        print(f"scope: {packet['scope']}")
        print(f"authority: {packet['requested_authority']}")
        if args.write_report:
            print(f"packet: {result['artifacts']['packet']['path']}")
            print(f"packet_sha256: {result['artifacts']['packet']['sha256']}")
            print(f"plan: {result['artifacts']['plan']['path']}")
            print(f"plan_sha256: {result['artifacts']['plan']['sha256']}")
        for error in plan.get("errors", []):
            print(f"ERROR: {error}")
    return 0 if plan["status"] == "READY_LOCAL_DRY_RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
