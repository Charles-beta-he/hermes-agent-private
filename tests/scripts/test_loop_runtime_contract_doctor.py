from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "loop_runtime_contract_doctor.py"
    spec = importlib.util.spec_from_file_location("_loop_runtime_contract_doctor", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_loop_runtime_example_has_five_moves_six_parts_and_fail_closed_receipt():
    module = _load_module()

    first = module.build_receipt(module.example_contract())
    second = module.build_receipt(module.example_contract())

    assert first == second
    assert first["status"] == "PASS"
    assert first["dry_run_only"] is True
    assert first["loop_runtime_enabled"] is False
    assert first["external_side_effects"] is False
    assert first["errors"] == []
    assert first["boundary"] == "local_loop_runtime_contract_validate_only_no_scheduler_no_executor_no_external_side_effects"
    assert first["coverage"]["moves"] == ["discovery", "handoff", "verification", "persistence", "scheduling"]
    assert first["coverage"]["parts"] == ["automations", "worktrees", "skills", "connectors", "sub_agents", "memory"]


def test_loop_runtime_rejects_missing_independent_evaluator_and_objective_evidence():
    module = _load_module()
    contract = module.example_contract()
    contract["evaluator"]["independent_from_generator"] = False
    contract["evaluator"]["can_veto"] = False
    contract["evaluator"]["evidence_refs"] = []

    receipt = module.build_receipt(contract)

    assert receipt["status"] == "FAIL"
    assert "evaluator_must_be_independent_from_generator" in receipt["errors"]
    assert "evaluator_must_be_able_to_veto" in receipt["errors"]
    assert "evaluator_missing_evidence_refs" in receipt["errors"]


def test_loop_runtime_rejects_write_loop_without_worktree_budget_and_human_gate():
    module = _load_module()
    contract = module.example_contract()
    contract["authority_tier"] = "local-reversible-write"
    contract["workspace_policy"] = "shared_repo"
    contract["budget"] = {"max_turns": 0, "max_runtime_seconds": 0, "max_subagents": 9, "daily_token_budget": 0}
    contract["human_gate"] = {"required_before": [], "decision_packet": ""}

    receipt = module.build_receipt(contract)

    assert receipt["status"] == "FAIL"
    assert "write_loop_requires_isolated_workspace" in receipt["errors"]
    assert "budget_max_turns_must_be_positive" in receipt["errors"]
    assert "budget_max_runtime_seconds_must_be_positive" in receipt["errors"]
    assert "budget_daily_token_budget_must_be_positive" in receipt["errors"]
    assert "human_gate_missing_required_before" in receipt["errors"]
    assert "human_gate_missing_decision_packet" in receipt["errors"]


def test_loop_runtime_writes_only_local_receipt_artifact(tmp_path):
    module = _load_module()
    receipt = module.build_receipt(module.example_contract())

    path = module.write_receipt(tmp_path, receipt)

    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["receipt_id"] == receipt["receipt_id"]
    assert written["external_side_effects"] is False
    assert written["loop_runtime_enabled"] is False
