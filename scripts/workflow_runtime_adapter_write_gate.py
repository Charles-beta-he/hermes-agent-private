#!/usr/bin/env python3
"""Write one digest-bound local adapter outbox packet for a workflow model call.

This opens only the local adapter write-through simulation boundary. It turns a
completed read-only model call into a durable taskpool adapter intent and local
outbox artifact, annotates the selected task, verifies readback, and stops before
external side effects, notifications, or third-party adapter mutation.
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
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-adapter-writes"
DEFAULT_RUNTIME_PACKET_ID = "rti_2c322fc59264ae3d"
DEFAULT_CLAIM_EXECUTION_ID = "clex_22a18e493dc020c4"
DEFAULT_RUNTIME_EXECUTION_ID = "rtex_8d01cdb6170a549f"
DEFAULT_MODULE_EXECUTION_ID = "modex_5bca069e18d6d922"
DEFAULT_DONE_EXECUTION_ID = "done_928841ef9a25840f"
DEFAULT_MODEL_CALL_ID = "mcall_e01f7305ba2efb07"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_GROUP_ID = "contract_acceptance_governance"
ADAPTER_ACTOR = "workflow_runtime_adapter_write_gate"


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _fm_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _fm_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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
    return _load_module(path, "_workflow_runtime_adapter_write_orchestrator")


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def find_model_call_task(
    orchestrator: Any,
    runtime_packet_id: str,
    claim_execution_id: str,
    runtime_execution_id: str,
    module_execution_id: str,
    done_execution_id: str,
    model_call_id: str,
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
        if str(task.fm.get("workflow_review_done_execution_id") or "") != done_execution_id:
            continue
        if str(task.fm.get("workflow_model_call_id") or "") != model_call_id:
            continue
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group_id:
            continue
        matches.append(task)
    if not matches:
        raise SystemExit("no model-call-completed task found for selected runtime adapter chain")
    if len(matches) > 1:
        raise SystemExit(f"multiple model-call-completed tasks found for group_id={group_id}; refusing ambiguous adapter write")
    return matches[0]


def build_adapter_write_id(model_call_id: str, group_id: str, task_id: str) -> str:
    return "adw_" + _digest({"model_call_id": model_call_id, "group_id": group_id, "task_id": task_id, "actor": ADAPTER_ACTOR}).split(":", 1)[1][:16]


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(data)
    readback = path.read_bytes()
    return {"path": str(path), "sha256": _sha256_bytes(data), "bytes": len(data), "readback_digest_match": _sha256_bytes(readback) == _sha256_bytes(data)}


def execute_adapter_write_gate(
    orchestrator: Any,
    *,
    runtime_packet_id: str = DEFAULT_RUNTIME_PACKET_ID,
    claim_execution_id: str = DEFAULT_CLAIM_EXECUTION_ID,
    runtime_execution_id: str = DEFAULT_RUNTIME_EXECUTION_ID,
    module_execution_id: str = DEFAULT_MODULE_EXECUTION_ID,
    done_execution_id: str = DEFAULT_DONE_EXECUTION_ID,
    model_call_id: str = DEFAULT_MODEL_CALL_ID,
    group_id: str = DEFAULT_GROUP_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    write: bool = False,
) -> dict[str, Any]:
    task = find_model_call_task(orchestrator, runtime_packet_id, claim_execution_id, runtime_execution_id, module_execution_id, done_execution_id, model_call_id, group_id)
    if _status(task) != "done":
        raise SystemExit(f"adapter write gate expects done task, got {task.id}:{_status(task)}")
    if str(task.fm.get("workflow_model_call_status") or "") != "model_call_completed_read_only":
        raise SystemExit(f"adapter write gate expects model_call_completed_read_only for {task.id}")
    if _fm_bool(task.fm.get("requires_human_gate")):
        raise SystemExit(f"adapter write gate refuses human-gated group without separate explicit override: {group_id}")
    if _fm_bool(task.fm.get("external_side_effects_enabled")):
        raise SystemExit(f"adapter write gate refuses task with external side effects already enabled: {task.id}")

    adapter_write_id = build_adapter_write_id(model_call_id, group_id, task.id)
    if str(task.fm.get("workflow_adapter_write_id") or "") == adapter_write_id and str(task.fm.get("workflow_adapter_write_status") or "") == "adapter_write_local_outbox_simulated":
        existing = _fm_object(task.fm.get("workflow_adapter_write_record"))
        return {
            "adapter_write_id": adapter_write_id,
            "model_call_id": model_call_id,
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
            "adapter_target": "taskpool_outbox",
            "adapter_mode": "local_outbox_simulation",
            "write_intent": existing.get("write_intent", {}),
            "intent_digest": existing.get("intent_digest", _digest({})),
            "outbox_artifact": existing.get("outbox_artifact"),
            "verification": {"status": "PASS", "checks": ["idempotent_readback"]},
            "taskpool_write_enabled": bool(write),
            "adapter_write_enabled": True,
            "external_side_effects_enabled": False,
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "Remove workflow_adapter_write_id / workflow_adapter_write_status / workflow_adapter_write_record and evidence markers after rollback authorization; delete local outbox artifact if desired; no external system was mutated.",
            "boundary": "runtime_adapter_write_local_outbox_simulated_before_external_side_effect",
        }

    model_record = _fm_object(task.fm.get("workflow_model_call_record"))
    write_intent = {
        "target": "taskpool",
        "operation": "annotate_model_call_ready_for_adapter_review",
        "task_id": task.id,
        "source_model_call_id": model_call_id,
        "fields": {
            "workflow_adapter_candidate": "model_call_ready_for_adapter_review",
            "workflow_model_call_status": str(task.fm.get("workflow_model_call_status") or ""),
            "response_artifact_sha256": ((model_record.get("response_artifact") or {}).get("sha256") if isinstance(model_record, dict) else None),
        },
        "created_by": ADAPTER_ACTOR,
        "external_side_effects_enabled": False,
    }
    intent_digest = _digest(write_intent)
    outbox_payload = {
        "adapter_write_id": adapter_write_id,
        "adapter_target": "taskpool_outbox",
        "adapter_mode": "local_outbox_simulation",
        "write_intent": write_intent,
        "intent_digest": intent_digest,
        "created_at": _now_utc(),
        "forbidden_without_next_authorization": ["external_adapter_apply", "external_notification", "third_party_mutation"],
    }
    outbox_path = output_dir / f"{workflow_id}.{run_id}.{adapter_write_id}.taskpool_outbox.json"
    outbox_artifact = _write_json_artifact(outbox_path, outbox_payload)
    if not outbox_artifact["readback_digest_match"]:
        raise SystemExit(f"outbox artifact digest mismatch: {outbox_path}")

    verification = {
        "status": "PASS",
        "checks": [
            "model_call_completed_read_only",
            "intent_digest_computed",
            "outbox_artifact_persisted",
            "outbox_artifact_digest_readback_match",
            "external_side_effects_disabled",
        ],
    }
    adapter_record = {
        "adapter_write_id": adapter_write_id,
        "model_call_id": model_call_id,
        "task_id": task.id,
        "group_id": group_id,
        "adapter_target": "taskpool_outbox",
        "adapter_mode": "local_outbox_simulation",
        "write_intent": write_intent,
        "intent_digest": intent_digest,
        "outbox_artifact": outbox_artifact,
        "verification": verification,
        "created_at": _now_utc(),
        "forbidden_next_without_authorization": ["external_adapter_apply", "external_notification", "third_party_mutation"],
    }

    readback_verified = False
    derived_refresh = None
    if write:
        task.fm["workflow_adapter_write_id"] = adapter_write_id
        task.fm["workflow_adapter_write_status"] = "adapter_write_local_outbox_simulated"
        task.fm["workflow_adapter_write_record"] = adapter_record
        task.fm["adapter_write_enabled"] = True
        task.fm["external_side_effects_enabled"] = False
        evidence = list(task.fm.get("evidence") or [])
        for marker in [
            f"artifact:runtime_adapter_write_packet:{adapter_write_id}",
            f"artifact:adapter_write_outbox:{outbox_artifact['sha256']}",
            "proof:runtime_adapter_write_local_outbox_no_external_side_effect",
            "classification:workflow_kernel_runtime_adapter_write_simulated",
        ]:
            if marker not in evidence:
                evidence.append(marker)
        task.fm["evidence"] = evidence
        orchestrator.save_task(task)
        readback = _readback_task(orchestrator, task.id)
        if readback is None:
            raise SystemExit(f"adapter-write task was not readable: {task.id}")
        if str(readback.fm.get("workflow_adapter_write_id") or "") != adapter_write_id:
            raise SystemExit(f"adapter write id readback mismatch for {task.id}")
        if str(readback.fm.get("workflow_adapter_write_status") or "") != "adapter_write_local_outbox_simulated":
            raise SystemExit(f"adapter write status readback mismatch for {task.id}")
        if _fm_bool(readback.fm.get("external_side_effects_enabled")):
            raise SystemExit(f"external side effect flag changed unexpectedly for {task.id}")
        readback_verified = True
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_adapter_write_gate")

    return {
        "adapter_write_id": adapter_write_id,
        "model_call_id": model_call_id,
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
        "adapter_target": "taskpool_outbox",
        "adapter_mode": "local_outbox_simulation",
        "write_intent": write_intent,
        "intent_digest": intent_digest,
        "outbox_artifact": outbox_artifact,
        "verification": verification,
        "taskpool_write_enabled": bool(write),
        "adapter_write_enabled": True,
        "external_side_effects_enabled": False,
        "readback_verified": readback_verified,
        "derived_refresh": derived_refresh,
        "rollback_plan": "Remove workflow_adapter_write_id / workflow_adapter_write_status / workflow_adapter_write_record and evidence markers after rollback authorization; delete local outbox artifact if desired; no external system was mutated.",
        "boundary": "runtime_adapter_write_local_outbox_simulated_before_external_side_effect" if write else "runtime_adapter_write_preview_local_outbox_no_taskpool_write",
    }


def write_packet(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['adapter_write_id']}.runtime_adapter_write_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-packet-id", default=DEFAULT_RUNTIME_PACKET_ID)
    parser.add_argument("--claim-execution-id", default=DEFAULT_CLAIM_EXECUTION_ID)
    parser.add_argument("--runtime-execution-id", default=DEFAULT_RUNTIME_EXECUTION_ID)
    parser.add_argument("--module-execution-id", default=DEFAULT_MODULE_EXECUTION_ID)
    parser.add_argument("--done-execution-id", default=DEFAULT_DONE_EXECUTION_ID)
    parser.add_argument("--model-call-id", default=DEFAULT_MODEL_CALL_ID)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    result = execute_adapter_write_gate(
        orchestrator,
        runtime_packet_id=args.runtime_packet_id,
        claim_execution_id=args.claim_execution_id,
        runtime_execution_id=args.runtime_execution_id,
        module_execution_id=args.module_execution_id,
        done_execution_id=args.done_execution_id,
        model_call_id=args.model_call_id,
        group_id=args.group_id,
        workflow_id=args.workflow_id,
        run_id=args.run_id,
        output_dir=args.output_dir,
        write=args.write,
    )
    if args.write:
        result["written"] = str(write_packet(args.output_dir, result))
    if args.as_json or not args.write:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
