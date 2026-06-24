#!/usr/bin/env python3
"""Validate a Hermes LoopRuntimeContract and emit a deterministic dry-run receipt.

This is the first local-only Loop Engineering landing slice for Hermes. It turns
Loop Engineering findings into a fail-closed contract validator, but it does not
enable a scheduler, start workers, mutate taskpool/mission state, or perform any
external side effects. A future runtime can promote only contracts that pass this
doctor plus separate human-gated executor authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "loop-runtime-contract-dry-runs"
ACCEPTED_EVIDENCE_PREFIXES = ("command:", "artifact:", "proof:", "file:", "url:", "brainctl:")
REQUIRED_MOVES = ("discovery", "handoff", "verification", "persistence", "scheduling")
REQUIRED_PARTS = ("automations", "worktrees", "skills", "connectors", "sub_agents", "memory")
REQUIRED_FIELDS = (
    "contract_id",
    "version",
    "objective",
    "authority_tier",
    "moves",
    "parts",
    "workspace_policy",
    "generator",
    "evaluator",
    "persistence",
    "scheduler",
    "budget",
    "human_gate",
    "evidence_refs",
    "rollback_plan",
    "external_side_effects",
)
WRITE_AUTHORITY_TIERS = {"local-reversible-write", "human-gated-external-write"}
ISOLATED_WORKSPACES = {"git_worktree", "sandbox", "kanban_worktree"}


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(data: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def example_contract() -> dict[str, Any]:
    return {
        "contract_id": "hermes.loop_runtime.contract.doctor",
        "version": "1.0.0",
        "objective": "Validate a proposed Hermes loop before any scheduler, worker, taskpool, mission, or external-write runtime can execute it.",
        "authority_tier": "local-reversible-dry-run",
        "moves": {
            "discovery": {
                "source_refs": ["file:/tmp/loop_engineering_orange_book.txt", "command:hermes cron list"],
                "output": "loop_candidate",
            },
            "handoff": {
                "mode": "contract_only_no_worker_claim",
                "workspace_policy": "git_worktree",
            },
            "verification": {
                "required": True,
                "evaluator_ref": "evaluator.independent_checker",
            },
            "persistence": {
                "ssot": "docs/private/loop-engineering-runtime-contract/loop_runtime_contract_ssot_20260624.md",
                "receipt_ledger": "docs/private/loop-runtime-contract-dry-runs/",
            },
            "scheduling": {
                "mode": "disabled_until_human_gated_runtime_authorization",
                "trigger": "manual_doctor_only",
            },
        },
        "parts": {
            "automations": {"present": True, "binding": "manual doctor now; cron/mission binding requires future gate"},
            "worktrees": {"present": True, "binding": "required for write loops"},
            "skills": {"present": True, "binding": "objective-model-path-verification + hermes-agent + architecture-governance-evaluation"},
            "connectors": {"present": True, "binding": "local file/git only in this slice"},
            "sub_agents": {"present": True, "binding": "evaluator contract required; no worker spawned in this slice"},
            "memory": {"present": True, "binding": "SSOT doc + deterministic receipt, not global memory"},
        },
        "workspace_policy": "git_worktree",
        "generator": {
            "role": "loop_contract_proposer",
            "can_write_runtime_state": False,
            "toolsets": ["file", "terminal", "skills"],
        },
        "evaluator": {
            "role": "independent_checker",
            "independent_from_generator": True,
            "can_veto": True,
            "default_posture": "assume_broken_until_evidence_passes",
            "must_execute_checks": ["schema_presence", "five_moves", "six_parts", "budget", "human_gate", "side_effect_boundary"],
            "evidence_refs": [
                "file:tests/scripts/test_loop_runtime_contract_doctor.py",
                "command:./venv/bin/python -m pytest -o addopts='' tests/scripts/test_loop_runtime_contract_doctor.py -q",
            ],
        },
        "persistence": {
            "ssot": "docs/private/loop-engineering-runtime-contract/loop_runtime_contract_ssot_20260624.md",
            "receipt_ledger": "docs/private/loop-runtime-contract-dry-runs/",
            "state_authority": "receipt_is_evidence_only_no_runtime_state",
        },
        "scheduler": {
            "enabled": False,
            "allowed_triggers": ["manual_doctor"],
            "promotion_requires": "human_gated_runtime_executor_authorization",
        },
        "budget": {
            "max_turns": 8,
            "max_runtime_seconds": 300,
            "max_subagents": 2,
            "max_retries": 1,
            "daily_token_budget": 200000,
        },
        "human_gate": {
            "required_before": ["enable_scheduler", "spawn_worker", "write_taskpool", "external_write", "publish", "destructive_action"],
            "decision_packet": "Authorize a specific runtime executor binding with blast radius, rollback, budget, and verifier evidence.",
        },
        "evidence_refs": [
            "file:/Users/charles/Downloads/Loop-Engineering橙皮书-v260615.pdf",
            "file:/tmp/loop_engineering_orange_book.txt",
            "file:schemas/loop_runtime_contract.schema.json",
            "file:scripts/loop_runtime_contract_doctor.py",
            "proof:local dry-run validator only; no scheduler, worker, taskpool, mission, or external side effects enabled",
        ],
        "rollback_plan": "Delete the local LoopRuntimeContract dry-run receipt and SSOT artifacts; no scheduler, worker, mission, taskpool, or external system was mutated.",
        "external_side_effects": False,
        "loop_runtime_enabled": False,
    }


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in contract:
            errors.append(f"missing_required_field:{field}")

    if contract.get("external_side_effects") is not False:
        errors.append("external_side_effects_must_be_false")
    if contract.get("loop_runtime_enabled") not in (None, False):
        errors.append("loop_runtime_enabled_must_be_false")

    if not _has_text(contract.get("contract_id")):
        errors.append("missing_contract_id")
    if not _has_text(contract.get("objective")):
        errors.append("missing_objective")
    if not _has_text(contract.get("rollback_plan")):
        errors.append("missing_rollback_plan")

    moves = contract.get("moves")
    if not isinstance(moves, dict):
        errors.append("moves_must_be_object")
        moves = {}
    for move in REQUIRED_MOVES:
        if move not in moves:
            errors.append(f"missing_move:{move}")

    parts = contract.get("parts")
    if not isinstance(parts, dict):
        errors.append("parts_must_be_object")
        parts = {}
    for part in REQUIRED_PARTS:
        if part not in parts:
            errors.append(f"missing_part:{part}")

    evidence_refs = contract.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        errors.append("missing_evidence_refs")
    else:
        for ref in evidence_refs:
            if not isinstance(ref, str) or not ref.startswith(ACCEPTED_EVIDENCE_PREFIXES):
                errors.append(f"invalid_evidence_ref:{ref}")

    evaluator_value = contract.get("evaluator")
    evaluator: dict[str, Any] = evaluator_value if isinstance(evaluator_value, dict) else {}
    if evaluator.get("independent_from_generator") is not True:
        errors.append("evaluator_must_be_independent_from_generator")
    if evaluator.get("can_veto") is not True:
        errors.append("evaluator_must_be_able_to_veto")
    evaluator_refs = evaluator.get("evidence_refs")
    if not isinstance(evaluator_refs, list) or not evaluator_refs:
        errors.append("evaluator_missing_evidence_refs")
    elif any(not isinstance(ref, str) or not ref.startswith(ACCEPTED_EVIDENCE_PREFIXES) for ref in evaluator_refs):
        errors.append("evaluator_invalid_evidence_refs")

    authority_tier = contract.get("authority_tier")
    workspace_policy = contract.get("workspace_policy")
    if authority_tier in WRITE_AUTHORITY_TIERS and workspace_policy not in ISOLATED_WORKSPACES:
        errors.append("write_loop_requires_isolated_workspace")

    budget_value = contract.get("budget")
    budget: dict[str, Any] = budget_value if isinstance(budget_value, dict) else {}
    if int(budget.get("max_turns") or 0) <= 0:
        errors.append("budget_max_turns_must_be_positive")
    if int(budget.get("max_runtime_seconds") or 0) <= 0:
        errors.append("budget_max_runtime_seconds_must_be_positive")
    if int(budget.get("daily_token_budget") or 0) <= 0:
        errors.append("budget_daily_token_budget_must_be_positive")
    if int(budget.get("max_subagents") or 0) > 3:
        errors.append("budget_max_subagents_exceeds_default_safe_cap")

    human_gate_value = contract.get("human_gate")
    human_gate: dict[str, Any] = human_gate_value if isinstance(human_gate_value, dict) else {}
    if not _list(human_gate.get("required_before")):
        errors.append("human_gate_missing_required_before")
    if not _has_text(human_gate.get("decision_packet")):
        errors.append("human_gate_missing_decision_packet")

    scheduler_value = contract.get("scheduler")
    scheduler: dict[str, Any] = scheduler_value if isinstance(scheduler_value, dict) else {}
    if scheduler.get("enabled") not in (None, False):
        errors.append("scheduler_must_be_disabled_in_dry_run")

    return errors


def build_receipt(contract: dict[str, Any]) -> dict[str, Any]:
    errors = validate_contract(contract)
    contract_digest = digest(contract)
    receipt_seed = {
        "schema": "loop_runtime_contract_doctor_receipt_v1",
        "contract_digest": contract_digest,
        "contract_id": contract.get("contract_id"),
        "version": contract.get("version"),
    }
    return {
        "schema": "loop_runtime_contract_doctor_receipt_v1",
        "status": "PASS" if not errors else "FAIL",
        "receipt_id": "loopdry_" + digest(receipt_seed).split(":", 1)[1][:16],
        "contract_digest": contract_digest,
        "contract_id": contract.get("contract_id", ""),
        "version": contract.get("version", ""),
        "dry_run_only": True,
        "loop_runtime_enabled": False,
        "scheduler_enabled": False,
        "worker_spawn_enabled": False,
        "taskpool_write_enabled": False,
        "mission_write_enabled": False,
        "external_side_effects": False,
        "coverage": {
            "moves": list(REQUIRED_MOVES),
            "parts": list(REQUIRED_PARTS),
            "evaluator_required": True,
            "human_gate_required": True,
            "budget_required": True,
        },
        "errors": errors,
        "evidence_refs": contract.get("evidence_refs", []),
        "rollback_plan": contract.get("rollback_plan", ""),
        "boundary": "local_loop_runtime_contract_validate_only_no_scheduler_no_executor_no_external_side_effects",
    }


def load_contract(path: Path | None) -> dict[str, Any]:
    if path is None:
        return example_contract()
    return json.loads(path.read_text(encoding="utf-8"))


def write_receipt(output_dir: Path, receipt: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    contract_id = str(receipt.get("contract_id") or "loop_runtime_contract").replace("/", "_")
    path = output_dir / f"{contract_id}.{receipt['receipt_id']}.loop_runtime_contract_dry_run.json"
    path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, help="Path to a LoopRuntimeContract JSON manifest. Defaults to a built-in local dry-run contract.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true", help="Write the deterministic receipt to docs/private.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    receipt = build_receipt(load_contract(args.contract))
    if args.write:
        receipt["written"] = str(write_receipt(args.output_dir, receipt))
    if args.as_json or not args.write:
        print(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(receipt.get("written", ""))
    return 0 if receipt["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
