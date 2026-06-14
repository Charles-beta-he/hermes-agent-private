#!/usr/bin/env python3
"""Compile the Modular Workflow Kernel phase sequence map.

Phase 38 boundary: order and dependency map only. This script does not enable
runtime, mutate cron, trigger jobs, call models, read credentials, or send
external messages.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-sequence-map"

PHASES: list[dict[str, Any]] = [
    {"phase": 17, "name": "External Apply Requirements", "layer": "contract", "status": "complete", "depends_on": [], "authority": "read-only/local contract", "gate": "real_apply_enabled=false", "next_unlocked": [18]},
    {"phase": 18, "name": "Local External Apply Outbox", "layer": "local outbox", "status": "complete", "depends_on": [17], "authority": "local simulator only", "gate": "external_side_effects_enabled=false", "next_unlocked": [19]},
    {"phase": 19, "name": "Single Explicit External Dispatch Smoke", "layer": "external apply smoke", "status": "complete", "depends_on": [18], "authority": "single explicit external send only", "gate": "no unattended sends", "next_unlocked": [20]},
    {"phase": 20, "name": "Governed Notification Policy", "layer": "policy", "status": "complete", "depends_on": [19], "authority": "dry-run policy compiler", "gate": "unattended_send_enabled=false", "next_unlocked": [21]},
    {"phase": 21, "name": "Guarded Manual-send Runner", "layer": "manual dispatch", "status": "complete", "depends_on": [20], "authority": "explicit per-send authorization", "gate": "no unattended dispatch", "next_unlocked": [22]},
    {"phase": 22, "name": "Cron Provider Degradation Triage", "layer": "diagnostics", "status": "complete", "depends_on": [21], "authority": "read-only classification", "gate": "no cron mutation/rerun", "next_unlocked": [23]},
    {"phase": 23, "name": "Provider Network Probe Ledger", "layer": "network evidence", "status": "complete", "depends_on": [22], "authority": "unauthenticated reachability only", "gate": "no LLM prompt/credentials", "next_unlocked": [24]},
    {"phase": 24, "name": "Cron Provider Health Gate", "layer": "decision compiler", "status": "complete", "depends_on": [23], "authority": "read-only gate decisions", "gate": "no scheduler mutation", "next_unlocked": [25]},
    {"phase": 25, "name": "Architecture Migration Evaluation", "layer": "migration strategy", "status": "complete", "depends_on": [24], "authority": "read-only recommendation", "gate": "no big-bang rewrite", "next_unlocked": [26]},
    {"phase": 26, "name": "Kernel Scaffold", "layer": "runtime scaffold", "status": "complete", "depends_on": [25], "authority": "package scaffold only", "gate": "RUNTIME_SWITCH_ENABLED=false", "next_unlocked": [27]},
    {"phase": 27, "name": "Runtime Switch Readiness", "layer": "switch readiness", "status": "complete", "depends_on": [26], "authority": "compatibility-shim readiness", "gate": "explicit enable required", "next_unlocked": [28]},
    {"phase": 28, "name": "Daily-full Wrapper Gate Rollout", "layer": "single wrapper opt-in", "status": "complete", "depends_on": [27], "authority": "one wrapper typed skip", "gate": "no cron/jobs.json edit", "next_unlocked": [29]},
    {"phase": 29, "name": "Every-4h Wrapper Exclusion Hint", "layer": "second wrapper opt-in", "status": "complete", "depends_on": [28], "authority": "plan-only model exclusion verification", "gate": "no generation during verification", "next_unlocked": [30]},
    {"phase": 30, "name": "Xiaomi Probe Calibration", "layer": "probe correctness", "status": "complete", "depends_on": [29], "authority": "unauthenticated /models probe", "gate": "edge reachability only, not credential validity", "next_unlocked": [31]},
    {"phase": 31, "name": "Cron Wrapper Kernel Shim", "layer": "kernel shim", "status": "complete", "depends_on": [30], "authority": "opt-in wrappers only", "gate": "global switch remains false", "next_unlocked": [32]},
    {"phase": 32, "name": "Scheduler Runtime Projection", "layer": "scheduler readback", "status": "complete", "depends_on": [31], "authority": "read-only projection", "gate": "no cron execution", "next_unlocked": [33]},
    {"phase": 33, "name": "Scheduled-run Outcome Correlation", "layer": "runtime evidence", "status": "complete", "depends_on": [32], "authority": "read logs only", "gate": "partial until both wrappers run", "next_unlocked": [34]},
    {"phase": 34, "name": "Wrapper Run Receipt Ledger", "layer": "audit receipts", "status": "complete", "depends_on": [33], "authority": "append-only local receipts", "gate": "verification uses override ledger", "next_unlocked": [35]},
    {"phase": 35, "name": "Receipt-aware Correlation", "layer": "correlation upgrade", "status": "complete", "depends_on": [34], "authority": "read-only receipt/log correlation", "gate": "do not trigger cron to create receipts", "next_unlocked": [36]},
    {"phase": 36, "name": "Runtime Readiness Summary", "layer": "operator summary", "status": "complete", "depends_on": [35], "authority": "read-only aggregation", "gate": "summary is not enablement authority", "next_unlocked": [37]},
    {"phase": 37, "name": "Runtime Enablement Authorization Packet", "layer": "human gate", "status": "complete", "depends_on": [36], "authority": "decision packet only", "gate": "B/C require explicit authorization", "next_unlocked": [38]},
    {"phase": 38, "name": "Sequence Map and Ordering", "layer": "workflow map", "status": "complete", "depends_on": [37], "authority": "read-only ordering", "gate": "do not cross enablement boundary", "next_unlocked": [39]},
    {"phase": 39, "name": "Enablement Runbook and Rollback Drill", "layer": "operator runbook", "status": "complete", "depends_on": [38], "authority": "read-only executable documentation", "gate": "do not execute runbook as authorization", "next_unlocked": [40]},
    {"phase": 40, "name": "Receipt/Projection Drift Watcher", "layer": "drift monitoring", "status": "complete", "depends_on": [39], "authority": "read-only drift detection", "gate": "do not trigger cron solely for receipts", "next_unlocked": [41]},
    {"phase": 41, "name": "Single-wrapper Runtime-enable Shadow Dry-run", "layer": "runtime-adjacent dry-run", "status": "complete", "depends_on": [40], "authority": "explicit B authorization", "gate": "no cron mutation, no credentialed generation", "next_unlocked": [42]},
    {"phase": 42, "name": "Single-wrapper Scheduler-native Rollout", "layer": "scheduler-native single job", "status": "complete", "depends_on": [41], "authority": "explicit C authorization", "gate": "one job script field only; rollback backup required", "next_unlocked": [43]},
    {"phase": 43, "name": "Daily-wrapper Receipt Closure", "layer": "receipt closure", "status": "complete", "depends_on": [42], "authority": "explicit all authorization", "gate": "plan-only receipt; no credentialed generation required for proof", "next_unlocked": [44]},
    {"phase": 44, "name": "Daily-wrapper Scheduler-native Rollout", "layer": "scheduler-native second opted-in job", "status": "complete", "depends_on": [43], "authority": "explicit all authorization", "gate": "daily job script field only; rollback backup required", "next_unlocked": [45]},
    {"phase": 45, "name": "Runtime Switch Auto-enabled", "layer": "runtime switch", "status": "complete", "depends_on": [44], "authority": "explicit all authorization until auto-enable", "gate": "future external dispatch/broad scheduler rewrites still scoped", "next_unlocked": [46]},
    {"phase": 46, "name": "Post-auto-enable Scheduled Observation", "layer": "runtime observation", "status": "observation_pending", "depends_on": [45], "authority": "read-only observation", "gate": "do not trigger cron solely to create post-projection evidence", "next_unlocked": [47]},
    {"phase": 47, "name": "External Dispatch Expansion Candidate Packet", "layer": "external dispatch governance", "status": "candidate_ready", "depends_on": [46], "authority": "candidate packet only", "gate": "no external send without exact target/payload/mode", "next_unlocked": [48]},
    {"phase": 48, "name": "External Dispatch Policy Gate Local Outbox", "layer": "external dispatch policy gate", "status": "local_outbox_recorded", "depends_on": [47], "authority": "local outbox only", "gate": "external adapters still require exact target/payload/mode", "next_unlocked": [49]},
    {"phase": 49, "name": "Workflow Pack / Agent Pack Schema", "layer": "pack productization", "status": "pack_manifests_ready", "depends_on": [48], "authority": "schema/projection only", "gate": "runtime adoption and external send remain separate gates", "next_unlocked": [50, 51]},
    {"phase": 50, "name": "Guarded Single External Send", "layer": "external dispatch smoke", "status": "guarded_send_verified", "depends_on": [49], "authority": "explicit option-1 Telegram home send", "gate": "single send only; no unattended dispatch", "next_unlocked": [51, 52]},
    {"phase": 51, "name": "Pack Registry / Control Tower Projection", "layer": "operator registry", "status": "registry_projection_ready", "depends_on": [49], "authority": "read-only registry projection", "gate": "UI/API adoption remains separate gate", "next_unlocked": [52]},
    {"phase": 52, "name": "Control Tower UI/API Projection", "layer": "control tower productization", "status": "ui_api_projection_ready", "depends_on": [50, 51], "authority": "read-only static UI/API projection", "gate": "durable runtime API and unattended dispatch remain separate gates", "next_unlocked": [53]},
]

NEXT_LADDER = [
    {"phase": 53, "name": "Durable Control Tower Runtime/API Integration", "track": "CONTROL_TOWER_RUNTIME_API", "authority_needed": "choose server/API integration path and persistence boundary", "why_next": "turn the static projection into a live operator surface"},
    {"phase": 54, "name": "Unattended Dispatch Policy Gate", "track": "UNATTENDED_DISPATCH_POLICY", "authority_needed": "target allowlist/rate-limit/duplicate policy/correction path", "why_next": "move from one guarded send to policy-governed unattended dispatch"},
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def validate_sequence(phases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    by_phase = {int(p["phase"]): p for p in phases}
    ordered = [int(p["phase"]) for p in phases]
    if ordered != sorted(ordered):
        errors.append("phases_not_sorted")
    for item in phases:
        phase = int(item["phase"])
        for dep in item.get("depends_on", []):
            if dep not in by_phase:
                errors.append(f"phase_{phase}_missing_dep_{dep}")
            elif dep >= phase:
                errors.append(f"phase_{phase}_non_prior_dep_{dep}")
    return errors


def build_map() -> dict[str, Any]:
    errors = validate_sequence(PHASES)
    closed_boundaries = [
        "trigger cron jobs solely to create evidence",
        "credentialed model generation smoke",
        "external message delivery without exact target/payload/mode",
        "unattended external dispatch without policy gate",
        "broad scheduler rewrite beyond opted-in jobs",
        "credential or secret read/write",
    ]
    return {
        "report_id": "workflow_kernel_sequence_map_20260613_phase38",
        "created_at": iso(utc_now()),
        "status": "WORKFLOW_SEQUENCE_MAP_READY" if not errors else "WORKFLOW_SEQUENCE_MAP_INVALID",
        "objective": "梳理 Phase17-52 的顺序、依赖、门禁和后续扩展路径；runtime switch 已自动开启，Phase50 单次 Telegram guarded send 已验证，Phase51 pack registry 已就绪，Phase52 Control Tower 静态 UI/API projection 已就绪。",
        "phase_count": len(PHASES),
        "phases": PHASES,
        "validation_errors": errors,
        "current_boundary": "Phase52 has produced a static Control Tower UI/API projection after the single guarded external send. The next step is a larger divergence: durable live Control Tower runtime/API integration versus unattended dispatch policy gate.",
        "safe_default_track": "EXTERNAL_DISPATCH_GOVERNED_EXPANSION",
        "next_ladder": NEXT_LADDER,
        "closed_boundaries_without_explicit_authorization": closed_boundaries,
        "recommended_next": "Human choice required: Phase53 live Control Tower runtime/API integration vs Phase54 unattended dispatch policy gate",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_sequence_map_20260613_phase38.json"
    md_path = out_dir / "workflow_kernel_sequence_map_20260613_phase38.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Sequence Map — Phase 38",
        "",
        f"Status: **{report['status']}**",
        f"Safe default track: `{report['safe_default_track']}`",
        "",
        "## Phase order",
    ]
    for p in report["phases"]:
        lines.append(f"- Phase {p['phase']}: {p['name']} — layer=`{p['layer']}`, gate=`{p['gate']}`")
    lines.extend(["", "## Next ladder"])
    for item in report["next_ladder"]:
        lines.append(f"- Phase {item['phase']}: {item['name']} — track=`{item['track']}`, authority=`{item['authority_needed']}`")
    lines.extend(["", "## Closed boundaries", *[f"- {x}" for x in report["closed_boundaries_without_explicit_authorization"]]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_map()
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: phases={report['phase_count']} next={report['recommended_next']}")
    return 0 if report["status"] == "WORKFLOW_SEQUENCE_MAP_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
