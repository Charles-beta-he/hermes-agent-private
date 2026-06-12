#!/usr/bin/env python3
"""Consume one prepared workflow-kernel runtime claim packet.

This opens the narrow taskpool claim boundary for exactly one consolidated
workflow-kernel task. It moves one selected `proposed` task to `claimed` with a
local claim marker and readback verification, then stops before running work,
calling models, writing adapters, or causing external side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_HOME = Path.home() / ".hermes"
ORCHESTRATOR_PATH = HERMES_HOME / "scripts" / "brain_task_orchestrator.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-claims"
DEFAULT_RUNTIME_PACKET_ID = "rti_2c322fc59264ae3d"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_GROUP_ID = "contract_acceptance_governance"
CLAIM_ACTOR = "workflow_runtime_claim_executor"


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ttl_utc(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fm_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_orchestrator(path: Path = ORCHESTRATOR_PATH):
    if not path.exists():
        raise SystemExit(f"brain_task_orchestrator.py not found: {path}")
    spec = importlib.util.spec_from_file_location("_workflow_runtime_claim_executor_orchestrator", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def find_target_task(orchestrator: Any, runtime_packet_id: str, group_id: str) -> Any:
    matches = []
    for task in orchestrator.iter_tasks():
        if str(task.fm.get("workflow_runtime_packet_id") or "") != runtime_packet_id:
            continue
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group_id:
            continue
        matches.append(task)
    if not matches:
        raise SystemExit(f"no prepared runtime task found for runtime_packet_id={runtime_packet_id} group_id={group_id}")
    if len(matches) > 1:
        raise SystemExit(f"multiple prepared runtime tasks found for group_id={group_id}; refusing ambiguous claim")
    return matches[0]


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def build_claim_execution_id(runtime_packet_id: str, group_id: str, task_id: str) -> str:
    return "clex_" + _digest({"runtime_packet_id": runtime_packet_id, "group_id": group_id, "task_id": task_id}).split(":", 1)[1][:16]


def execute_runtime_claim(
    orchestrator: Any,
    *,
    runtime_packet_id: str = DEFAULT_RUNTIME_PACKET_ID,
    group_id: str = DEFAULT_GROUP_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    write: bool = False,
) -> dict[str, Any]:
    task = find_target_task(orchestrator, runtime_packet_id, group_id)
    previous_status = _status(task)
    claim_execution_id = build_claim_execution_id(runtime_packet_id, group_id, task.id)
    if previous_status == "claimed" and str(task.fm.get("workflow_claim_execution_id") or "") == claim_execution_id:
        already_result = {
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
            "runtime_record_only": False,
            "model_call_enabled": False,
            "adapter_write_enabled": False,
            "external_side_effects_enabled": False,
            "claim_execution": {
                "task_id": task.id,
                "task_path": str(task.path),
                "group_id": group_id,
                "previous_status": "proposed",
                "new_status": "claimed",
                "claim_packet_status": "consumed_claimed_not_running",
                "claimed_by": CLAIM_ACTOR,
                "claim_scope": "single_task_no_worker_no_model_no_adapter",
                "readback_verified": True,
            },
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "Transition the selected task from claimed back to proposed and remove workflow_claim_execution_id / workflow_claim_state / workflow_claim_record / claimed_by / claim_expires_at after rollback authorization; no running/model/adapter/external work was started.",
            "boundary": "runtime_claim_executor_done_before_running_or_adapter",
        }
        return already_result
    if previous_status != "proposed":
        raise SystemExit(f"runtime claim expects proposed task, got {task.id}:{previous_status}")
    if str(task.fm.get("workflow_claim_packet_status") or "") != "prepared_not_claimed":
        raise SystemExit(f"runtime claim expects prepared_not_claimed packet for {task.id}")
    if _fm_bool(task.fm.get("requires_human_gate")):
        raise SystemExit(f"runtime claim refuses human-gated group without a separate explicit group override: {group_id}")
    if _fm_bool(task.fm.get("model_call_enabled")) or _fm_bool(task.fm.get("adapter_write_enabled")) or _fm_bool(task.fm.get("external_side_effects_enabled")):
        raise SystemExit(f"runtime claim refuses task with model/adapter/external flags already enabled: {task.id}")

    claim_record = {
        "claim_execution_id": claim_execution_id,
        "runtime_packet_id": runtime_packet_id,
        "task_id": task.id,
        "group_id": group_id,
        "claimed_by": CLAIM_ACTOR,
        "claim_scope": "single_task_no_worker_no_model_no_adapter",
        "claim_packet_status": "consumed_claimed_not_running",
        "created_at": _now_utc(),
        "expires_at": _ttl_utc(),
        "forbidden_next_without_authorization": [
            "running",
            "review",
            "done",
            "model_call",
            "adapter_write",
            "external_side_effect",
        ],
    }

    if write:
        try:
            task.status = "claimed"
        except AttributeError:
            pass
        task.fm["status"] = "claimed"
        task.fm["workflow_claim_execution_id"] = claim_execution_id
        task.fm["workflow_claim_packet_status"] = "consumed_claimed_not_running"
        task.fm["workflow_claim_state"] = "claimed_waiting_for_runtime_execution_authorization"
        task.fm["workflow_claim_record"] = claim_record
        task.fm["claimed_by"] = CLAIM_ACTOR
        task.fm["claim_expires_at"] = claim_record["expires_at"]
        task.fm["taskpool_claim_enabled"] = True
        task.fm["runtime_enabled"] = True
        task.fm["runtime_record_only"] = False
        task.fm["model_call_enabled"] = False
        task.fm["adapter_write_enabled"] = False
        task.fm["external_side_effects_enabled"] = False
        evidence = list(task.fm.get("evidence") or [])
        for marker in [
            f"artifact:runtime_claim_execution_packet:{claim_execution_id}",
            "proof:runtime_claim_executor_claimed_no_running_no_model_no_adapter",
            "classification:workflow_kernel_runtime_claim_consumed",
        ]:
            if marker not in evidence:
                evidence.append(marker)
        task.fm["evidence"] = evidence
        orchestrator.save_task(task)
        readback = _readback_task(orchestrator, task.id)
        if readback is None:
            raise SystemExit(f"claimed task was not readable: {task.id}")
        if _status(readback) != "claimed":
            raise SystemExit(f"claim readback status mismatch for {task.id}: {_status(readback)}")
        if str(readback.fm.get("workflow_claim_execution_id") or "") != claim_execution_id:
            raise SystemExit(f"claim execution id readback mismatch for {task.id}")
        if str(readback.fm.get("workflow_claim_packet_status") or "") != "consumed_claimed_not_running":
            raise SystemExit(f"claim packet status readback mismatch for {task.id}")
        task = readback
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_claim_executor")
    else:
        derived_refresh = None

    result = {
        "claim_execution_id": claim_execution_id,
        "runtime_packet_id": runtime_packet_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "generated_at": _now_utc(),
        "selected_group_id": group_id,
        "selected_task_id": task.id,
        "taskpool_write_enabled": bool(write),
        "taskpool_claim_enabled": bool(write),
        "runtime_enabled": True,
        "runtime_record_only": False if write else True,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "claim_execution": {
            "task_id": task.id,
            "task_path": str(task.path),
            "group_id": group_id,
            "previous_status": previous_status,
            "new_status": "claimed" if write else previous_status,
            "claim_packet_status": "consumed_claimed_not_running" if write else "prepared_not_claimed",
            "claimed_by": CLAIM_ACTOR,
            "claim_scope": "single_task_no_worker_no_model_no_adapter",
            "readback_verified": bool(write),
        },
        "readback_verified": bool(write),
        "derived_refresh": derived_refresh,
        "rollback_plan": "Transition the selected task from claimed back to proposed and remove workflow_claim_execution_id / workflow_claim_state / workflow_claim_record / claimed_by / claim_expires_at after rollback authorization; no running/model/adapter/external work was started.",
        "boundary": "runtime_claim_executor_done_before_running_or_adapter" if write else "runtime_claim_executor_preview_before_claim",
    }
    return result


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['runtime_packet_id']}.{result['claim_execution_id']}.runtime_claim_execution_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-packet-id", default=DEFAULT_RUNTIME_PACKET_ID)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    result = execute_runtime_claim(
        orchestrator,
        runtime_packet_id=args.runtime_packet_id,
        group_id=args.group_id,
        workflow_id=args.workflow_id,
        run_id=args.run_id,
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
