#!/usr/bin/env python3
"""Invoke one pinned read-only model route for a completed workflow-kernel task.

This is the first model-call boundary after local deterministic runtime closeout.
It persists a route trace, prompt artifact, response artifact, and packet. It may
annotate the selected local task with model-call evidence, but it never writes
adapters, sends notifications, or mutates external systems.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_HOME = Path.home() / ".hermes"
ORCHESTRATOR_PATH = HERMES_HOME / "scripts" / "brain_task_orchestrator.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-runtime-model-calls"
DEFAULT_RUNTIME_PACKET_ID = "rti_2c322fc59264ae3d"
DEFAULT_CLAIM_EXECUTION_ID = "clex_22a18e493dc020c4"
DEFAULT_RUNTIME_EXECUTION_ID = "rtex_8d01cdb6170a549f"
DEFAULT_MODULE_EXECUTION_ID = "modex_5bca069e18d6d922"
DEFAULT_DONE_EXECUTION_ID = "done_928841ef9a25840f"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_GROUP_ID = "contract_acceptance_governance"
DEFAULT_MODULE_ID = "goal_calibrator.v1"
DEFAULT_ROUTE_ALIAS = "best.goal_calibration.structured"
DEFAULT_PROVIDER = "openai-codex"
DEFAULT_MODEL = "gpt-5.5"
EXPECTED_MARKER = "MODEL_CALL_GATE_OK"
MODEL_ACTOR = "workflow_runtime_model_call_gate"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    return _load_module(path, "_workflow_runtime_model_call_orchestrator")


def _status(task: Any) -> str:
    return str(getattr(task, "status", "") or task.fm.get("status") or "")


def _readback_task(orchestrator: Any, task_id: str):
    for task in orchestrator.iter_tasks():
        if task.id == task_id:
            return task
    return None


def find_done_task(
    orchestrator: Any,
    runtime_packet_id: str,
    claim_execution_id: str,
    runtime_execution_id: str,
    module_execution_id: str,
    done_execution_id: str,
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
        if str(task.fm.get("workflow_consolidation_group_id") or "") != group_id:
            continue
        matches.append(task)
    if not matches:
        raise SystemExit("no done task found for the selected runtime/model-call chain")
    if len(matches) > 1:
        raise SystemExit(f"multiple done tasks found for group_id={group_id}; refusing ambiguous model call")
    return matches[0]


def build_model_call_id(done_execution_id: str, module_id: str, route_alias: str, provider: str, model: str) -> str:
    return "mcall_" + _digest(
        {
            "done_execution_id": done_execution_id,
            "module_id": module_id,
            "route_alias": route_alias,
            "provider": provider,
            "model": model,
            "actor": MODEL_ACTOR,
        }
    ).split(":", 1)[1][:16]


def build_prompt(*, workflow_id: str, group_id: str, module_id: str, route_alias: str, task_id: str) -> str:
    return "\n".join(
        [
            "You are executing a Hermes workflow runtime Model Call Gate smoke module.",
            "This is a read-only verification call. Do not ask to write files, send messages, call tools, or mutate systems.",
            f"workflow_id={workflow_id}",
            f"group_id={group_id}",
            f"module_id={module_id}",
            f"route_alias={route_alias}",
            f"task_id={task_id}",
            "Return exactly this marker and nothing else:",
            EXPECTED_MARKER,
        ]
    )


def default_model_runner(prompt: str, *, provider: str, model: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "hermes",
            "chat",
            "--provider",
            provider,
            "-m",
            model,
            "-q",
            prompt,
            "--quiet",
            "--ignore-rules",
            "--max-turns",
            "1",
            "--source",
            "tool",
        ],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _artifact(path: Path, content: str) -> dict[str, Any]:
    data = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "sha256": _sha256_bytes(data), "bytes": len(data)}


def execute_model_call_gate(
    orchestrator: Any,
    *,
    runtime_packet_id: str = DEFAULT_RUNTIME_PACKET_ID,
    claim_execution_id: str = DEFAULT_CLAIM_EXECUTION_ID,
    runtime_execution_id: str = DEFAULT_RUNTIME_EXECUTION_ID,
    module_execution_id: str = DEFAULT_MODULE_EXECUTION_ID,
    done_execution_id: str = DEFAULT_DONE_EXECUTION_ID,
    group_id: str = DEFAULT_GROUP_ID,
    workflow_id: str = DEFAULT_WORKFLOW_ID,
    run_id: str = DEFAULT_RUN_ID,
    module_id: str = DEFAULT_MODULE_ID,
    route_alias: str = DEFAULT_ROUTE_ALIAS,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    timeout: int = 180,
    write: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = default_model_runner,
) -> dict[str, Any]:
    task = find_done_task(
        orchestrator,
        runtime_packet_id,
        claim_execution_id,
        runtime_execution_id,
        module_execution_id,
        done_execution_id,
        group_id,
    )
    if _status(task) != "done":
        raise SystemExit(f"model call gate expects done task, got {task.id}:{_status(task)}")
    if str(task.fm.get("workflow_review_done_status") or "") != "done_local_review_closed":
        raise SystemExit(f"model call gate expects done_local_review_closed for {task.id}")
    if _fm_bool(task.fm.get("requires_human_gate")):
        raise SystemExit(f"model call gate refuses human-gated group without separate explicit override: {group_id}")
    if _fm_bool(task.fm.get("adapter_write_enabled")) or _fm_bool(task.fm.get("external_side_effects_enabled")):
        raise SystemExit(f"model call gate refuses task with adapter/external flags enabled: {task.id}")

    model_call_id = build_model_call_id(done_execution_id, module_id, route_alias, provider, model)
    if str(task.fm.get("workflow_model_call_id") or "") == model_call_id and str(task.fm.get("workflow_model_call_status") or "") == "model_call_completed_read_only":
        existing = _fm_object(task.fm.get("workflow_model_call_record"))
        return {
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
            "module_id": module_id,
            "route_trace": existing.get("route_trace") or {
                "route_alias": route_alias,
                "provider": provider,
                "model": model,
                "run_mode": "pinned_read_only",
                "fallback_chain": [],
                "routing_policy_version": "phase13.model_call_gate.v1",
                "dynamic_routing_enabled": False,
                "adapter_write_enabled": False,
                "external_side_effects_enabled": False,
            },
            "prompt_artifact": existing.get("prompt_artifact"),
            "response_artifact": existing.get("response_artifact"),
            "verification": {"status": "PASS", "checks": ["idempotent_readback"]},
            "taskpool_write_enabled": bool(write),
            "model_call_enabled": True,
            "adapter_write_enabled": False,
            "external_side_effects_enabled": False,
            "readback_verified": True,
            "derived_refresh": None,
            "rollback_plan": "Remove workflow_model_call_id / workflow_model_call_status / workflow_model_call_record and evidence markers after rollback authorization; no adapter/external write was performed.",
            "boundary": "runtime_model_call_completed_before_adapter_or_external_write",
        }

    prompt = build_prompt(workflow_id=workflow_id, group_id=group_id, module_id=module_id, route_alias=route_alias, task_id=task.id)
    prompt_path = output_dir / f"{workflow_id}.{run_id}.{model_call_id}.prompt.txt"
    response_path = output_dir / f"{workflow_id}.{run_id}.{model_call_id}.response.txt"
    prompt_artifact = _artifact(prompt_path, prompt)

    completed = runner(prompt, provider=provider, model=model, timeout=timeout)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        failure_path = output_dir / f"{workflow_id}.{run_id}.{model_call_id}.failure.json"
        failure_payload = {
            "model_call_id": model_call_id,
            "provider": provider,
            "model": model,
            "returncode": completed.returncode,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-2000:],
            "generated_at": _now_utc(),
        }
        _artifact(failure_path, json.dumps(failure_payload, indent=2, ensure_ascii=False) + "\n")
        raise SystemExit(f"model call failed with returncode={completed.returncode}; failure artifact={failure_path}")

    response_artifact = _artifact(response_path, stdout)
    response_artifact["non_empty"] = bool(stdout.strip())
    response_artifact["expected_marker_present"] = EXPECTED_MARKER in stdout
    if not response_artifact["non_empty"]:
        raise SystemExit("model call returned empty response")
    if not response_artifact["expected_marker_present"]:
        raise SystemExit(f"model call response missing expected marker {EXPECTED_MARKER}; response artifact={response_path}")

    route_trace = {
        "route_alias": route_alias,
        "provider": provider,
        "model": model,
        "run_mode": "pinned_read_only",
        "fallback_chain": [],
        "routing_policy_version": "phase13.model_call_gate.v1",
        "dynamic_routing_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
    }
    verification = {
        "status": "PASS",
        "checks": [
            "model_process_exit_zero",
            "response_non_empty",
            "expected_marker_present",
            "prompt_artifact_persisted",
            "response_artifact_persisted",
            "adapter_write_disabled",
            "external_side_effects_disabled",
        ],
    }
    model_record = {
        "model_call_id": model_call_id,
        "done_execution_id": done_execution_id,
        "task_id": task.id,
        "group_id": group_id,
        "module_id": module_id,
        "route_trace": route_trace,
        "prompt_artifact": prompt_artifact,
        "response_artifact": response_artifact,
        "verification": verification,
        "created_at": _now_utc(),
        "forbidden_next_without_authorization": [
            "adapter_write",
            "external_side_effect",
            "external_notification",
        ],
    }

    derived_refresh = None
    readback_verified = False
    if write:
        task.fm["workflow_model_call_id"] = model_call_id
        task.fm["workflow_model_call_status"] = "model_call_completed_read_only"
        task.fm["workflow_model_call_record"] = model_record
        task.fm["model_call_enabled"] = True
        task.fm["adapter_write_enabled"] = False
        task.fm["external_side_effects_enabled"] = False
        evidence = list(task.fm.get("evidence") or [])
        for marker in [
            f"artifact:runtime_model_call_packet:{model_call_id}",
            f"artifact:model_call_response:{response_artifact['sha256']}",
            "proof:runtime_model_call_gate_read_only_no_adapter_no_external",
            "classification:workflow_kernel_runtime_model_call_completed",
        ]:
            if marker not in evidence:
                evidence.append(marker)
        task.fm["evidence"] = evidence
        orchestrator.save_task(task)
        readback = _readback_task(orchestrator, task.id)
        if readback is None:
            raise SystemExit(f"model-call task was not readable: {task.id}")
        if str(readback.fm.get("workflow_model_call_id") or "") != model_call_id:
            raise SystemExit(f"model call id readback mismatch for {task.id}")
        if str(readback.fm.get("workflow_model_call_status") or "") != "model_call_completed_read_only":
            raise SystemExit(f"model call status readback mismatch for {task.id}")
        if _fm_bool(readback.fm.get("adapter_write_enabled")) or _fm_bool(readback.fm.get("external_side_effects_enabled")):
            raise SystemExit(f"adapter/external flags changed unexpectedly for {task.id}")
        readback_verified = True
        derived_refresh = orchestrator.refresh_derived_task_views("workflow_runtime_model_call_gate")

    result = {
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
        "module_id": module_id,
        "route_trace": route_trace,
        "prompt_artifact": prompt_artifact,
        "response_artifact": response_artifact,
        "verification": verification,
        "taskpool_write_enabled": bool(write),
        "model_call_enabled": True,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "readback_verified": readback_verified,
        "derived_refresh": derived_refresh,
        "rollback_plan": "Remove workflow_model_call_id / workflow_model_call_status / workflow_model_call_record and evidence markers after rollback authorization; no adapter/external write was performed.",
        "boundary": "runtime_model_call_completed_before_adapter_or_external_write" if write else "runtime_model_call_completed_preview_no_taskpool_write",
    }
    return result


def write_packet(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['model_call_id']}.runtime_model_call_packet.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-packet-id", default=DEFAULT_RUNTIME_PACKET_ID)
    parser.add_argument("--claim-execution-id", default=DEFAULT_CLAIM_EXECUTION_ID)
    parser.add_argument("--runtime-execution-id", default=DEFAULT_RUNTIME_EXECUTION_ID)
    parser.add_argument("--module-execution-id", default=DEFAULT_MODULE_EXECUTION_ID)
    parser.add_argument("--done-execution-id", default=DEFAULT_DONE_EXECUTION_ID)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--module-id", default=DEFAULT_MODULE_ID)
    parser.add_argument("--route-alias", default=DEFAULT_ROUTE_ALIAS)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--orchestrator", type=Path, default=ORCHESTRATOR_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    orchestrator = load_orchestrator(args.orchestrator)
    result = execute_model_call_gate(
        orchestrator,
        runtime_packet_id=args.runtime_packet_id,
        claim_execution_id=args.claim_execution_id,
        runtime_execution_id=args.runtime_execution_id,
        module_execution_id=args.module_execution_id,
        done_execution_id=args.done_execution_id,
        group_id=args.group_id,
        workflow_id=args.workflow_id,
        run_id=args.run_id,
        module_id=args.module_id,
        route_alias=args.route_alias,
        provider=args.provider,
        model=args.model,
        output_dir=args.output_dir,
        timeout=args.timeout,
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
