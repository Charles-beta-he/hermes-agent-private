#!/usr/bin/env python3
"""Consolidate workflow-kernel imported taskpool candidates into grouped proposed tasks.

This is a canonical taskpool write boundary, but not a runtime boundary. It reads
previously imported proposed candidate notes, creates a smaller set of grouped
`status: proposed` tasks, verifies readback, refreshes derived views, and stops.
It does not claim tasks, run workflows, call models, write adapters, archive/delete
source candidate notes, or send external messages.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_HOME = Path.home() / ".hermes"
ORCHESTRATOR_PATH = HERMES_HOME / "scripts" / "brain_task_orchestrator.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-candidate-consolidations"
DEFAULT_WRITE_ID = "tpwrite_f62e8067efc48f89"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_MISSION_ID = "msn_20260610_120149_27c88cfc"

GROUPS = [
    {
        "group_id": "contract_acceptance_governance",
        "title": "Workflow kernel consolidated candidate｜contract acceptance governance",
        "prefixes": ["workflow_contract_valid", "runtime_disabled_in_phase1", "all_modules_have_", "evidence_prefixes_valid", "human_gate_packet_fields_present"],
        "objective": "Consolidate workflow contract, acceptance-policy, evidence-prefix, and human-gate governance candidates into one reviewable runtime-readiness task.",
        "capability_delta": "contract and acceptance governance candidates are grouped for runtime-readiness review",
    },
    {
        "group_id": "module_contracts",
        "title": "Workflow kernel consolidated candidate｜module contracts",
        "prefixes": ["module_contract:"],
        "objective": "Consolidate module contract candidates for goal calibration, source research, synthesis, acceptance compilation, and evidence verification.",
        "capability_delta": "module contract candidates are grouped for runtime integration planning",
    },
    {
        "group_id": "model_routes",
        "title": "Workflow kernel consolidated candidate｜model routes",
        "prefixes": ["model_route:"],
        "objective": "Consolidate model route candidates so runtime integration can later bind modules to pinned route aliases without opening model-call authority now.",
        "capability_delta": "model route candidates are grouped while model calls remain disabled",
    },
    {
        "group_id": "safety_flags",
        "title": "Workflow kernel consolidated candidate｜safety flags",
        "prefixes": ["safety_flag:"],
        "objective": "Consolidate safety-flag candidates that must remain explicit human gates before runtime, write-through, or external side effects are opened.",
        "capability_delta": "safety flag candidates are grouped as explicit human-gated runtime blockers",
    },
]


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_orchestrator(path: Path = ORCHESTRATOR_PATH):
    if not path.exists():
        raise SystemExit(f"brain_task_orchestrator.py not found: {path}")
    spec = importlib.util.spec_from_file_location("_workflow_candidate_consolidation_orchestrator", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _task_status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def collect_source_candidates(orchestrator: Any, source_write_id: str) -> list[Any]:
    rows = []
    for task in orchestrator.iter_tasks():
        if str(task.fm.get("workflow_import_write_id") or "") == source_write_id:
            if str(task.fm.get("workflow_import_candidate_id") or ""):
                rows.append(task)
    rows.sort(key=lambda task: str(task.fm.get("workflow_source_criterion") or task.id))
    if not rows:
        raise SystemExit(f"no imported candidate tasks found for workflow_import_write_id={source_write_id}")
    return rows


def _matches_group(criterion: str, prefixes: list[str]) -> bool:
    return any(criterion == prefix or criterion.startswith(prefix) for prefix in prefixes)


def _fm_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_groups(source_tasks: list[Any]) -> list[dict[str, Any]]:
    remaining = list(source_tasks)
    groups = []
    for spec in GROUPS:
        members = []
        for task in list(remaining):
            criterion = str(task.fm.get("workflow_source_criterion") or "")
            if _matches_group(criterion, spec["prefixes"]):
                members.append(task)
                remaining.remove(task)
        if not members:
            continue
        groups.append(
            {
                "group_id": spec["group_id"],
                "title": spec["title"],
                "objective": spec["objective"],
                "capability_delta": spec["capability_delta"],
                "source_candidate_ids": [str(t.fm.get("workflow_import_candidate_id")) for t in members],
                "source_task_ids": [t.id for t in members],
                "source_criteria": [str(t.fm.get("workflow_source_criterion")) for t in members],
                "requires_human_gate": any(_fm_bool(t.fm.get("requires_human_gate")) or str(t.fm.get("workflow_source_criterion") or "").startswith("safety_flag:") for t in members),
            }
        )
    if remaining:
        groups.append(
            {
                "group_id": "other_candidates",
                "title": "Workflow kernel consolidated candidate｜other candidates",
                "objective": "Review remaining workflow-kernel import candidates that did not match a known consolidation group.",
                "capability_delta": "remaining candidates are grouped for review",
                "source_candidate_ids": [str(t.fm.get("workflow_import_candidate_id")) for t in remaining],
                "source_task_ids": [t.id for t in remaining],
                "source_criteria": [str(t.fm.get("workflow_source_criterion")) for t in remaining],
                "requires_human_gate": True,
            }
        )
    return groups


def _existing_group_task(orchestrator: Any, consolidation_id: str, group_id: str):
    for task in orchestrator.iter_tasks():
        if str(task.fm.get("workflow_consolidation_id") or "") == consolidation_id and str(task.fm.get("workflow_consolidation_group_id") or "") == group_id:
            return task
    return None


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def consolidate_candidates(
    orchestrator: Any,
    *,
    source_write_id: str = DEFAULT_WRITE_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    mission_id: str = DEFAULT_MISSION_ID,
) -> dict[str, Any]:
    source_tasks = collect_source_candidates(orchestrator, source_write_id)
    groups = build_groups(source_tasks)
    consolidation_id = "tpcon_" + _digest({"source_write_id": source_write_id, "groups": [(g["group_id"], g["source_candidate_ids"]) for g in groups]}).split(":", 1)[1][:16]
    created_tasks = []

    for group in groups:
        existing = _existing_group_task(orchestrator, consolidation_id, group["group_id"])
        if existing is not None:
            task = existing
            idempotent_replay = True
        else:
            criteria_lines = "\n".join(f"- `{criterion}`" for criterion in group["source_criteria"])
            source_task_lines = "\n".join(f"- `{task_id}`" for task_id in group["source_task_ids"])
            extra_body = "\n".join(
                [
                    "## Consolidated Source Candidates",
                    "",
                    "Criteria:",
                    criteria_lines,
                    "",
                    "Source proposed task ids:",
                    source_task_lines,
                    "",
                    "## Boundary",
                    "",
                    "- This task is a consolidated `proposed` review item only.",
                    "- It supersedes source candidates for planning/review, but does not archive/delete them.",
                    "- Runtime, model calls, task claims, adapter writes, and external side effects remain disabled.",
                ]
            )
            result = orchestrator.create_task_note(
                title=group["title"],
                priority="P2",
                project="hermes",
                status="proposed",
                concepts=["task", "workflow-kernel", "candidate-consolidation"],
                lane="evolution",
                architecture_target="taskpool",
                capability_delta=group["capability_delta"],
                anti_drift_check="consolidated candidates must remain proposed until runtime executor integration is separately authorized",
                acceptance_evidence="readback of consolidated proposed task plus consolidation result artifact",
                priority_layer="P2",
                dependency_order=["candidate-consolidation-review", "runtime-boundary-gate"],
                multimodel_triage_result="P2:evolution:workflow-kernel-candidate-consolidation:no-runtime",
                layer="L3",
                workflow_contract="modular-workflow-kernel-candidate-consolidation",
                workspace=str(REPO_ROOT),
                parallel_boundary=f"workflow-kernel:{workflow_id}:candidate-consolidation",
                objective=group["objective"],
                accept="Consolidated candidate is reviewed for later runtime integration or rejected, without claim/runtime/model/adapter/external side effects.",
                extra_frontmatter={
                    "mission_id": mission_id,
                    "workflow_id": workflow_id,
                    "workflow_run_id": run_id,
                    "workflow_source_import_write_id": source_write_id,
                    "workflow_consolidation_id": consolidation_id,
                    "workflow_consolidation_group_id": group["group_id"],
                    "workflow_consolidated_candidate_ids": group["source_candidate_ids"],
                    "workflow_consolidated_source_task_ids": group["source_task_ids"],
                    "workflow_consolidated_source_criteria": group["source_criteria"],
                    "workflow_stage": "human_gate",
                    "completion_boundary": "taskpool_candidate",
                    "harness_surfaces": ["taskpool", "brain-note", "kanban-projection"],
                    "required_artifacts": ["candidate_consolidation_result", "brain_task_readback"],
                    "taskpool_candidate_consolidation_boundary": "no_claim_no_runtime_no_model_no_adapter",
                    "taskpool_claim_enabled": False,
                    "runtime_enabled": False,
                    "model_call_enabled": False,
                    "adapter_write_enabled": False,
                    "external_side_effects_enabled": False,
                    "requires_human_gate": bool(group["requires_human_gate"]),
                    "evidence": [
                        f"artifact:taskpool_import_write:{source_write_id}",
                        "proof:candidate_consolidation_write_no_claim",
                        "classification:workflow_kernel_consolidated_candidate",
                    ],
                },
                extra_body_sections=extra_body,
            )
            task = _readback_task(orchestrator, result["id"])
            if task is None:
                raise SystemExit(f"created consolidated task was not readable: {result['id']}")
            idempotent_replay = False

        if _task_status(task) != "proposed":
            raise SystemExit(f"consolidated task {task.id} readback status mismatch: expected proposed got {_task_status(task)}")
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group["group_id"]:
            raise SystemExit(f"consolidated task {task.id} readback group mismatch")
        created_tasks.append(
            {
                "group_id": group["group_id"],
                "task_id": task.id,
                "task_path": str(task.path),
                "status": _task_status(task),
                "source_candidate_ids": group["source_candidate_ids"],
                "source_criteria": group["source_criteria"],
                "readback_verified": True,
                "idempotent_replay": idempotent_replay,
            }
        )

    derived_refresh = orchestrator.refresh_derived_task_views("workflow_candidate_consolidation_write")
    return {
        "consolidation_id": consolidation_id,
        "source_write_id": source_write_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "consolidated_at": _now_utc(),
        "taskpool_write_enabled": True,
        "taskpool_claim_enabled": False,
        "runtime_enabled": False,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "source_candidate_count": len(source_tasks),
        "groups": groups,
        "created_tasks": created_tasks,
        "readback_verified": True,
        "derived_refresh": derived_refresh,
        "rollback_plan": "If rejected, archive/remove only the consolidated proposed task notes after separate rollback authorization; source candidate notes were not archived/deleted and no runtime/claim/model/adapter state exists.",
        "boundary": "candidate_consolidation_write_done_no_claim_no_runtime",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['consolidation_id']}.candidate_consolidation.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-write-id", default=DEFAULT_WRITE_ID)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--mission-id", default=DEFAULT_MISSION_ID)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    if not args.write:
        source_tasks = collect_source_candidates(orchestrator, args.source_write_id)
        groups = build_groups(source_tasks)
        preview = {
            "status": "READY_TO_CONSOLIDATE",
            "source_write_id": args.source_write_id,
            "workflow_id": args.workflow_id,
            "run_id": args.run_id,
            "source_candidate_count": len(source_tasks),
            "group_count": len(groups),
            "groups": [{"group_id": g["group_id"], "source_candidate_count": len(g["source_candidate_ids"]), "requires_human_gate": g["requires_human_gate"]} for g in groups],
            "taskpool_write_enabled": False,
            "boundary": "preview_only_rerun_with_write_to_create_consolidated_proposed_tasks",
        }
        print(json.dumps(preview, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    result = consolidate_candidates(
        orchestrator,
        source_write_id=args.source_write_id,
        workflow_id=args.workflow_id,
        run_id=args.run_id,
        mission_id=args.mission_id,
    )
    result["written"] = str(write_result(args.output_dir, result))
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result["written"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
