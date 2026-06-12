#!/usr/bin/env python3
"""Generate taskpool import candidates for a workflow executor dry-run result.

This is intentionally a dry-run importer. It translates local executor-preview
steps into canonical taskpool *candidate* records, but it never writes brain task
notes, kanban rows, claims, runtime lifecycle state, model calls, adapters, or
external side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXECUTOR_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-executor-dry-runs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-taskpool-import-dry-runs"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"

FORBIDDEN_TRUE_FLAGS = (
    "runtime_enabled",
    "taskpool_claim_enabled",
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


def find_executor_result(workflow_id: str, run_id: str, executor_dir: Path = DEFAULT_EXECUTOR_DIR) -> Path:
    matches = sorted(executor_dir.glob(f"{workflow_id}.{run_id}.execdry_*.executor_dry_run.json"))
    if not matches:
        raise SystemExit(f"no executor dry-run artifact found for {workflow_id}/{run_id} in {executor_dir}")
    return matches[-1]


def _criterion_title(criterion: str) -> str:
    readable = criterion.replace(":", " / ").replace("_", " ").replace(".", " ")
    return f"Workflow dry-run candidate: {readable}"


def build_import_dry_run(executor_result: dict[str, Any]) -> dict[str, Any]:
    if executor_result.get("dry_run_only") is not True:
        raise SystemExit("executor result must be dry_run_only")
    if executor_result.get("local_reversible_only") is not True:
        raise SystemExit("executor result must be local_reversible_only")
    for flag in FORBIDDEN_TRUE_FLAGS:
        if executor_result.get(flag) is not False:
            raise SystemExit(f"executor result crosses forbidden boundary: {flag}=true")

    steps = executor_result.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SystemExit("executor result must contain non-empty steps")

    seed = {
        "run_id": executor_result["run_id"],
        "workflow_id": executor_result["workflow_id"],
        "executor_result_id": executor_result["result_id"],
        "step_count": len(steps),
    }
    import_id = "tpdry_" + _digest(seed).split(":", 1)[1][:16]
    candidate_tasks: list[dict[str, Any]] = []

    for index, step in enumerate(steps, start=1):
        source_criterion = step["source_criterion"]
        boundary_blocked = step.get("status") == "blocked_by_boundary"
        candidate_seed = {
            "import_id": import_id,
            "source_step_id": step["step_id"],
            "source_criterion": source_criterion,
        }
        candidate_id = "candidate_" + _digest(candidate_seed).split(":", 1)[1][:12]
        frontmatter_preview = {
            "status": "blocked" if boundary_blocked else "pending",
            "priority": "P2",
            "lane": "evolution",
            "project": "hermes",
            "architecture_target": "modular-workflow-kernel",
            "workflow_id": executor_result["workflow_id"],
            "workflow_run_id": executor_result["run_id"],
            "workflow_executor_result_id": executor_result["result_id"],
            "workflow_import_dry_run_id": import_id,
            "source_criterion": source_criterion,
            "dry_run_candidate_only": True,
            "taskpool_write_enabled": False,
            "requires_human_gate": boundary_blocked,
        }
        body_preview = "\n".join(
            [
                f"# {_criterion_title(source_criterion)}",
                "",
                "This is a taskpool import candidate generated from a workflow executor dry-run.",
                "It is not a canonical brain task and must not be claimed until a separate taskpool import write gate is authorized.",
                "",
                f"- Source step: `{step['step_id']}`",
                f"- Source criterion: `{source_criterion}`",
                f"- Boundary status: `{'human_gate_required' if boundary_blocked else 'candidate_pending'}`",
            ]
        )
        candidate_tasks.append(
            {
                "candidate_id": candidate_id,
                "source_step_id": step["step_id"],
                "source_criterion": source_criterion,
                "title": _criterion_title(source_criterion),
                "lane": "evolution",
                "priority": "P2",
                "status_candidate": "blocked" if boundary_blocked else "pending",
                "frontmatter_preview": frontmatter_preview,
                "body_preview": body_preview,
                "evidence": [
                    f"artifact:executor_dry_run:{executor_result['result_id']}",
                    "proof:taskpool_import_dry_run_no_canonical_write",
                    "classification:workflow_kernel_import_candidate",
                ],
                "write_enabled": False,
            }
        )

    return {
        "import_id": import_id,
        "run_id": executor_result["run_id"],
        "workflow_id": executor_result["workflow_id"],
        "executor_result_id": executor_result["result_id"],
        "generated_at": _now_utc(),
        "dry_run_only": True,
        "taskpool_write_enabled": False,
        "taskpool_claim_enabled": False,
        "runtime_enabled": False,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "candidate_tasks": candidate_tasks,
        "human_gate_packet": {
            "decision_needed": "Authorize or reject writing these dry-run candidates into the canonical taskpool.",
            "confirmed_facts": [
                "Executor dry-run artifact exists",
                "All runtime/model/adapter/external side-effect flags remain false",
                "Candidate task previews were generated without writing brain notes or kanban rows",
            ],
            "unconfirmed_facts": [
                "Canonical taskpool write has not been authorized",
                "Runtime executor integration has not been authorized",
                "Adapter write-through remains unopened",
            ],
            "options": [
                "continue_taskpool_import_dry_run_only",
                "authorize_taskpool_import_write",
                "stop_and_review_candidates",
            ],
            "recommended_default": "continue_taskpool_import_dry_run_only",
            "blast_radius": "Local JSON artifact only; no canonical taskpool mutation unless a separate write gate is opened.",
            "rollback_plan": "Delete this import dry-run artifact; no brain/kanban/runtime state was mutated.",
            "post_action_verification": "If write is later authorized, read back brain task note(s), kanban projection, taskpool list/next, and proof-check/selfcheck.",
        },
        "rollback_plan": "Delete this dry-run JSON artifact. No canonical taskpool state was written.",
        "boundary": "taskpool_import_dry_run_only_no_canonical_write_no_claim",
    }


def write_import_dry_run(output_dir: Path, import_result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{import_result['workflow_id']}.{import_result['run_id']}.{import_result['import_id']}.taskpool_import_dry_run.json"
    path.write_text(json.dumps(import_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--executor-result", type=Path)
    parser.add_argument("--executor-dir", type=Path, default=DEFAULT_EXECUTOR_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    executor_path = args.executor_result or find_executor_result(args.workflow_id, args.run_id, args.executor_dir)
    executor_result = _load_json(executor_path)
    import_result = build_import_dry_run(executor_result)
    if args.write:
        import_result["written"] = str(write_import_dry_run(args.output_dir, import_result))
    if args.as_json or not args.write:
        print(json.dumps(import_result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(import_result.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
