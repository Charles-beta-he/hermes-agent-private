#!/usr/bin/env python3
"""Compile a dry-run acceptance bundle for a workflow projection.

This is an acceptance compiler, not an executor. It consumes a run lockfile and
route traces produced by workflow_projection.py and emits frozen criteria that a
future run must satisfy. It does not claim tasks, call models, execute tools, or
write through adapters.
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
DEFAULT_PROJECTION_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-projections"
DEFAULT_ACCEPTANCE_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-acceptance"


def _load_json(path: Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _default_projection_paths(workflow_id: str, run_id: str) -> tuple[Path, Path]:
    return (
        DEFAULT_PROJECTION_DIR / f"{workflow_id}.{run_id}.run_lockfile.json",
        DEFAULT_PROJECTION_DIR / f"{workflow_id}.{run_id}.route_traces.json",
    )


def compile_acceptance_bundle(
    run_lockfile: dict[str, Any], route_traces: list[dict[str, Any]]
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_id = run_lockfile["run_id"]
    workflow_id = run_lockfile["workflow_id"]
    criteria: list[dict[str, Any]] = []

    for criterion in run_lockfile.get("acceptance_criteria", []):
        criteria.append(
            {
                "criterion_id": criterion["criterion_id"],
                "description": criterion["description"],
                "required_evidence": criterion["evidence_required"],
                "source": "run_lockfile.acceptance_criteria",
            }
        )

    for module in run_lockfile.get("modules", []):
        module_id = module["module_id"]
        criteria.append(
            {
                "criterion_id": f"module_contract:{module_id}",
                "description": f"Module {module_id} must keep declared side-effect and risk boundaries.",
                "required_evidence": [
                    "artifact:run_lockfile",
                    "proof:module_contract_readback",
                ],
                "source": "run_lockfile.modules",
            }
        )

    for trace in route_traces:
        module_id = trace["module_id"]
        criteria.append(
            {
                "criterion_id": f"model_route:{module_id}",
                "description": f"Module {module_id} must use a registered pinned route with fallback and passing eval gate.",
                "required_evidence": [
                    "artifact:route_trace",
                    "proof:route_eval_gate_passed",
                ],
                "source": "route_trace",
            }
        )

    safety_flags = [
        "runtime_enabled",
        "write_through_enabled",
        "external_side_effects_enabled",
    ]
    for flag in safety_flags:
        criteria.append(
            {
                "criterion_id": f"safety_flag:{flag}",
                "description": f"{flag} must remain false in dry-run acceptance boundary.",
                "required_evidence": ["artifact:run_lockfile", "proof:flag_false"],
                "source": "run_lockfile.safety_flags",
            }
        )

    bundle_seed = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "context_digest": run_lockfile["context_digest"],
        "criteria_count": len(criteria),
    }
    return {
        "bundle_id": "acc_" + _digest(bundle_seed).split(":", 1)[1][:16],
        "run_id": run_id,
        "workflow_id": workflow_id,
        "compiled_at": now,
        "frozen": True,
        "dry_run_only": True,
        "runtime_enabled": False,
        "write_through_enabled": False,
        "external_side_effects_enabled": False,
        "criteria": criteria,
        "human_gate_packet_template": {
            "decision_needed": "Authorize or reject transition from dry-run acceptance to local reversible execution.",
            "confirmed_facts": [
                "Run lockfile exists",
                "Route traces exist",
                "No runtime/write-through/external side-effect flag is enabled",
            ],
            "unconfirmed_facts": [
                "Executor implementation has not been authorized",
                "Adapter write-through remains unopened",
            ],
            "options": [
                "continue_dry_run_only",
                "authorize_local_reversible_executor_stub",
                "stop_and_review_architecture",
            ],
            "recommended_default": "continue_dry_run_only",
            "blast_radius": "Local contract/projection artifacts only unless explicitly authorized otherwise.",
            "rollback_plan": "Revert generated acceptance artifact and keep registries pinned; no runtime state to roll back.",
            "post_action_verification": "Re-run validator, projection, acceptance compiler, and focused tests before any executor integration.",
        },
        "boundary": "dry_run_acceptance_only_no_execution",
    }


def _write_bundle(output_dir: Path, bundle: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{bundle['workflow_id']}.{bundle['run_id']}.{bundle['bundle_id']}.acceptance.json"
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", default="decision_brief_pack")
    parser.add_argument("--run-id", default="run_preview_78eee6afcec1f12a")
    parser.add_argument("--run-lockfile", type=Path)
    parser.add_argument("--route-traces", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ACCEPTANCE_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    default_lockfile, default_traces = _default_projection_paths(args.workflow_id, args.run_id)
    run_lockfile_path = args.run_lockfile or default_lockfile
    route_traces_path = args.route_traces or default_traces
    run_lockfile = _load_json(run_lockfile_path)
    route_traces = _load_json(route_traces_path)
    if not isinstance(run_lockfile, dict):
        raise SystemExit("run lockfile must be a JSON object")
    if not isinstance(route_traces, list):
        raise SystemExit("route traces must be a JSON array")
    bundle = compile_acceptance_bundle(run_lockfile, route_traces)
    if args.write:
        bundle["written"] = str(_write_bundle(args.output_dir, bundle))
    if args.as_json or not args.write:
        print(json.dumps(bundle, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(bundle.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
