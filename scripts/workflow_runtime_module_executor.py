#!/usr/bin/env python3
"""Execute one running workflow-kernel task with a local deterministic module.

This is the first non-noop execution-plane slice after the runtime worker marker.
It consumes exactly one running workflow-kernel task, runs a local contract
validation module, writes a bounded module execution packet, transitions the task
from `running` to `review`, verifies readback, and stops before `done`, model
calls, adapter writes, or external side effects.
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
VALIDATOR_PATH = REPO_ROOT / "scripts" / "workflow_contract_validator.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-module-executions"
DEFAULT_RUNTIME_PACKET_ID = "rti_2c322fc59264ae3d"
DEFAULT_CLAIM_EXECUTION_ID = "clex_22a18e493dc020c4"
DEFAULT_RUNTIME_EXECUTION_ID = "rtex_8d01cdb6170a549f"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_GROUP_ID = "contract_acceptance_governance"
MODULE_EXECUTOR_ACTOR = "workflow_runtime_module_executor"
MODULE_ID = "contract_acceptance_governance.local_validator.v1"
MODULE_SCOPE = "local_deterministic_contract_validation_no_model_no_adapter"


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


def _load_module(path: Path, name: str):
    if not path.exists():
        raise SystemExit(f"module not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_orchestrator(path: Path = ORCHESTRATOR_PATH):
    return _load_module(path, "_workflow_runtime_module_executor_orchestrator")


def load_validator(path: Path = VALIDATOR_PATH):
    return _load_module(path, "_workflow_runtime_module_executor_validator")


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def find_running_task(
    orchestrator: Any,
    runtime_packet_id: str,
    claim_execution_id: str,
    runtime_execution_id: str,
    group_id: str,
) -> Any:
    matches = []
    for task in orchestrator.iter_tasks():
        if str(task.fm.get("workflow_runtime_packet_id") or "") != runtime_packet_id:
            continue
        if str(task.fm.get("workflow_claim_execution_id") or "") != claim_execution_id:
            continue
        if str(task.fm.get("workflow_runtime_execution_id") or "") != runtime_execution_id:
            continue
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group_id:
            continue
        matches.append(task)
    if not matches:
        raise SystemExit(
            "no running runtime task found for "
            f"runtime_packet_id={runtime_packet_id} claim_execution_id={claim_execution_id} "
            f"runtime_execution_id={runtime_execution_id} group_id={group_id}"
        )
    if len(matches) > 1:
        raise SystemExit(f"multiple running runtime tasks found for runtime_execution_id={runtime_execution_id}; refusing ambiguous run")
    return matches[0]


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def build_module_execution_id(runtime_execution_id: str, group_id: str, task_id: str, module_id: str = MODULE_ID) -> str:
    return "modex_" + _digest(
        {
            "runtime_execution_id": runtime_execution_id,
            "group_id": group_id,
            "task_id": task_id,
            "module_id": module_id,
        }
    ).split(":", 1)[1][:16]


def run_local_module(validator: Any) -> dict[str, Any]:
    result = validator.validate_registry(validator.DEFAULT_REGISTRY)
    payload = result.to_dict()
    return {
        "module_id": MODULE_ID,
        "module_scope": MODULE_SCOPE,
        "validator_status": payload.get("status"),
        "validator_summary": {
            "validated_workflows": payload.get("validated_workflows"),
            "validated_modules": payload.get("validated_modules"),
            "validated_route_aliases": payload.get("validated_route_aliases"),
            "errors": payload.get("errors", []),
            "warnings": payload.get("warnings", []),
        },
        "validator_digest": _digest(payload),
    }


def execute_runtime_module(
    orchestrator: Any,
    validator: Any,
    *,
    runtime_packet_id: str = DEFAULT_RUNTIME_PACKET_ID,
    claim_execution_id: str = DEFAULT_CLAIM_EXECUTION_ID,
    runtime_execution_id: str = DEFAULT_RUNTIME_EXECUTION_ID,
    group_id: str = DEFAULT_GROUP_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    write: bool = False,
) -> dict[str, Any]:
    task = find_running_task(orchestrator, runtime_packet_id, claim_execution_id, runtime_execution_id, group_id)
    previous_status = _status(task)
    module_execution_id = build_module_execution_id(runtime_execution_id, group_id, task.id)

    if previous_status == "review" and str(task.fm.get("workflow_module_execution_id") or "") == module_execution_id:
        return {
            "module_execution_id": module_execution_id,
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
            "runtime_module_enabled": True,
            "model_call_enabled": False,
            "adapter_write_enabled": False,
            "external_side_effects_enabled": False,
            "module_execution": {
                "task_id": task.id,
                "task_path": str(task.path),
                "group_id": group_id,
                "previous_status": "running",
                "new_status": "review",
                "executor_actor": MODULE_EXECUTOR_ACTOR,
                "module_id": MODULE_ID,
                "module_scope": MODULE_SCOPE,
                "execution_status": "module_execution_review_ready",
                "validator_status": "PASS",
                "readback_verified": True,
            },
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "Transition the selected task from review back to running and remove workflow_module_execution_id / workflow_module_execution_status / workflow_module_execution_record after rollback authorization; no model/adapter/external work was started.",
            "boundary": "runtime_module_execution_done_before_done_model_or_adapter",
        }

    if previous_status != "running":
        raise SystemExit(f"runtime module execution expects running task, got {task.id}:{previous_status}")
    if str(task.fm.get("workflow_runtime_execution_status") or "") != "running_noop_started_not_reviewed":
        raise SystemExit(f"runtime module execution expects running_noop_started_not_reviewed for {task.id}")
    if str(task.fm.get("claimed_by") or "") != "workflow_runtime_claim_executor":
        raise SystemExit(f"runtime module execution refuses task not claimed by workflow_runtime_claim_executor: {task.id}")
    if _fm_bool(task.fm.get("requires_human_gate")):
        raise SystemExit(f"runtime module execution refuses human-gated group without separate explicit group override: {group_id}")
    if _fm_bool(task.fm.get("model_call_enabled")) or _fm_bool(task.fm.get("adapter_write_enabled")) or _fm_bool(task.fm.get("external_side_effects_enabled")):
        raise SystemExit(f"runtime module execution refuses task with model/adapter/external flags already enabled: {task.id}")

    module_output = run_local_module(validator)
    if module_output["validator_status"] != "PASS":
        raise SystemExit("local contract validation module failed; refusing review transition")

    execution_record = {
        "module_execution_id": module_execution_id,
        "runtime_execution_id": runtime_execution_id,
        "claim_execution_id": claim_execution_id,
        "runtime_packet_id": runtime_packet_id,
        "task_id": task.id,
        "group_id": group_id,
        "executor_actor": MODULE_EXECUTOR_ACTOR,
        "module_id": MODULE_ID,
        "module_scope": MODULE_SCOPE,
        "execution_status": "module_execution_review_ready",
        "created_at": _now_utc(),
        "module_output": module_output,
        "forbidden_next_without_authorization": [
            "done",
            "model_call",
            "adapter_write",
            "external_side_effect",
        ],
    }

    if write:
        try:
            task.status = "review"
        except AttributeError:
            pass
        task.fm["status"] = "review"
        task.fm["workflow_runtime_execution_status"] = "module_execution_review_ready"
        task.fm["workflow_module_execution_id"] = module_execution_id
        task.fm["workflow_module_execution_status"] = "module_execution_review_ready"
        task.fm["workflow_module_execution_record"] = execution_record
        task.fm["runtime_module_enabled"] = True
        task.fm["model_call_enabled"] = False
        task.fm["adapter_write_enabled"] = False
        task.fm["external_side_effects_enabled"] = False
        evidence = list(task.fm.get("evidence") or [])
        for marker in [
            f"artifact:runtime_module_execution_packet:{module_execution_id}",
            "command:workflow_contract_validator PASS",
            "proof:local_deterministic_module_execution_no_model_no_adapter",
            "classification:workflow_kernel_runtime_module_execution_review_ready",
        ]:
            if marker not in evidence:
                evidence.append(marker)
        task.fm["evidence"] = evidence
        orchestrator.save_task(task)
        readback = _readback_task(orchestrator, task.id)
        if readback is None:
            raise SystemExit(f"review task was not readable: {task.id}")
        if _status(readback) != "review":
            raise SystemExit(f"runtime module execution readback status mismatch for {task.id}: {_status(readback)}")
        if str(readback.fm.get("workflow_module_execution_id") or "") != module_execution_id:
            raise SystemExit(f"runtime module execution id readback mismatch for {task.id}")
        if str(readback.fm.get("workflow_module_execution_status") or "") != "module_execution_review_ready":
            raise SystemExit(f"runtime module execution status readback mismatch for {task.id}")
        task = readback
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_module_executor")
    else:
        derived_refresh = None

    return {
        "module_execution_id": module_execution_id,
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
        "runtime_module_enabled": True,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "module_execution": {
            "task_id": task.id,
            "task_path": str(task.path),
            "group_id": group_id,
            "previous_status": previous_status,
            "new_status": "review" if write else previous_status,
            "executor_actor": MODULE_EXECUTOR_ACTOR,
            "module_id": MODULE_ID,
            "module_scope": MODULE_SCOPE,
            "execution_status": "module_execution_review_ready" if write else "preview_not_started",
            "validator_status": module_output["validator_status"],
            "module_output": module_output,
            "readback_verified": bool(write),
        },
        "readback_verified": bool(write),
        "derived_refresh": derived_refresh,
        "rollback_plan": "Transition the selected task from review back to running and remove workflow_module_execution_id / workflow_module_execution_status / workflow_module_execution_record after rollback authorization; no model/adapter/external work was started.",
        "boundary": "runtime_module_execution_done_before_done_model_or_adapter" if write else "runtime_module_execution_preview_before_review",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['runtime_packet_id']}.{result['claim_execution_id']}.{result['runtime_execution_id']}.{result['module_execution_id']}.runtime_module_execution_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-packet-id", default=DEFAULT_RUNTIME_PACKET_ID)
    parser.add_argument("--claim-execution-id", default=DEFAULT_CLAIM_EXECUTION_ID)
    parser.add_argument("--runtime-execution-id", default=DEFAULT_RUNTIME_EXECUTION_ID)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--validator", type=Path, default=VALIDATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    validator = load_validator(args.validator)
    result = execute_runtime_module(
        orchestrator,
        validator,
        runtime_packet_id=args.runtime_packet_id,
        claim_execution_id=args.claim_execution_id,
        runtime_execution_id=args.runtime_execution_id,
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
