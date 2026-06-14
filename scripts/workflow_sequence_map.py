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
    {"phase": 53, "name": "Durable Control Tower Runtime/API Integration", "layer": "live control tower snapshot", "status": "live_snapshot_integration_ready", "depends_on": [52], "authority": "read-only dashboard plugin/API integration", "gate": "operator projection only; no lifecycle mutation", "next_unlocked": [54]},
    {"phase": 54, "name": "Unattended Dispatch Policy Gate", "layer": "dispatch policy", "status": "policy_gate_ready_disabled", "depends_on": [50, 53], "authority": "policy compiler only", "gate": "unattended_dispatch_enabled=false; dry-run preview only", "next_unlocked": [55, 56]},
    {"phase": 55, "name": "Unattended Dry-run Scheduler", "layer": "dry-run scheduler", "status": "dry_run_scheduler_ready", "depends_on": [54], "authority": "dry-run receipt only", "gate": "no cron mutation; no external send", "next_unlocked": [56]},
    {"phase": 56, "name": "Control Tower Policy Editor / Approval UI", "layer": "policy editor", "status": "approval_ui_ready", "depends_on": [55], "authority": "approval ledger only", "gate": "no policy activation; no unattended send", "next_unlocked": [57]},
    {"phase": 57, "name": "Dry-run Cron Rollout", "layer": "scheduler rollout", "status": "dry_run_cron_rollout_applied", "depends_on": [56], "authority": "single no-agent local cron job", "gate": "no external send; rollback backup required", "next_unlocked": [58]},
    {"phase": 58, "name": "Guarded Send Runner Activation Packet", "layer": "send runner activation boundary", "status": "activation_packet_ready", "depends_on": [57], "authority": "activation packet only", "gate": "manual runner not active; no send executed", "next_unlocked": [59]},
    {"phase": 59, "name": "Guarded Manual-send Runner Implementation", "layer": "manual send runner", "status": "manual_send_runner_ready", "depends_on": [58], "authority": "preview by default; execute only with explicit flag", "gate": "no send unless --execute and packet validation pass", "next_unlocked": [60]},
    {"phase": 60, "name": "One-shot Guarded Send Activation", "layer": "external dispatch execution", "status": "one_shot_guarded_send_verified", "depends_on": [59], "authority": "single Telegram home guarded send", "gate": "unattended dispatch remains disabled; future sends require fresh packet/manual gate", "next_unlocked": [61]},
    {"phase": 61, "name": "Post-send Correlation and Compensation Watch", "layer": "post-send audit", "status": "post_send_correlation_ready", "depends_on": [60], "authority": "read-only correlation", "gate": "no new message; automatic compensation disabled", "next_unlocked": [62]},
    {"phase": 62, "name": "Unattended Dispatch Enablement Packet", "layer": "enablement packet", "status": "enablement_packet_ready_disabled", "depends_on": [61], "authority": "decision packet only", "gate": "unattended_dispatch_enabled=false; no cron/apply mutation", "next_unlocked": [63]},
    {"phase": 63, "name": "Explicit Unattended Enablement Decision Record", "layer": "decision record", "status": "keep_manual_only_recorded", "depends_on": [62], "authority": "record-only decision", "gate": "selected_option=A_KEEP_MANUAL_ONLY; no apply mutation", "next_unlocked": [64]},
    {"phase": 64, "name": "Safe-default Manual-only Closure", "layer": "closure", "status": "manual_only_closure_ready", "depends_on": [63], "authority": "closure evidence only", "gate": "repeated/unattended dispatch closed without new authorization", "next_unlocked": [65]},
    {"phase": 65, "name": "Manual-only Closure Monitoring Projection", "layer": "closure monitoring", "status": "manual_only_monitoring_clear", "depends_on": [64], "authority": "read-only monitoring", "gate": "no send; no cron mutation; no apply", "next_unlocked": [66]},
    {"phase": 66, "name": "Future Unattended Apply Reopen Packet", "layer": "future reopen packet", "status": "future_reopen_packet_ready_disabled", "depends_on": [65], "authority": "packet only", "gate": "reopen_apply_enabled=false; selected_option=null", "next_unlocked": [67]},
    {"phase": 67, "name": "Single-point Reopen Implementation Choice", "layer": "single implementation point choice", "status": "keep_closed_choice_recorded", "depends_on": [66], "authority": "record-only safe default", "gate": "choice=A_NO_REOPEN_KEEP_MANUAL_ONLY; no apply", "next_unlocked": [68]},
    {"phase": 68, "name": "Manual-only Governance Archive", "layer": "archive", "status": "manual_only_governance_archive_ready", "depends_on": [67], "authority": "read-only archive", "gate": "archive/index only; no mutation", "next_unlocked": [69]},
    {"phase": 69, "name": "Periodic Closure Health Report", "layer": "stage report", "status": "periodic_closure_health_clear", "depends_on": [68], "authority": "read-only report", "gate": "stage_report_only=true; no publication", "next_unlocked": [70]},
    {"phase": 70, "name": "Governance Archive Index Publication Packet", "layer": "publication packet", "status": "archive_publication_packet_ready", "depends_on": [69], "authority": "packet only", "gate": "publication_apply_enabled=false; private default", "next_unlocked": [71]},
    {"phase": 71, "name": "Private Archive Retention Watch", "layer": "retention watch", "status": "private_archive_retention_clear", "depends_on": [70], "authority": "read-only watch", "gate": "private archive retained; publication apply disabled", "next_unlocked": [72]},
    {"phase": 72, "name": "Publication Apply Exact-target Gate", "layer": "exact-target gate", "status": "publication_exact_target_gate_ready", "depends_on": [71], "authority": "gate only", "gate": "exact target/payload required; apply disabled", "next_unlocked": [73]},
    {"phase": 73, "name": "Exact-target Apply Dry-run Compiler", "layer": "dry-run compiler", "status": "exact_target_apply_dry_run_compiler_ready", "depends_on": [72], "authority": "dry-run only", "gate": "would_publish=false; publication apply disabled", "next_unlocked": [74]},
    {"phase": 74, "name": "Publication Apply Authorization Packet", "layer": "authorization packet", "status": "publication_apply_authorization_packet_ready", "depends_on": [73], "authority": "packet only", "gate": "recommended_default=A_KEEP_PRIVATE_NO_PUBLICATION; apply disabled", "next_unlocked": [75]},
    {"phase": 75, "name": "Private Export Artifact Compiler", "layer": "private export", "status": "private_export_artifact_compiler_ready", "depends_on": [74], "authority": "local artifact only", "gate": "private local only; public_share_ready=false", "next_unlocked": [76]},
    {"phase": 76, "name": "Publication Apply Implementation Choice", "layer": "implementation choice", "status": "keep_private_no_apply_choice_recorded", "depends_on": [75], "authority": "record-only safe default", "gate": "choice=A_KEEP_PRIVATE_NO_APPLY; no exact target", "next_unlocked": [77]},
    {"phase": 77, "name": "Private Export Retention Watch", "layer": "export retention watch", "status": "private_export_retention_clear", "depends_on": [76], "authority": "read-only watch", "gate": "drift=[]; publication apply disabled", "next_unlocked": [78]},
    {"phase": 78, "name": "Publication Apply Reopen Packet", "layer": "reopen packet", "status": "publication_apply_reopen_packet_ready", "depends_on": [77], "authority": "packet only", "gate": "recommended_default=A_NO_REOPEN_KEEP_PRIVATE; apply disabled", "next_unlocked": [79]},
    {"phase": 79, "name": "Publication Closure Health Report", "layer": "publication health", "status": "publication_closure_health_clear", "depends_on": [78], "authority": "read-only report", "gate": "health=clear; apply disabled", "next_unlocked": [80]},
    {"phase": 80, "name": "Publication Governance Archive Update", "layer": "governance archive", "status": "publication_governance_archive_ready", "depends_on": [79], "authority": "read-only archive", "gate": "read_only=true; publication apply disabled", "next_unlocked": [81]},
    {"phase": 81, "name": "Publication Archive Retention Watch", "layer": "archive retention watch", "status": "publication_archive_retention_clear", "depends_on": [80], "authority": "read-only watch", "gate": "drift=[]; publication apply disabled", "next_unlocked": [82]},
    {"phase": 82, "name": "Publication Export Review Packet", "layer": "export review packet", "status": "publication_export_review_packet_ready", "depends_on": [81], "authority": "packet only", "gate": "recommended_default=A_REVIEW_PRIVATE_ARCHIVE_ONLY; apply disabled", "next_unlocked": [83]},
    {"phase": 83, "name": "Publication Export Review Retention Watch", "layer": "export review retention", "status": "publication_export_review_retention_clear", "depends_on": [82], "authority": "read-only watch", "gate": "drift=[]; packet_only=true; apply disabled", "next_unlocked": [84]},
    {"phase": 84, "name": "Publication Apply Final Boundary Packet", "layer": "final boundary packet", "status": "publication_apply_final_boundary_packet_ready", "depends_on": [83], "authority": "packet only", "gate": "recommended_default=A_STOP_AT_PRIVATE_REVIEW; apply disabled", "next_unlocked": [85]},
    {"phase": 85, "name": "Publication Final Boundary Retention Watch", "layer": "final boundary retention", "status": "publication_final_boundary_retention_clear", "depends_on": [84], "authority": "read-only watch", "gate": "drift=[]; apply disabled", "next_unlocked": [86]},
    {"phase": 86, "name": "Publication Governance Closure Summary", "layer": "governance closure", "status": "publication_governance_closure_summary_ready", "depends_on": [85], "authority": "read-only summary", "gate": "summary_only=true; apply disabled", "next_unlocked": [87]},
    {"phase": 87, "name": "Publication Closure Retention Watch", "layer": "closure retention", "status": "publication_closure_retention_clear", "depends_on": [86], "authority": "read-only watch", "gate": "drift=[]; apply disabled", "next_unlocked": [88]},
    {"phase": 88, "name": "Publication Apply Reopen Human Gate Packet", "layer": "human gate packet", "status": "publication_apply_reopen_human_gate_packet_ready", "depends_on": [87], "authority": "packet only", "gate": "recommended_default=A_KEEP_CLOSED_NO_REOPEN; apply disabled", "next_unlocked": [89]},
]

NEXT_LADDER = [
    {"phase": 89, "name": "Publication Human Gate Retention Watch", "track": "PUBLICATION_HUMAN_GATE_RETENTION_WATCH", "authority_needed": "read-only retention/drift watch over human gate packet", "why_next": "verify reopen gate remains fail-closed"},
    {"phase": 90, "name": "Publication Governance Final Index", "track": "PUBLICATION_GOVERNANCE_FINAL_INDEX", "authority_needed": "read-only final index", "why_next": "index complete publication governance chain"},
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
        "objective": "梳理 Phase17-88 的顺序、依赖、门禁和后续扩展路径；runtime switch 已自动开启，Phase87 已完成 closure retention watch，Phase88 已生成 reopen human-gate packet；archive/export 仍私有，publication/apply/unattended/repeated dispatch 仍关闭、无新外发。",
        "phase_count": len(PHASES),
        "phases": PHASES,
        "validation_errors": errors,
        "current_boundary": "Phase88 has produced a packet-only human gate for any future publication apply reopen while keeping publication_apply_enabled=false.",
        "safe_default_track": "EXTERNAL_DISPATCH_GOVERNED_EXPANSION",
        "next_ladder": NEXT_LADDER,
        "closed_boundaries_without_explicit_authorization": closed_boundaries,
        "recommended_next": "Stage-report cadence: Phase89 human gate retention watch; Phase90 governance final index remains read-only",
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
