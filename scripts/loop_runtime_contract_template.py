#!/usr/bin/env python3
"""Compile concrete LoopRuntimeContract candidates for existing Hermes workflows.

The candidates produced here are fail-closed local artifacts. They do not enable
runtime execution, schedulers, worker spawn, taskpool writes, mission executor
promotion, credentialed generation, or external side effects. They provide the
next preflight surface for project-level workflow updates after the generic
LoopRuntimeContract doctor and Control Tower projections are in place.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import loop_runtime_contract_doctor as doctor  # noqa: E402

DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "private" / "loop-runtime-contract-candidates"


def control_tower_runtime_promotion_contract() -> dict[str, Any]:
    """Return the first concrete workflow candidate contract.

    This candidate models the future Control Tower runtime/API promotion path,
    but remains a dry-run contract. It intentionally requires a human gate before
    any real runtime/API, executor, taskpool, mission, scheduler, or external
    boundary can be opened.
    """
    return {
        "contract_id": "hermes.workflow_kernel.control_tower_runtime_promotion",
        "version": "1.0.0",
        "objective": "Preflight a future Control Tower runtime/API promotion without enabling any scheduler, worker, taskpool write-through, mission executor, or external side effect.",
        "authority_tier": "local-reversible-dry-run",
        "moves": {
            "discovery": {
                "source_refs": [
                    "file:docs/private/workflow-kernel-control-tower-projection/workflow_kernel_control_tower_projection_20260613_phase52.json",
                    "file:docs/private/workflow-kernel-runtime-readiness-summary/workflow_kernel_runtime_readiness_summary_20260613_phase36.json",
                    "file:docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json",
                ],
                "output": "control_tower_runtime_promotion_candidate",
            },
            "handoff": {
                "mode": "contract_template_only_no_executor_claim",
                "workspace_policy": "git_worktree",
            },
            "verification": {
                "required": True,
                "evaluator_ref": "loop_runtime_contract_doctor",
                "fail_closed_on": [
                    "missing_budget",
                    "missing_rollback",
                    "non_independent_evaluator",
                    "scheduler_enabled",
                    "external_side_effects_enabled",
                ],
            },
            "persistence": {
                "ssot": "docs/private/loop-engineering-runtime-contract/loop_runtime_contract_ssot_20260624.md",
                "candidate_dir": "docs/private/loop-runtime-contract-candidates/",
                "receipt_ledger": "docs/private/loop-runtime-contract-candidates/",
            },
            "scheduling": {
                "mode": "disabled_until_runtime_promotion_authorization",
                "trigger": "manual_template_compile_and_doctor_only",
            },
        },
        "parts": {
            "automations": {"present": True, "binding": "template compiler + doctor only; no scheduler binding"},
            "worktrees": {"present": True, "binding": "required before future write-loop promotion"},
            "skills": {"present": True, "binding": "objective-model-path-verification + hermes-agent + test-driven-development"},
            "connectors": {"present": True, "binding": "local file/git/control-tower projection only"},
            "sub_agents": {"present": True, "binding": "future independent evaluator only; no worker spawned"},
            "memory": {"present": True, "binding": "candidate contract + receipt, not global memory"},
        },
        "workspace_policy": "git_worktree",
        "generator": {
            "role": "control_tower_runtime_contract_proposer",
            "can_write_runtime_state": False,
            "toolsets": ["file", "terminal"],
        },
        "evaluator": {
            "role": "loop_runtime_contract_doctor",
            "independent_from_generator": True,
            "can_veto": True,
            "default_posture": "fail_closed_until_all_promotion_evidence_exists",
            "must_execute_checks": [
                "schema_presence",
                "five_moves",
                "six_parts",
                "budget",
                "human_gate",
                "side_effect_boundary",
                "runtime_promotion_disabled",
            ],
            "evidence_refs": [
                "file:tests/scripts/test_loop_runtime_contract_template.py",
                "file:tests/scripts/test_loop_runtime_contract_doctor.py",
                "command:./venv/bin/python -m pytest -o addopts='' tests/scripts/test_loop_runtime_contract_template.py tests/scripts/test_loop_runtime_contract_doctor.py -q",
            ],
        },
        "persistence": {
            "ssot": "docs/private/loop-engineering-runtime-contract/loop_runtime_contract_ssot_20260624.md",
            "candidate_dir": "docs/private/loop-runtime-contract-candidates/",
            "state_authority": "candidate_and_receipt_are_preflight_evidence_only_no_runtime_state",
        },
        "scheduler": {
            "enabled": False,
            "allowed_triggers": ["manual_template_compile", "manual_doctor"],
            "promotion_requires": "project_authorization_plus_exact_runtime_api_executor_budget_rollback_evaluator_packet",
        },
        "budget": {
            "max_turns": 12,
            "max_runtime_seconds": 600,
            "max_subagents": 2,
            "max_retries": 1,
            "daily_token_budget": 300000,
        },
        "human_gate": {
            "required_before": [
                "activate_control_tower_runtime_api",
                "enable_scheduler",
                "spawn_worker",
                "write_taskpool",
                "promote_mission_executor",
                "credentialed_model_generation",
                "external_write",
                "publish",
                "destructive_action",
            ],
            "decision_packet": "Authorize exact Control Tower runtime/API executor binding with scope, budget, rollback, kill switch, evaluator command, and readback evidence.",
        },
        "evidence_refs": [
            "file:docs/private/workflow-kernel-control-tower-projection/workflow_kernel_control_tower_projection_20260613_phase52.json",
            "file:docs/private/workflow-kernel-runtime-readiness-summary/workflow_kernel_runtime_readiness_summary_20260613_phase36.json",
            "file:docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json",
            "file:scripts/loop_runtime_contract_template.py",
            "file:scripts/loop_runtime_contract_doctor.py",
            "proof:local candidate contract only; no runtime api, scheduler, worker, taskpool write, mission executor, credentialed generation, or external side effect enabled",
        ],
        "rollback_plan": "Delete the local candidate contract and receipt under docs/private/loop-runtime-contract-candidates/; no runtime, scheduler, worker, taskpool, mission, credential, or external state was mutated.",
        "external_side_effects": False,
        "loop_runtime_enabled": False,
    }


CANDIDATES = {
    "control_tower_runtime_promotion": control_tower_runtime_promotion_contract,
}


def write_candidate(contract: dict[str, Any], out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = contract["contract_id"].replace(".", "_")
    contract_path = out_dir / f"{stem}.loop_runtime_contract.json"
    receipt = doctor.build_receipt(contract)
    receipt_path = out_dir / f"{stem}.{receipt['receipt_id']}.loop_runtime_contract_receipt.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"contract": str(contract_path), "receipt": str(receipt_path)}


def control_tower_runtime_promotion_authorization_packet(contract: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Return a human-gated packet for a future runtime promotion.

    This packet is intentionally not an authorization grant. It records the exact
    authority that would be needed to cross from local read-only projection into
    runtime/API activation while keeping every scheduler/write/external boundary
    closed by default.
    """
    all_write_side_effects_disabled = all(
        receipt.get(flag) is False
        for flag in [
            "loop_runtime_enabled",
            "scheduler_enabled",
            "worker_spawn_enabled",
            "taskpool_write_enabled",
            "mission_write_enabled",
            "external_side_effects",
        ]
    )
    return {
        "packet_id": "authz.hermes.workflow_kernel.control_tower_runtime_promotion.v1",
        "status": "HUMAN_AUTHORIZATION_REQUIRED",
        "objective": "Authorize, or explicitly reject, the first runtime/API promotion path for the Control Tower LoopRuntimeContract candidate.",
        "candidate": {
            "contract_id": contract.get("contract_id"),
            "receipt_id": receipt.get("receipt_id"),
            "doctor_status": receipt.get("status"),
            "dry_run_only": receipt.get("dry_run_only") is True,
        },
        "preconditions": {
            "candidate_doctor_passed": receipt.get("status") == "PASS",
            "all_write_side_effects_disabled": all_write_side_effects_disabled,
            "generic_loop_contract_visible_in_control_tower": True,
            "concrete_candidate_visible_in_control_tower": True,
        },
        "requested_authority": {
            "activate_control_tower_runtime_api": True,
            "enable_scheduler": False,
            "spawn_worker": False,
            "write_taskpool": False,
            "promote_mission_executor": False,
            "credentialed_model_generation": False,
            "external_dispatch": False,
            "publish": False,
            "destructive_action": False,
        },
        "human_decision_required": [
            "exact_executor_binding",
            "runtime_api_scope",
            "budget_max_turns_runtime_seconds_retries",
            "rollback_owner_and_command",
            "kill_switch_location",
            "independent_evaluator_command",
            "post_action_readback_surface",
        ],
        "default_safe_decision": "do_not_promote_runtime_until_all_required_fields_are_explicitly_authorized",
        "escape_hatch": {
            "kill_switch": "disable_loop_runtime_contract_promotion",
            "rollback": contract.get("rollback_plan"),
            "blast_radius": "local Hermes Control Tower runtime/API only; no scheduler, worker, taskpool, mission, credential, external dispatch, publish, or destructive authority included in this packet",
        },
        "post_action_verification": [
            "post_action_readback",
            "control_tower_snapshot_runtime_api_status",
            "loop_runtime_contract_doctor_recheck",
            "pytest_targeted_control_tower_snapshot",
            "git_diff_scoped_to_runtime_promotion_slice",
        ],
        "evidence_refs": [
            "file:docs/private/loop-runtime-contract-candidates/hermes_workflow_kernel_control_tower_runtime_promotion.loop_runtime_contract.json",
            "file:docs/private/loop-runtime-contract-candidates/hermes_workflow_kernel_control_tower_runtime_promotion.loopdry_97fee7870ae83828.loop_runtime_contract_receipt.json",
            "file:docs/private/workflow-kernel-control-tower-projection/workflow_kernel_control_tower_projection_20260613_phase52.json",
            "command:./venv/bin/python -m pytest -o addopts='' tests/scripts/test_loop_runtime_contract_template.py tests/scripts/test_workflow_control_tower_projection.py tests/hermes_cli/test_control_tower_snapshot.py::test_control_tower_snapshot_includes_workflow_kernel_projection_when_artifacts_exist -q",
        ],
    }


def write_authorization_packet(packet: dict[str, Any], out_dir: Path = DEFAULT_OUT_DIR) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "hermes_workflow_kernel_control_tower_runtime_promotion.authorization_packet.json"
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=sorted(CANDIDATES), default="control_tower_runtime_promotion")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--write-authorization-packet", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    contract = CANDIDATES[args.candidate]()
    receipt = doctor.build_receipt(contract)
    authorization_packet = control_tower_runtime_promotion_authorization_packet(contract, receipt)
    result: dict[str, Any] = {
        "candidate": args.candidate,
        "contract": contract,
        "receipt": receipt,
        "authorization_packet": authorization_packet,
    }
    if args.write:
        result["artifacts"] = write_candidate(contract, args.out_dir)
    if args.write_authorization_packet:
        result.setdefault("artifacts", {})["authorization_packet"] = write_authorization_packet(authorization_packet, args.out_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{args.candidate}: {receipt['status']} receipt={receipt['receipt_id']}")
        if args.write:
            print(f"contract: {result['artifacts']['contract']}")
            print(f"receipt: {result['artifacts']['receipt']}")
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
