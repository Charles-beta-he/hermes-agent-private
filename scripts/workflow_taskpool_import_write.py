#!/usr/bin/env python3
"""Write workflow taskpool import candidates into the canonical local taskpool.

This is the first SSOT-mutation boundary for the modular workflow kernel. It is
narrow by design: it creates proposed brain task notes from a previously generated
Taskpool Import Dry-run artifact, verifies readback, refreshes derived views, and
then stops. It does not claim tasks, run workflows, call models, write adapters,
or send external messages.
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
DEFAULT_IMPORT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-taskpool-import-dry-runs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-taskpool-import-writes"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_MISSION_ID = "msn_20260610_120149_27c88cfc"

FORBIDDEN_TRUE_FLAGS = (
    "taskpool_claim_enabled",
    "runtime_enabled",
    "model_call_enabled",
    "adapter_write_enabled",
    "external_side_effects_enabled",
)


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_import_dry_run(workflow_id: str, run_id: str, import_dir: Path = DEFAULT_IMPORT_DIR) -> Path:
    matches = sorted(import_dir.glob(f"{workflow_id}.{run_id}.tpdry_*.taskpool_import_dry_run.json"))
    if not matches:
        raise SystemExit(f"no taskpool import dry-run artifact found for {workflow_id}/{run_id} in {import_dir}")
    return matches[-1]


def load_orchestrator(path: Path = ORCHESTRATOR_PATH):
    if not path.exists():
        raise SystemExit(f"brain_task_orchestrator.py not found: {path}")
    spec = importlib.util.spec_from_file_location("_workflow_taskpool_import_write_orchestrator", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_import_dry_run(import_result: dict[str, Any]) -> None:
    if import_result.get("dry_run_only") is not True:
        raise SystemExit("taskpool import artifact must be dry_run_only")
    if import_result.get("taskpool_write_enabled") is not False:
        raise SystemExit("taskpool import artifact must have taskpool_write_enabled=false")
    for flag in FORBIDDEN_TRUE_FLAGS:
        if import_result.get(flag) is not False:
            raise SystemExit(f"taskpool import artifact crosses forbidden boundary: {flag}=true")
    candidates = import_result.get("candidate_tasks")
    if not isinstance(candidates, list) or not candidates:
        raise SystemExit("taskpool import artifact must contain non-empty candidate_tasks")
    for candidate in candidates:
        if candidate.get("write_enabled") is not False:
            raise SystemExit(f"candidate {candidate.get('candidate_id')} is not write-disabled in dry-run artifact")


def build_write_id(import_result: dict[str, Any], max_candidates: int | None) -> str:
    seed = {
        "import_id": import_result["import_id"],
        "run_id": import_result["run_id"],
        "workflow_id": import_result["workflow_id"],
        "candidate_ids": [c["candidate_id"] for c in import_result["candidate_tasks"][:max_candidates]],
    }
    return "tpwrite_" + _digest(seed).split(":", 1)[1][:16]


def _existing_task_by_candidate(orchestrator: Any, candidate_id: str):
    for task in orchestrator.iter_tasks():
        if str(task.fm.get("workflow_import_candidate_id") or "") == candidate_id:
            return task
    return None


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def write_candidates(
    import_result: dict[str, Any],
    orchestrator: Any,
    *,
    max_candidates: int | None = None,
    mission_id: str = DEFAULT_MISSION_ID,
) -> dict[str, Any]:
    validate_import_dry_run(import_result)
    candidates = import_result["candidate_tasks"][:max_candidates]
    write_id = build_write_id(import_result, max_candidates)
    created_tasks: list[dict[str, Any]] = []

    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        existing = _existing_task_by_candidate(orchestrator, candidate_id)
        if existing is not None:
            task = existing
            idempotent_replay = True
        else:
            source_criterion = candidate["source_criterion"]
            boundary_sensitive = candidate.get("status_candidate") == "blocked"
            title = f"Workflow kernel import candidate｜{source_criterion}"
            objective = (
                "Review and land the modular workflow-kernel taskpool import candidate without "
                "crossing runtime executor or adapter write-through boundaries.\n\n"
                f"Source import: {import_result['import_id']}\n"
                f"Source criterion: {source_criterion}\n"
                f"Candidate id: {candidate_id}"
            )
            accept = (
                "Candidate is either approved for a later bounded workflow-kernel slice or explicitly rejected, "
                "with evidence recorded; no taskpool claim/runtime/model/adapter/external side effect is performed by this import write."
            )
            extra_frontmatter = {
                "mission_id": mission_id,
                "workflow_id": import_result["workflow_id"],
                "workflow_run_id": import_result["run_id"],
                "workflow_taskpool_import_id": import_result["import_id"],
                "workflow_import_write_id": write_id,
                "workflow_import_candidate_id": candidate_id,
                "workflow_source_criterion": source_criterion,
                "taskpool_import_write_boundary": "no_claim_no_runtime_no_model_no_adapter",
                "taskpool_claim_enabled": False,
                "runtime_enabled": False,
                "model_call_enabled": False,
                "adapter_write_enabled": False,
                "external_side_effects_enabled": False,
                "requires_human_gate": bool(boundary_sensitive),
                "workflow_stage": "human_gate",
                "completion_boundary": "taskpool_candidate",
                "harness_surfaces": ["taskpool", "brain-note", "kanban-projection"],
                "required_artifacts": ["taskpool_import_write_result", "brain_task_readback"],
                "evidence": [
                    f"artifact:taskpool_import_dry_run:{import_result['import_id']}",
                    "proof:canonical_taskpool_import_write_no_claim",
                    "classification:workflow_kernel_import_candidate",
                ],
            }
            extra_body = "\n".join(
                [
                    "## Workflow Import Candidate",
                    "",
                    candidate.get("body_preview", "").strip(),
                    "",
                    "## Boundary",
                    "",
                    "- This canonical task was imported as `status: proposed` only.",
                    "- It must not be claimed or executed until a separate runtime executor authorization exists.",
                    "- Runtime/model/adapter/external side effects remain disabled.",
                ]
            )
            result = orchestrator.create_task_note(
                title=title,
                priority="P2",
                project="hermes",
                status="proposed",
                concepts=["task", "workflow-kernel", "taskpool-import"],
                lane="evolution",
                architecture_target="taskpool",
                capability_delta="modular workflow kernel taskpool import candidate is now represented in canonical taskpool SSOT",
                anti_drift_check="do not claim or execute before runtime executor authorization; keep taskpool as SSOT",
                acceptance_evidence="readback of canonical proposed task note plus workflow import write artifact",
                priority_layer="P2",
                dependency_order=["candidate-review", "runtime-boundary-gate"],
                multimodel_triage_result="P2:evolution:workflow-kernel-import-write:no-runtime",
                layer="L3",
                workflow_contract="modular-workflow-kernel-taskpool-import-write",
                workspace=str(REPO_ROOT),
                parallel_boundary=f"workflow-kernel:{import_result['workflow_id']}:taskpool-import-write",
                objective=objective,
                accept=accept,
                extra_frontmatter=extra_frontmatter,
                extra_body_sections=extra_body,
            )
            task = _readback_task(orchestrator, result["id"])
            if task is None:
                raise SystemExit(f"created task was not readable: {result['id']}")
            idempotent_replay = False

        if task.status != "proposed":
            if idempotent_replay and task.status in {"archived", "done", "review", "blocked"}:
                created_tasks.append(
                    {
                        "candidate_id": candidate_id,
                        "task_id": task.id,
                        "task_path": str(task.path),
                        "status": task.status,
                        "readback_verified": True,
                        "idempotent_replay": True,
                        "closed_existing": True,
                    }
                )
                continue
            raise SystemExit(f"task {task.id} readback status mismatch: expected proposed got {task.status}")
        if str(task.fm.get("workflow_import_candidate_id") or "") != candidate_id:
            raise SystemExit(f"task {task.id} readback candidate mismatch")
        created_tasks.append(
            {
                "candidate_id": candidate_id,
                "task_id": task.id,
                "task_path": str(task.path),
                "status": task.status,
                "readback_verified": True,
                "idempotent_replay": idempotent_replay,
            }
        )

    derived_refresh = orchestrator.refresh_derived_task_views("workflow_taskpool_import_write")
    return {
        "write_id": write_id,
        "import_id": import_result["import_id"],
        "run_id": import_result["run_id"],
        "workflow_id": import_result["workflow_id"],
        "written_at": _now_utc(),
        "taskpool_write_enabled": True,
        "taskpool_claim_enabled": False,
        "runtime_enabled": False,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "created_tasks": created_tasks,
        "readback_verified": True,
        "derived_refresh": derived_refresh,
        "rollback_plan": "If this import must be rolled back, archive or remove only the listed proposed task notes after a separate rollback authorization; no runtime/claim/model/adapter state exists.",
        "boundary": "taskpool_import_write_done_no_claim_no_runtime",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['write_id']}.taskpool_import_write.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--import-dry-run", type=Path)
    parser.add_argument("--import-dir", type=Path, default=DEFAULT_IMPORT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--mission-id", default=DEFAULT_MISSION_ID)
    parser.add_argument("--max-candidates", type=int, default=0, help="0 means all candidates")
    parser.add_argument("--write", action="store_true", help="Actually create proposed canonical taskpool task notes")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    import_path = args.import_dry_run or find_import_dry_run(args.workflow_id, args.run_id, args.import_dir)
    import_result = _load_json(import_path)
    validate_import_dry_run(import_result)
    if not args.write:
        preview = {
            "status": "READY_TO_WRITE",
            "import_id": import_result["import_id"],
            "workflow_id": import_result["workflow_id"],
            "run_id": import_result["run_id"],
            "candidate_count": len(import_result["candidate_tasks"]),
            "taskpool_write_enabled": False,
            "boundary": "preview_only_rerun_with_write_to_mutate_taskpool",
        }
        print(json.dumps(preview, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    orchestrator = load_orchestrator(args.orchestrator)
    max_candidates = args.max_candidates or None
    result = write_candidates(import_result, orchestrator, max_candidates=max_candidates, mission_id=args.mission_id)
    written_path = write_result(args.output_dir, result)
    result["written"] = str(written_path)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(str(written_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
