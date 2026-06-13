#!/usr/bin/env python3
"""Compile a read-only migration evaluation for the Modular Workflow Kernel.

Phase 25 guardrail: this is an architecture/migration assessment compiler. It
reads prior local evidence artifacts and emits a migration plan; it does not
switch runtime architecture, mutate cron state, dispatch external messages, or
enable autorun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_OUT = Path("docs/private/workflow-kernel-architecture-migration")
DEFAULT_INPUTS = {
    "phase21_selfcheck": Path("docs/private/workflow-kernel-architecture-selfcheck/arch_selfcheck_20260613_phase21.json"),
    "phase22_cron_triage": Path("docs/private/workflow-kernel-cron-provider-triage/cron_provider_triage_20260613_phase22.json"),
    "phase23_provider_probe": Path("docs/private/workflow-kernel-provider-network-probe/provider_network_probe_20260613_phase23.json"),
    "phase24_health_gate": Path("docs/private/workflow-kernel-cron-provider-health-gate/cron_provider_health_gate_20260613_phase24.json"),
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"missing": True, "path": str(path)}
    if not isinstance(data, dict):
        return {"invalid": True, "path": str(path)}
    return data


def summarize_inputs(inputs: dict[str, Path]) -> dict[str, Any]:
    loaded = {name: read_json(path) for name, path in inputs.items()}
    phase21 = loaded["phase21_selfcheck"]
    phase22 = loaded["phase22_cron_triage"]
    phase23 = loaded["phase23_provider_probe"]
    phase24 = loaded["phase24_health_gate"]
    return {
        "phase21_status": phase21.get("status"),
        "phase21_score": phase21.get("overall_score") or phase21.get("score"),
        "phase22_failed_count": phase22.get("failed_count"),
        "phase22_class_counts": phase22.get("class_counts"),
        "phase23_status": phase23.get("status"),
        "phase23_classification_counts": phase23.get("classification_counts"),
        "phase23_degraded_endpoints": [r.get("id") for r in phase23.get("results", []) if isinstance(r, dict) and r.get("classification") != "reachable"],
        "phase24_status": phase24.get("status"),
        "phase24_failed_job_count": phase24.get("failed_job_count"),
        "phase24_decision_counts": phase24.get("decision_counts"),
        "phase24_degraded_endpoints": phase24.get("degraded_endpoints"),
        "missing_inputs": [name for name, data in loaded.items() if data.get("missing") or data.get("invalid")],
    }


def build_report(inputs: dict[str, Path] | None = None) -> dict[str, Any]:
    inputs = inputs or DEFAULT_INPUTS
    evidence = summarize_inputs(inputs)
    current_architecture = {
        "type": "layered local-control-plane with growing modular workflow kernel",
        "components": [
            {"name": "conversation/runtime tools", "role": "interactive execution and explicit user-authorized side effects"},
            {"name": "mission ledger", "role": "long-running objective, tick budget, stage reports, human gates"},
            {"name": "taskpool control plane", "role": "canonical task state, proof/evidence, lifecycle transitions"},
            {"name": "workflow kernel scripts", "role": "local validators, policy compilers, dry-run executors, evidence generators"},
            {"name": "cron scheduler", "role": "scheduled automation, currently provider/network health-gated by read-only artifacts"},
            {"name": "external dispatch adapters", "role": "Telegram smoke tested; unattended dispatch remains disabled"},
        ],
        "known_debt": [
            "kernel behavior is distributed across scripts rather than a single typed runtime package",
            "some evidence directories from earlier iterations have naming drift and should be consolidated later",
            "cron health gate is not yet wired into job execution wrappers",
            "taskpool/mission/cron schemas are connected by conventions, not a unified event schema",
        ],
    }
    target_architecture = {
        "name": "Event-governed Modular Workflow Kernel",
        "principles": [
            "single event/evidence ledger projection for taskpool, mission, cron, and external dispatch",
            "policy-first execution: plan/dry-run/health-gate before any side effect",
            "adapter registry with explicit authority, readback, rollback, idempotency, and blast radius",
            "workers execute proposals; lifecycle and external side effects remain governed by gates",
            "migration by strangler facade, not big-bang replacement",
        ],
        "core_modules": [
            "kernel.contracts: typed schemas for packets, receipts, rollback, health decisions",
            "kernel.ledger: append-only local artifacts plus projections into taskpool/mission",
            "kernel.policy: allowlists, idempotency, rate limits, provider health decisions",
            "kernel.adapters: telegram/taskpool_outbox/github/lark adapters behind explicit capabilities",
            "kernel.runtime: deterministic compile/apply/readback orchestration with fail-closed defaults",
            "kernel.cron_gate: optional wrapper entrypoints that consult health decisions before work",
        ],
    }
    candidate_paths = [
        {
            "id": "A",
            "name": "incremental strangler migration",
            "score": 9,
            "summary": "Wrap existing scripts behind a typed kernel package one phase at a time; keep old scripts as CLI shims until parity tests pass.",
            "risk": "low/medium",
            "rollback": "disable wrapper or call existing script directly",
        },
        {
            "id": "B",
            "name": "big-bang kernel rewrite",
            "score": 3,
            "summary": "Replace scripts/mission/taskpool/cron integration in one architecture pass.",
            "risk": "high",
            "rollback": "large revert; high chance of losing evidence semantics",
        },
        {
            "id": "C",
            "name": "status quo plus docs",
            "score": 5,
            "summary": "Keep producing policy scripts and ADRs without runtime consolidation.",
            "risk": "medium operational drift",
            "rollback": "none needed but debt grows",
        },
    ]
    migration_lanes = [
        {
            "phase": "25",
            "name": "migration evaluation and SSOT",
            "status": "this report",
            "gate": "read-only architecture decision; no runtime switch",
        },
        {
            "phase": "26",
            "name": "kernel package scaffold",
            "status": "proposed",
            "gate": "package imports compile; existing scripts still canonical",
        },
        {
            "phase": "27",
            "name": "contracts/ledger extraction",
            "status": "proposed",
            "gate": "existing Phase17-24 tests pass through both old script and new module paths",
        },
        {
            "phase": "28",
            "name": "cron health wrapper pilot",
            "status": "proposed",
            "gate": "one Xiaomi/MiMo smoke job uses skip evidence without model call when degraded",
        },
        {
            "phase": "29",
            "name": "external dispatch manual runner",
            "status": "proposed",
            "gate": "consumes dry-run packet and explicit authorization; unattended remains disabled",
        },
        {
            "phase": "30",
            "name": "projection consolidation",
            "status": "proposed",
            "gate": "taskpool/mission/cron views derive from one typed event projection",
        },
    ]
    return {
        "report_id": "workflow_kernel_architecture_migration_20260613_phase25",
        "created_at": now(),
        "status": "MIGRATION_RECOMMENDED_INCREMENTAL_STRANGLER",
        "objective": "Evaluate migration from scattered workflow-kernel scripts and ledgers into an event-governed Modular Workflow Kernel without changing runtime behavior yet.",
        "evidence_summary": evidence,
        "current_architecture": current_architecture,
        "target_architecture": target_architecture,
        "candidate_paths": candidate_paths,
        "selected_path": "A",
        "selected_rationale": "Best risk-adjusted path: preserves verified Phase17-24 behavior, allows parity tests, and avoids big-bang loss of taskpool/mission evidence semantics.",
        "migration_lanes": migration_lanes,
        "hard_boundaries": [
            "no global autorun enablement",
            "no cron mutation in migration evaluation",
            "no external dispatch expansion",
            "no config migration without backup/diff",
            "no deletion/renaming of historical evidence before a manifest-backed consolidation phase",
        ],
        "acceptance_criteria_before_runtime_switch": [
            "all existing Phase17-24 tests pass through new module wrappers",
            "old script CLIs remain backward compatible",
            "taskpool selfcheck and proof-check pass",
            "mission stage-report can cite new kernel artifact paths",
            "rollback path is a one-line CLI shim revert or wrapper disable",
        ],
        "next_phase": "Phase 26: create kernel package scaffold and move shared read/write helpers behind imported modules while keeping scripts as compatibility shims.",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_architecture_migration_20260613_phase25.json"
    md_path = out_dir / "workflow_kernel_architecture_migration_20260613_phase25.md"
    payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    json_path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    lines = [
        "# Workflow Kernel Architecture Migration Evaluation — Phase 25",
        "",
        f"Status: **{report['status']}**",
        "",
        "## Objective",
        report["objective"],
        "",
        "## Evidence Summary",
        "```json",
        json.dumps(report["evidence_summary"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Current Architecture",
        f"Type: {report['current_architecture']['type']}",
        "",
        "### Components",
    ]
    for c in report["current_architecture"]["components"]:
        lines.append(f"- **{c['name']}** — {c['role']}")
    lines += ["", "### Known Debt"] + [f"- {x}" for x in report["current_architecture"]["known_debt"]]
    lines += ["", "## Target Architecture", f"Name: **{report['target_architecture']['name']}**", "", "### Principles"]
    lines += [f"- {x}" for x in report["target_architecture"]["principles"]]
    lines += ["", "### Core Modules"] + [f"- `{x}`" for x in report["target_architecture"]["core_modules"]]
    lines += ["", "## Candidate Paths"]
    for p in report["candidate_paths"]:
        lines.append(f"- **{p['id']} {p['name']}** score={p['score']}/10 risk={p['risk']} — {p['summary']} Rollback: {p['rollback']}")
    lines += ["", "## Selected Path", f"Selected: **{report['selected_path']}**", report["selected_rationale"], "", "## Migration Lanes"]
    for lane in report["migration_lanes"]:
        lines.append(f"- **Phase {lane['phase']} {lane['name']}** — {lane['status']}; gate: {lane['gate']}")
    lines += ["", "## Hard Boundaries"] + [f"- {x}" for x in report["hard_boundaries"]]
    lines += ["", "## Acceptance Before Runtime Switch"] + [f"- {x}" for x in report["acceptance_criteria_before_runtime_switch"]]
    lines += ["", "## Next Phase", report["next_phase"]]
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "md": str(md_path), "sha256": sha}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = build_report()
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: selected_path={report['selected_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
