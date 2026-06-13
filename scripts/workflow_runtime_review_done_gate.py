#!/usr/bin/env python3
"""Close one review-ready workflow-kernel runtime task as local done.

This consumes exactly one `review` task whose local deterministic runtime module
has already produced review-ready evidence. It writes a bounded done packet,
marks only that task `done`, verifies readback, and stops before model calls,
adapter writes, notifications, or external side effects.
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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-review-done"
DEFAULT_RUNTIME_PACKET_ID = "rti_2c322fc59264ae3d"
DEFAULT_CLAIM_EXECUTION_ID = "clex_22a18e493dc020c4"
DEFAULT_RUNTIME_EXECUTION_ID = "rtex_8d01cdb6170a549f"
DEFAULT_MODULE_EXECUTION_ID = "modex_5bca069e18d6d922"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_GROUP_ID = "contract_acceptance_governance"
DONE_ACTOR = "workflow_runtime_review_done_gate"
DONE_SCOPE = "local_review_done_closeout_no_model_no_adapter"


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
    return _load_module(path, "_workflow_runtime_review_done_orchestrator")


def load_validator(path: Path = VALIDATOR_PATH):
    return _load_module(path, "_workflow_runtime_review_done_validator")


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def find_review_task(
    orchestrator: Any,
    runtime_packet_id: str,
    claim_execution_id: str,
    runtime_execution_id: str,
    module_execution_id: str,
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
        if str(task.fm.get("workflow_module_execution_id") or "") != module_execution_id:
            continue
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group_id:
            continue
        matches.append(task)
    if not matches:
        raise SystemExit(
            "no review-ready runtime task found for "
            f"runtime_packet_id={runtime_packet_id} claim_execution_id={claim_execution_id} "
            f"runtime_execution_id={runtime_execution_id} module_execution_id={module_execution_id} group_id={group_id}"
        )
    if len(matches) > 1:
        raise SystemExit(f"multiple review-ready tasks found for module_execution_id={module_execution_id}; refusing ambiguous closeout")
    return matches[0]


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def build_done_execution_id(module_execution_id: str, group_id: str, task_id: str) -> str:
    return "done_" + _digest(
        {
            "module_execution_id": module_execution_id,
            "group_id": group_id,
            "task_id": task_id,
            "actor": DONE_ACTOR,
        }
    ).split(":", 1)[1][:16]


def run_closeout_verification(validator: Any) -> dict[str, Any]:
    result = validator.validate_registry(validator.DEFAULT_REGISTRY)
    payload = result.to_dict()
    return {
        "verification_status": payload.get("status"),
        "verification_summary": {
            "validated_workflows": payload.get("validated_workflows"),
            "validated_modules": payload.get("validated_modules"),
            "validated_route_aliases": payload.get("validated_route_aliases"),
            "errors": payload.get("errors", []),
            "warnings": payload.get("warnings", []),
        },
        "verification_digest": _digest(payload),
    }


def close_review_done(
    orchestrator: Any,
    validator: Any,
    *,
    runtime_packet_id: str = DEFAULT_RUNTIME_PACKET_ID,
    claim_execution_id: str = DEFAULT_CLAIM_EXECUTION_ID,
    runtime_execution_id: str = DEFAULT_RUNTIME_EXECUTION_ID,
    module_execution_id: str = DEFAULT_MODULE_EXECUTION_ID,
    group_id: str = DEFAULT_GROUP_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    write: bool = False,
) -> dict[str, Any]:
    task = find_review_task(orchestrator, runtime_packet_id, claim_execution_id, runtime_execution_id, module_execution_id, group_id)
    previous_status = _status(task)
    done_execution_id = build_done_execution_id(module_execution_id, group_id, task.id)

    if previous_status == "done" and str(task.fm.get("workflow_review_done_execution_id") or "") == done_execution_id:
        return {
            "done_execution_id": done_execution_id,
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
            "review_done_enabled": True,
            "model_call_enabled": False,
            "adapter_write_enabled": False,
            "external_side_effects_enabled": False,
            "review_done": {
                "task_id": task.id,
                "task_path": str(task.path),
                "group_id": group_id,
                "previous_status": "review",
                "new_status": "done",
                "executor_actor": DONE_ACTOR,
                "done_scope": DONE_SCOPE,
                "done_status": "done_local_review_closed",
                "verification_status": "PASS",
                "readback_verified": True,
            },
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "Transition the selected task from done back to review and remove workflow_review_done_execution_id / workflow_review_done_status / workflow_review_done_record after rollback authorization; no model/adapter/external work was started.",
            "boundary": "runtime_review_done_closed_before_model_or_adapter",
        }

    if previous_status != "review":
        raise SystemExit(f"review done gate expects review task, got {task.id}:{previous_status}")
    if str(task.fm.get("workflow_module_execution_status") or "") != "module_execution_review_ready":
        raise SystemExit(f"review done gate expects module_execution_review_ready for {task.id}")
    if _fm_bool(task.fm.get("requires_human_gate")):
        raise SystemExit(f"review done gate refuses human-gated group without separate explicit group override: {group_id}")
    if _fm_bool(task.fm.get("model_call_enabled")) or _fm_bool(task.fm.get("adapter_write_enabled")) or _fm_bool(task.fm.get("external_side_effects_enabled")):
        raise SystemExit(f"review done gate refuses task with model/adapter/external flags already enabled: {task.id}")

    verification = run_closeout_verification(validator)
    if verification["verification_status"] != "PASS":
        raise SystemExit("closeout verification failed; refusing done transition")

    done_record = {
        "done_execution_id": done_execution_id,
        "module_execution_id": module_execution_id,
        "runtime_execution_id": runtime_execution_id,
        "claim_execution_id": claim_execution_id,
        "runtime_packet_id": runtime_packet_id,
        "task_id": task.id,
        "group_id": group_id,
        "executor_actor": DONE_ACTOR,
        "done_scope": DONE_SCOPE,
        "done_status": "done_local_review_closed",
        "created_at": _now_utc(),
        "verification": verification,
        "forbidden_next_without_authorization": [
            "model_call",
            "adapter_write",
            "external_side_effect",
            "external_notification",
        ],
    }

    if write:
        try:
            task.status = "done"
        except AttributeError:
            pass
        task.fm["status"] = "done"
        task.fm["workflow_review_done_execution_id"] = done_execution_id
        task.fm["workflow_review_done_status"] = "done_local_review_closed"
        task.fm["workflow_review_done_record"] = done_record
        task.fm["review_done_enabled"] = True
        task.fm["model_call_enabled"] = False
        task.fm["adapter_write_enabled"] = False
        task.fm["external_side_effects_enabled"] = False
        evidence = list(task.fm.get("evidence") or [])
        for marker in [
            f"artifact:runtime_review_done_packet:{done_execution_id}",
            "command:workflow_contract_validator PASS",
            "proof:runtime_review_done_gate_local_closeout_no_model_no_adapter",
            "classification:workflow_kernel_runtime_review_done_closed",
        ]:
            if marker not in evidence:
                evidence.append(marker)
        task.fm["evidence"] = evidence
        orchestrator.save_task(task)
        readback = _readback_task(orchestrator, task.id)
        if readback is None:
            raise SystemExit(f"done task was not readable: {task.id}")
        if _status(readback) != "done":
            raise SystemExit(f"review done readback status mismatch for {task.id}: {_status(readback)}")
        if str(readback.fm.get("workflow_review_done_execution_id") or "") != done_execution_id:
            raise SystemExit(f"review done id readback mismatch for {task.id}")
        if str(readback.fm.get("workflow_review_done_status") or "") != "done_local_review_closed":
            raise SystemExit(f"review done status readback mismatch for {task.id}")
        task = readback
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_review_done_gate")
    else:
        derived_refresh = None

    return {
        "done_execution_id": done_execution_id,
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
        "review_done_enabled": True,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "review_done": {
            "task_id": task.id,
            "task_path": str(task.path),
            "group_id": group_id,
            "previous_status": previous_status,
            "new_status": "done" if write else previous_status,
            "executor_actor": DONE_ACTOR,
            "done_scope": DONE_SCOPE,
            "done_status": "done_local_review_closed" if write else "preview_not_closed",
            "verification_status": verification["verification_status"],
            "verification": verification,
            "readback_verified": bool(write),
        },
        "readback_verified": bool(write),
        "derived_refresh": derived_refresh,
        "rollback_plan": "Transition the selected task from done back to review and remove workflow_review_done_execution_id / workflow_review_done_status / workflow_review_done_record after rollback authorization; no model/adapter/external work was started.",
        "boundary": "runtime_review_done_closed_before_model_or_adapter" if write else "runtime_review_done_preview_before_done",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['runtime_packet_id']}.{result['claim_execution_id']}.{result['runtime_execution_id']}.{result['module_execution_id']}.{result['done_execution_id']}.runtime_review_done_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-packet-id", default=DEFAULT_RUNTIME_PACKET_ID)
    parser.add_argument("--claim-execution-id", default=DEFAULT_CLAIM_EXECUTION_ID)
    parser.add_argument("--runtime-execution-id", default=DEFAULT_RUNTIME_EXECUTION_ID)
    parser.add_argument("--module-execution-id", default=DEFAULT_MODULE_EXECUTION_ID)
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
    result = close_review_done(
        orchestrator,
        validator,
        runtime_packet_id=args.runtime_packet_id,
        claim_execution_id=args.claim_execution_id,
        runtime_execution_id=args.runtime_execution_id,
        module_execution_id=args.module_execution_id,
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
