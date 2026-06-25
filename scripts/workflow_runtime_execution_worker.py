#!/usr/bin/env python3
"""Start one claimed workflow-kernel task in a local no-op runtime worker.

This opens the narrow runtime worker boundary for exactly one already-claimed
workflow-kernel task. It moves the selected task from `claimed` to `running`,
records a local no-op runtime worker packet, verifies readback, and stops before
review/done transitions, model calls, adapter writes, or external side effects.
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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-executions"
DEFAULT_RUNTIME_PACKET_ID = "rti_2c322fc59264ae3d"
DEFAULT_CLAIM_EXECUTION_ID = "clex_22a18e493dc020c4"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_GROUP_ID = "contract_acceptance_governance"
WORKER_ACTOR = "workflow_runtime_execution_worker"


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fm_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_orchestrator(path: Path = ORCHESTRATOR_PATH):
    if not path.exists():
        raise SystemExit(f"brain_task_orchestrator.py not found: {path}")
    spec = importlib.util.spec_from_file_location("_workflow_runtime_execution_worker_orchestrator", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def find_claimed_task(
    orchestrator: Any,
    runtime_packet_id: str,
    claim_execution_id: str,
    group_id: str,
    task_id: str | None = None,
) -> Any:
    matches = []
    exact_task = None
    for task in orchestrator.iter_tasks():
        if task_id and task.id == task_id:
            exact_task = task
        if str(task.fm.get("workflow_runtime_packet_id") or "") != runtime_packet_id:
            continue
        if str(task.fm.get("workflow_claim_execution_id") or "") != claim_execution_id:
            continue
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group_id:
            continue
        if task_id and task.id != task_id:
            continue
        matches.append(task)
    if task_id:
        if exact_task is None:
            raise SystemExit(f"exact task_id not found: {task_id}")
        if not matches:
            raise SystemExit(
                f"exact task_id {task_id} does not match runtime_packet_id/claim_execution_id/group_id: "
                f"runtime_packet_id={runtime_packet_id} claim_execution_id={claim_execution_id} group_id={group_id}"
            )
    if not matches:
        raise SystemExit(
            f"no claimed runtime task found for runtime_packet_id={runtime_packet_id} claim_execution_id={claim_execution_id} group_id={group_id}"
        )
    if len(matches) > 1:
        raise SystemExit(f"multiple claimed runtime tasks found for claim_execution_id={claim_execution_id}; refusing ambiguous run")
    return matches[0]


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def build_runtime_execution_id(runtime_packet_id: str, claim_execution_id: str, group_id: str, task_id: str) -> str:
    return "rtex_" + _digest(
        {
            "runtime_packet_id": runtime_packet_id,
            "claim_execution_id": claim_execution_id,
            "group_id": group_id,
            "task_id": task_id,
        }
    ).split(":", 1)[1][:16]


def start_runtime_execution(
    orchestrator: Any,
    *,
    runtime_packet_id: str = DEFAULT_RUNTIME_PACKET_ID,
    claim_execution_id: str = DEFAULT_CLAIM_EXECUTION_ID,
    group_id: str = DEFAULT_GROUP_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    task_id: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    task = find_claimed_task(orchestrator, runtime_packet_id, claim_execution_id, group_id, task_id=task_id)
    previous_status = _status(task)
    runtime_execution_id = build_runtime_execution_id(runtime_packet_id, claim_execution_id, group_id, task.id)
    existing_runtime_execution_id = str(task.fm.get("workflow_runtime_execution_id") or "")

    if previous_status == "running" and existing_runtime_execution_id == runtime_execution_id:
        return {
            "runtime_execution_id": runtime_execution_id,
            "claim_execution_id": claim_execution_id,
            "runtime_packet_id": runtime_packet_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "generated_at": _now_utc(),
            "selected_group_id": group_id,
            "selected_task_id": task.id,
            "taskpool_write_enabled": bool(write),
            "taskpool_claim_enabled": True,
            "runtime_enabled": True,
            "runtime_worker_enabled": True,
            "model_call_enabled": False,
            "adapter_write_enabled": False,
            "external_side_effects_enabled": False,
            "runtime_execution": {
                "task_id": task.id,
                "task_path": str(task.path),
                "group_id": group_id,
                "previous_status": "claimed",
                "new_status": "running",
                "worker_actor": WORKER_ACTOR,
                "worker_scope": "local_noop_runtime_marker_no_model_no_adapter",
                "execution_status": "running_noop_started_not_reviewed",
                "readback_verified": True,
            },
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "Transition the selected task from running back to claimed and remove workflow_runtime_execution_id / workflow_runtime_execution_status / workflow_runtime_worker_record / run_id after rollback authorization; no model/adapter/external work was started.",
            "boundary": "runtime_execution_worker_done_before_review_done_or_adapter",
        }

    if previous_status in {"review", "done", "archived"} and existing_runtime_execution_id:
        runtime_execution_id = existing_runtime_execution_id
        return {
            "runtime_execution_id": runtime_execution_id,
            "claim_execution_id": claim_execution_id,
            "runtime_packet_id": runtime_packet_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "generated_at": _now_utc(),
            "selected_group_id": group_id,
            "selected_task_id": task.id,
            "taskpool_write_enabled": bool(write),
            "taskpool_claim_enabled": True,
            "runtime_enabled": True,
            "runtime_worker_enabled": True,
            "model_call_enabled": _fm_bool(task.fm.get("model_call_enabled")),
            "adapter_write_enabled": _fm_bool(task.fm.get("adapter_write_enabled")),
            "external_side_effects_enabled": _fm_bool(task.fm.get("external_side_effects_enabled")),
            "runtime_execution": {
                "task_id": task.id,
                "task_path": str(task.path),
                "group_id": group_id,
                "previous_status": "claimed",
                "new_status": previous_status,
                "worker_actor": WORKER_ACTOR,
                "worker_scope": "idempotent_terminal_readback_no_mutation",
                "execution_status": str(task.fm.get("workflow_runtime_execution_status") or previous_status),
                "readback_verified": True,
            },
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "No mutation performed: selected task is already past worker boundary. Rollback must use the latest downstream stage rollback packet if needed.",
            "boundary": "runtime_execution_worker_idempotent_terminal_readback",
        }

    if previous_status != "claimed":
        raise SystemExit(f"runtime execution expects claimed task, got {task.id}:{previous_status}")
    if str(task.fm.get("workflow_claim_packet_status") or "") != "consumed_claimed_not_running":
        raise SystemExit(f"runtime execution expects consumed_claimed_not_running packet for {task.id}")
    if str(task.fm.get("claimed_by") or "") != "workflow_runtime_claim_executor":
        raise SystemExit(f"runtime execution refuses task not claimed by workflow_runtime_claim_executor: {task.id}")
    if _fm_bool(task.fm.get("requires_human_gate")):
        raise SystemExit(f"runtime execution refuses human-gated group without separate explicit group override: {group_id}")
    if _fm_bool(task.fm.get("model_call_enabled")) or _fm_bool(task.fm.get("adapter_write_enabled")) or _fm_bool(task.fm.get("external_side_effects_enabled")):
        raise SystemExit(f"runtime execution refuses task with model/adapter/external flags already enabled: {task.id}")

    worker_record = {
        "runtime_execution_id": runtime_execution_id,
        "claim_execution_id": claim_execution_id,
        "runtime_packet_id": runtime_packet_id,
        "task_id": task.id,
        "group_id": group_id,
        "worker_actor": WORKER_ACTOR,
        "worker_scope": "local_noop_runtime_marker_no_model_no_adapter",
        "execution_status": "running_noop_started_not_reviewed",
        "created_at": _now_utc(),
        "forbidden_next_without_authorization": [
            "review",
            "done",
            "model_call",
            "adapter_write",
            "external_side_effect",
        ],
    }

    if write:
        try:
            task.status = "running"
        except AttributeError:
            pass
        task.fm["status"] = "running"
        task.fm["workflow_runtime_execution_id"] = runtime_execution_id
        task.fm["workflow_runtime_execution_status"] = "running_noop_started_not_reviewed"
        task.fm["workflow_runtime_worker_record"] = worker_record
        task.fm["run_id"] = runtime_execution_id
        task.fm["taskpool_claim_enabled"] = True
        task.fm["runtime_enabled"] = True
        task.fm["runtime_worker_enabled"] = True
        task.fm["model_call_enabled"] = False
        task.fm["adapter_write_enabled"] = False
        task.fm["external_side_effects_enabled"] = False
        evidence = list(task.fm.get("evidence") or [])
        for marker in [
            f"artifact:runtime_execution_worker_packet:{runtime_execution_id}",
            "proof:runtime_execution_worker_running_noop_no_model_no_adapter",
            "classification:workflow_kernel_runtime_execution_started",
        ]:
            if marker not in evidence:
                evidence.append(marker)
        task.fm["evidence"] = evidence
        orchestrator.save_task(task)
        readback = _readback_task(orchestrator, task.id)
        if readback is None:
            raise SystemExit(f"running task was not readable: {task.id}")
        if _status(readback) != "running":
            raise SystemExit(f"runtime execution readback status mismatch for {task.id}: {_status(readback)}")
        if str(readback.fm.get("workflow_runtime_execution_id") or "") != runtime_execution_id:
            raise SystemExit(f"runtime execution id readback mismatch for {task.id}")
        if str(readback.fm.get("workflow_runtime_execution_status") or "") != "running_noop_started_not_reviewed":
            raise SystemExit(f"runtime execution status readback mismatch for {task.id}")
        task = readback
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_execution_worker")
    else:
        derived_refresh = None

    return {
        "runtime_execution_id": runtime_execution_id,
        "claim_execution_id": claim_execution_id,
        "runtime_packet_id": runtime_packet_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "generated_at": _now_utc(),
        "selected_group_id": group_id,
        "selected_task_id": task.id,
        "taskpool_write_enabled": bool(write),
        "taskpool_claim_enabled": True if write else False,
        "runtime_enabled": True,
        "runtime_worker_enabled": True if write else False,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "runtime_execution": {
            "task_id": task.id,
            "task_path": str(task.path),
            "group_id": group_id,
            "previous_status": previous_status,
            "new_status": "running" if write else previous_status,
            "worker_actor": WORKER_ACTOR,
            "worker_scope": "local_noop_runtime_marker_no_model_no_adapter",
            "execution_status": "running_noop_started_not_reviewed" if write else "preview_not_started",
            "readback_verified": bool(write),
        },
        "readback_verified": bool(write),
        "derived_refresh": derived_refresh,
        "rollback_plan": "Transition the selected task from running back to claimed and remove workflow_runtime_execution_id / workflow_runtime_execution_status / workflow_runtime_worker_record / run_id after rollback authorization; no model/adapter/external work was started.",
        "boundary": "runtime_execution_worker_done_before_review_done_or_adapter" if write else "runtime_execution_worker_preview_before_running",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['runtime_packet_id']}.{result['claim_execution_id']}.{result['runtime_execution_id']}.runtime_execution_worker_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-packet-id", default=DEFAULT_RUNTIME_PACKET_ID)
    parser.add_argument("--claim-execution-id", default=DEFAULT_CLAIM_EXECUTION_ID)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--task-id", default=None, help="Exact task id to select when claim/group matches are duplicated")
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    result = start_runtime_execution(
        orchestrator,
        runtime_packet_id=args.runtime_packet_id,
        claim_execution_id=args.claim_execution_id,
        group_id=args.group_id,
        workflow_id=args.workflow_id,
        run_id=args.run_id,
        task_id=args.task_id,
        write=args.write,
    )
    if args.write:
        result["written"] = str(write_result(args.output_dir, result))
    if args.as_json or not args.write:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
