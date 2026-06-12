#!/usr/bin/env python3
"""Record runtime integration packets for consolidated workflow-kernel tasks.

This opens the runtime integration layer in record-only mode. It annotates
consolidated proposed task notes with runtime packet metadata and prepared claim
packets, then stops before any taskpool claim, running/review/done transition,
model call, adapter write-through, or external side effect.
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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-integration"
DEFAULT_CONSOLIDATION_ID = "tpcon_4421e19ba058d222"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_orchestrator(path: Path = ORCHESTRATOR_PATH):
    if not path.exists():
        raise SystemExit(f"brain_task_orchestrator.py not found: {path}")
    spec = importlib.util.spec_from_file_location("_workflow_runtime_integration_orchestrator", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot import orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def collect_consolidated_tasks(orchestrator: Any, consolidation_id: str) -> list[Any]:
    rows = []
    for task in orchestrator.iter_tasks():
        if str(task.fm.get("workflow_consolidation_id") or "") == consolidation_id:
            rows.append(task)
    rows.sort(key=lambda task: str(task.fm.get("workflow_consolidation_group_id") or task.id))
    if not rows:
        raise SystemExit(f"no consolidated tasks found for workflow_consolidation_id={consolidation_id}")
    return rows


def build_runtime_packet_id(consolidation_id: str, tasks: list[Any]) -> str:
    seed = {
        "consolidation_id": consolidation_id,
        "task_ids": [task.id for task in tasks],
        "group_ids": [str(task.fm.get("workflow_consolidation_group_id") or "") for task in tasks],
    }
    return "rti_" + _digest(seed).split(":", 1)[1][:16]


def build_claim_packet(runtime_packet_id: str, task: Any) -> dict[str, Any]:
    group_id = str(task.fm.get("workflow_consolidation_group_id") or task.id)
    return {
        "runtime_packet_id": runtime_packet_id,
        "task_id": task.id,
        "group_id": group_id,
        "claim_packet_status": "prepared_not_claimed",
        "allowed_next_status": "pending_after_claim_authorization",
        "forbidden_without_authorization": [
            "claim",
            "running",
            "review",
            "done",
            "model_call",
            "adapter_write",
            "external_side_effect",
        ],
        "required_before_claim": [
            "runtime_claim_authorization",
            "selected_group_confirmation",
            "rollback_plan_confirmation",
            "post_claim_readback_plan",
        ],
    }


def record_runtime_integration(
    orchestrator: Any,
    *,
    consolidation_id: str = DEFAULT_CONSOLIDATION_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    write: bool = False,
) -> dict[str, Any]:
    tasks = collect_consolidated_tasks(orchestrator, consolidation_id)
    runtime_packet_id = build_runtime_packet_id(consolidation_id, tasks)
    targets = []
    for task in tasks:
        if _status(task) != "proposed":
            raise SystemExit(f"runtime integration expects proposed consolidated task, got {task.id}:{_status(task)}")
        group_id = str(task.fm.get("workflow_consolidation_group_id") or "")
        claim_packet = build_claim_packet(runtime_packet_id, task)
        if write:
            task.fm["workflow_runtime_packet_id"] = runtime_packet_id
            task.fm["workflow_runtime_state"] = "recorded_waiting_for_claim_authorization"
            task.fm["workflow_runtime_record_only"] = True
            task.fm["workflow_claim_packet_status"] = "prepared_not_claimed"
            task.fm["workflow_claim_packet"] = claim_packet
            task.fm["taskpool_claim_enabled"] = False
            task.fm["runtime_enabled"] = True
            task.fm["runtime_record_only"] = True
            task.fm["model_call_enabled"] = False
            task.fm["adapter_write_enabled"] = False
            task.fm["external_side_effects_enabled"] = False
            evidence = list(task.fm.get("evidence") or [])
            marker = f"artifact:runtime_integration_packet:{runtime_packet_id}"
            if marker not in evidence:
                evidence.append(marker)
            proof = "proof:runtime_integration_record_only_no_claim"
            if proof not in evidence:
                evidence.append(proof)
            task.fm["evidence"] = evidence
            orchestrator.save_task(task)
            readback = next((t for t in orchestrator.iter_tasks() if t.id == task.id), None)
            if readback is None:
                raise SystemExit(f"runtime-integrated task was not readable: {task.id}")
            if str(readback.fm.get("workflow_runtime_packet_id") or "") != runtime_packet_id:
                raise SystemExit(f"runtime packet readback mismatch for {task.id}")
            task = readback
        targets.append(
            {
                "task_id": task.id,
                "task_path": str(task.path),
                "group_id": group_id,
                "runtime_state": "recorded_waiting_for_claim_authorization",
                "claim_packet_status": "prepared_not_claimed",
                "readback_verified": True if write else False,
            }
        )
    if write:
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_integration_record_only")
    else:
        derived_refresh = None
    return {
        "runtime_packet_id": runtime_packet_id,
        "consolidation_id": consolidation_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "generated_at": _now_utc(),
        "taskpool_write_enabled": bool(write),
        "taskpool_claim_enabled": False,
        "runtime_enabled": True,
        "runtime_record_only": True,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "runtime_targets": targets,
        "readback_verified": bool(write),
        "derived_refresh": derived_refresh,
        "rollback_plan": "Remove workflow_runtime_packet_id / workflow_runtime_state / claim packet annotations from the listed proposed tasks after separate rollback authorization; no claim/runtime/model/adapter/external state was executed.",
        "boundary": "runtime_integration_record_only_done_before_claim_or_adapter",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['runtime_packet_id']}.runtime_integration_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consolidation-id", default=DEFAULT_CONSOLIDATION_ID)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    result = record_runtime_integration(
        orchestrator,
        consolidation_id=args.consolidation_id,
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
