#!/usr/bin/env python3
"""Dry-run verifier for the Agent-native five-layer Workflow Kernel acceptance lens.

This command reads a local JSON fixture describing a candidate architecture slice
and emits a deterministic verdict against the five-layer lens. It is deliberately
local-only: it never enables runtime, writes taskpool state, calls models,
mutates adapters, sends messages, or performs external side effects.
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
DEFAULT_FIXTURE = REPO_ROOT / "docs" / "private" / "workflow-kernel-acceptance-lens-dry-run" / "agent_native_five_layer_fixture.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-acceptance-lens-dry-run"
REQUIRED_LAYERS = (
    "application_surface",
    "runtime_host",
    "prompt_capability_stack",
    "agent_runtime",
    "tool_artifact_loop",
)
FORBIDDEN_TRUE_FLAGS = (
    "external_side_effects_enabled",
    "runtime_enabled",
    "write_through_enabled",
    "task_lifecycle_mutation_enabled",
    "model_call_enabled",
    "remote_push_enabled",
    "production_deploy_enabled",
    "secret_write_enabled",
    "destructive_delete_enabled",
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_acceptance_lens_dry_run(fixture: dict[str, Any]) -> dict[str, Any]:
    candidate = fixture.get("candidate")
    if not isinstance(candidate, dict):
        raise SystemExit("fixture must contain candidate object")
    layers = candidate.get("layers")
    if not isinstance(layers, dict):
        raise SystemExit("candidate must contain layers object")
    safety_flags = candidate.get("safety_flags", {})
    if not isinstance(safety_flags, dict):
        raise SystemExit("candidate.safety_flags must be an object")

    layer_results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for layer in REQUIRED_LAYERS:
        record = layers.get(layer)
        present = isinstance(record, dict)
        evidence_refs = record.get("evidence_refs") if present else None
        requirements = record.get("requirements") if present else None
        ok = bool(
            present
            and record.get("declared") is True
            and isinstance(evidence_refs, list)
            and len(evidence_refs) > 0
            and isinstance(requirements, list)
            and len(requirements) > 0
        )
        if not ok:
            failures.append(f"layer_not_satisfied:{layer}")
        layer_results[layer] = {
            "declared": bool(record.get("declared")) if present else False,
            "evidence_ref_count": len(evidence_refs) if isinstance(evidence_refs, list) else 0,
            "requirement_count": len(requirements) if isinstance(requirements, list) else 0,
            "ok": ok,
        }

    flag_results: dict[str, bool] = {}
    for flag in FORBIDDEN_TRUE_FLAGS:
        ok = safety_flags.get(flag) is False
        flag_results[flag] = ok
        if not ok:
            failures.append(f"forbidden_flag_not_false:{flag}")

    durable_artifact_ref_present = bool(candidate.get("durable_artifact_ref"))
    human_intervention_trigger_declared = bool(candidate.get("human_intervention_trigger"))
    if not durable_artifact_ref_present:
        failures.append("durable_artifact_ref_missing")
    if not human_intervention_trigger_declared:
        failures.append("human_intervention_trigger_missing")

    all_ok = not failures
    verdict = "implementation_ready_not_runtime_enabled" if all_ok else "blocked_by_acceptance_lens"
    seed = {
        "candidate_id": candidate.get("candidate_id"),
        "layers": layer_results,
        "flags": flag_results,
        "durable_artifact_ref_present": durable_artifact_ref_present,
        "human_intervention_trigger_declared": human_intervention_trigger_declared,
        "verdict": verdict,
    }
    result_id = "aclens_" + _digest(seed)[:16]
    return {
        "schema": "workflow_kernel_acceptance_lens_dry_run_v1",
        "result_id": result_id,
        "candidate_id": candidate.get("candidate_id"),
        "generated_at": _now_utc(),
        "dry_run_only": True,
        "local_reversible_only": True,
        "runtime_enabled": False,
        "write_through_enabled": False,
        "external_side_effects_enabled": False,
        "task_lifecycle_mutation_enabled": False,
        "model_call_enabled": False,
        "layer_results": layer_results,
        "flag_results": flag_results,
        "durable_artifact_ref_present": durable_artifact_ref_present,
        "human_intervention_trigger_declared": human_intervention_trigger_declared,
        "failures": failures,
        "verdict": verdict,
        "boundary": "acceptance_lens_dry_run_only_no_runtime_no_task_lifecycle_mutation",
        "evidence_refs": fixture.get("evidence_refs", []),
    }


def write_dry_run(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['candidate_id']}.{result['result_id']}.acceptance_lens_dry_run.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = build_acceptance_lens_dry_run(_load_json(args.fixture))
    if args.write:
        result["written"] = str(write_dry_run(args.output_dir, result))
    if args.as_json or not args.write:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
