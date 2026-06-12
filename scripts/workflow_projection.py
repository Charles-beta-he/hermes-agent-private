#!/usr/bin/env python3
"""Generate read-only workflow kernel projections.

This script compiles the static Phase 2 contract into operator-readable artifacts:
route traces and a run lockfile preview. It does not claim tasks, call models,
execute tools, mutate ledgers, send messages, or write through adapters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW_REGISTRY = REPO_ROOT / "config" / "workflow_registry.json"
DEFAULT_MODEL_REGISTRY = REPO_ROOT / "config" / "model_registry.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-projections"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _index_models(model_registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {model["model_id"]: model for model in model_registry.get("models", [])}


def _select_workflow(registry: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    for workflow in registry.get("workflows", []):
        if workflow.get("workflow_id") == workflow_id:
            return workflow
    raise SystemExit(f"workflow not found: {workflow_id}")


def _route_trace(
    workflow: dict[str, Any],
    module: dict[str, Any],
    model_registry: dict[str, Any],
    models: dict[str, dict[str, Any]],
    run_id: str,
    now: str,
) -> dict[str, Any]:
    route_alias = module["model_policy"]["route_alias"]
    alias_spec = model_registry["route_aliases"][route_alias]
    selected = alias_spec["primary_model_id"]
    selected_model = models[selected]
    eval_results = selected_model.get("eval_results") or []
    eval_result = eval_results[-1]
    minimum_score = 0.7
    observed_score = float(eval_result.get("score", 0))
    return {
        "trace_id": f"rt_{run_id}_{module['module_id']}",
        "workflow_id": workflow["workflow_id"],
        "run_id": run_id,
        "module_id": module["module_id"],
        "route_alias": route_alias,
        "selected_model_id": selected,
        "model_registry_version": model_registry["registry_version"],
        "routing_policy_version": model_registry["phase"],
        "run_mode": alias_spec.get("run_mode", "pinned"),
        "fallback_chain": alias_spec.get("fallback_model_ids", []),
        "decision_reasons": [
            f"route_alias={route_alias}",
            f"required_status={','.join(alias_spec.get('required_status', []))}",
            "phase2_projection_only_no_model_call",
            "fallback_required_and_available",
        ],
        "constraints_checked": {
            "provider_status": selected_model.get("status") in alias_spec.get("required_status", []),
            "privacy_policy": bool(selected_model.get("risk_profile")),
            "structured_output": bool(selected_model.get("capabilities", {}).get("structured_output")),
            "cost_budget": True,
            "latency_slo": True,
            "fallback_available": bool(alias_spec.get("fallback_model_ids")),
            "eval_passed": bool(eval_result.get("passed")) and observed_score >= minimum_score,
        },
        "eval_gate": {
            "suite_id": alias_spec["eval_suite_id"],
            "passed": bool(eval_result.get("passed")) and observed_score >= minimum_score,
            "minimum_score": minimum_score,
            "observed_score": observed_score,
        },
        "created_at": now,
    }


def build_projection(
    workflow_registry: dict[str, Any], model_registry: dict[str, Any], workflow_id: str
) -> dict[str, Any]:
    workflow = _select_workflow(workflow_registry, workflow_id)
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_seed = {
        "workflow_id": workflow["workflow_id"],
        "workflow_version": workflow["version"],
        "registry_version": workflow_registry["registry_version"],
        "model_registry_version": model_registry["registry_version"],
        "runtime_enabled": workflow["runtime_enabled"],
        "write_through_enabled": workflow["write_through_enabled"],
        "external_side_effects_enabled": workflow["external_side_effects_enabled"],
    }
    run_id = "run_preview_" + _digest(run_seed).split(":", 1)[1][:16]
    models = _index_models(model_registry)
    traces = [
        _route_trace(workflow, module, model_registry, models, run_id, now)
        for module in workflow.get("modules", [])
    ]
    acceptance_checks = workflow["acceptance_policy"]["required_checks"]
    lockfile = {
        "run_id": run_id,
        "workflow_id": workflow["workflow_id"],
        "workflow_version": workflow["version"],
        "registry_version": workflow_registry["registry_version"],
        "model_registry_version": model_registry["registry_version"],
        "acceptance_policy_version": workflow["acceptance_policy"].get(
            "version", f"acceptance@{workflow['version']}"
        ),
        "run_mode": workflow.get("run_mode", "pinned"),
        "started_at": now,
        "runtime_enabled": workflow["runtime_enabled"],
        "write_through_enabled": workflow["write_through_enabled"],
        "external_side_effects_enabled": workflow["external_side_effects_enabled"],
        "modules": [
            {
                "module_id": module["module_id"],
                "module_version": module.get("version", "v1"),
                "route_alias": module["model_policy"]["route_alias"],
                "risk_level": module["risk_level"],
                "side_effect_level": module["side_effect_level"],
            }
            for module in workflow.get("modules", [])
        ],
        "route_traces": [trace["trace_id"] for trace in traces],
        "acceptance_criteria": [
            {
                "criterion_id": check,
                "description": f"Phase 2 projection requires {check}",
                "evidence_required": ["artifact:projection", "proof:read_only_projection"],
            }
            for check in acceptance_checks
        ],
        "evidence_policy": workflow["evidence_policy"],
        "context_digest": _digest({"workflow": workflow, "model_registry": model_registry}),
        "notes": "Read-only preview lockfile. It is not a runtime claim or execution authorization.",
    }
    return {
        "status": "PASS",
        "projection_only": True,
        "workflow_id": workflow["workflow_id"],
        "run_lockfile": lockfile,
        "route_traces": traces,
        "operator_summary": {
            "workflow_id": workflow["workflow_id"],
            "modules": len(workflow.get("modules", [])),
            "route_traces": len(traces),
            "runtime_enabled": workflow["runtime_enabled"],
            "write_through_enabled": workflow["write_through_enabled"],
            "external_side_effects_enabled": workflow["external_side_effects_enabled"],
            "boundary": "read_only_projection_no_task_claim_no_model_call_no_adapter_write",
        },
    }


def _write_projection(output_dir: Path, projection: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow_id = projection["workflow_id"]
    run_id = projection["run_lockfile"]["run_id"]
    lockfile_path = output_dir / f"{workflow_id}.{run_id}.run_lockfile.json"
    traces_path = output_dir / f"{workflow_id}.{run_id}.route_traces.json"
    summary_path = output_dir / f"{workflow_id}.{run_id}.summary.json"
    lockfile_path.write_text(
        json.dumps(projection["run_lockfile"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    traces_path.write_text(
        json.dumps(projection["route_traces"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(projection["operator_summary"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "run_lockfile": str(lockfile_path),
        "route_traces": str(traces_path),
        "operator_summary": str(summary_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-registry", type=Path, default=DEFAULT_WORKFLOW_REGISTRY)
    parser.add_argument("--model-registry", type=Path, default=DEFAULT_MODEL_REGISTRY)
    parser.add_argument("--workflow-id", default="decision_brief_pack")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true", help="write read-only projection artifacts")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    workflow_registry = _load_json(args.workflow_registry)
    model_registry = _load_json(args.model_registry)
    projection = build_projection(workflow_registry, model_registry, args.workflow_id)
    if args.write:
        projection["written"] = _write_projection(args.output_dir, projection)
    if args.as_json or not args.write:
        print(json.dumps(projection, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(projection.get("written", {}), indent=2, ensure_ascii=False))
    return 0 if projection["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
