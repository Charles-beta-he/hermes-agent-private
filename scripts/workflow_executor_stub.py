#!/usr/bin/env python3
"""Local reversible executor stub for modular workflow kernels.

This script is intentionally not a real executor. It consumes a frozen acceptance
bundle and produces a local dry-run result showing which criteria would be
executed later. It does not claim taskpool work, call models, invoke tools, write
adapters, send messages, or mutate runtime state.
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
DEFAULT_ACCEPTANCE_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-acceptance"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "workflow-kernel-executor-dry-runs"
DEFAULT_WORKFLOW_ID = "decision_brief_pack"
DEFAULT_RUN_ID = "run_preview_78eee6afcec1f12a"
DEFAULT_BUNDLE_ID = "acc_3c811427ac76ce88"


def _digest(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_acceptance_path(workflow_id: str, run_id: str, bundle_id: str) -> Path:
    return DEFAULT_ACCEPTANCE_DIR / f"{workflow_id}.{run_id}.{bundle_id}.acceptance.json"


def build_dry_run_result(bundle: dict[str, Any]) -> dict[str, Any]:
    if bundle.get("frozen") is not True or bundle.get("dry_run_only") is not True:
        raise SystemExit("acceptance bundle must be frozen and dry_run_only")
    for flag in ("runtime_enabled", "write_through_enabled", "external_side_effects_enabled"):
        if bundle.get(flag) is not False:
            raise SystemExit(f"acceptance bundle crosses forbidden boundary: {flag}=true")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    criteria = bundle.get("criteria", [])
    steps: list[dict[str, Any]] = []
    for index, criterion in enumerate(criteria, start=1):
        criterion_id = criterion["criterion_id"]
        boundary_sensitive = criterion_id.startswith("safety_flag:")
        steps.append(
            {
                "step_id": f"dry_step_{index:03d}",
                "status": "blocked_by_boundary" if boundary_sensitive else "would_run",
                "source_criterion": criterion_id,
                "evidence": [
                    "artifact:acceptance_bundle",
                    "proof:executor_stub_no_runtime_side_effect",
                ],
            }
        )

    seed = {
        "run_id": bundle["run_id"],
        "workflow_id": bundle["workflow_id"],
        "bundle_id": bundle["bundle_id"],
        "criteria_count": len(criteria),
    }
    return {
        "result_id": "execdry_" + _digest(seed).split(":", 1)[1][:16],
        "run_id": bundle["run_id"],
        "workflow_id": bundle["workflow_id"],
        "acceptance_bundle_id": bundle["bundle_id"],
        "executed_at": now,
        "dry_run_only": True,
        "local_reversible_only": True,
        "runtime_enabled": False,
        "taskpool_claim_enabled": False,
        "model_call_enabled": False,
        "adapter_write_enabled": False,
        "external_side_effects_enabled": False,
        "steps": steps,
        "rollback_plan": "Delete this dry-run result artifact; no taskpool/runtime/model/adapter state was mutated.",
        "boundary": "local_reversible_executor_stub_only_no_taskpool_no_model_no_adapter",
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['workflow_id']}.{result['run_id']}.{result['result_id']}.executor_dry_run.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID)
    parser.add_argument("--acceptance-bundle", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    acceptance_path = args.acceptance_bundle or default_acceptance_path(
        args.workflow_id, args.run_id, args.bundle_id
    )
    bundle = _load_json(acceptance_path)
    result = build_dry_run_result(bundle)
    if args.write:
        result["written"] = str(write_result(args.output_dir, result))
    if args.as_json or not args.write:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
