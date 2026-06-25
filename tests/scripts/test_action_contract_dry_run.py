from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "action_contract_dry_run.py"
    spec = importlib.util.spec_from_file_location("_action_contract_dry_run", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_action_contract_example_receipt_is_deterministic_and_side_effect_free():
    module = _load_module()

    first = module.build_receipt(module.example_contract())
    second = module.build_receipt(module.example_contract())

    assert first == second
    assert first["status"] == "PASS"
    assert first["dry_run_only"] is True
    assert first["runtime_executor_enabled"] is False
    assert first["write_through_enabled"] is False
    assert first["mcp_enabled"] is False
    assert first["a2a_enabled"] is False
    assert first["external_side_effects"] is False
    assert first["builderio_package_installed"] is False
    assert first["errors"] == []
    assert first["boundary"] == "local_action_contract_validate_only_no_runtime_no_external_side_effects"


def test_action_contract_rejects_missing_authority_evidence_and_rollback():
    module = _load_module()
    contract = module.example_contract()
    del contract["authority_tier"]
    contract["evidence_refs"] = []
    contract["rollback_plan"] = ""

    receipt = module.build_receipt(contract)

    assert receipt["status"] == "FAIL"
    assert "missing_required_field:authority_tier" in receipt["errors"]
    assert "missing_authority_tier" in receipt["errors"]
    assert "missing_evidence_refs" in receipt["errors"]
    assert "missing_rollback_plan" in receipt["errors"]


def test_action_contract_rejects_external_side_effects_and_external_surfaces():
    module = _load_module()
    contract = module.example_contract()
    contract["external_side_effects"] = True
    contract["callable_surfaces"] = ["cli", "mcp", "a2a"]
    contract["package_imports"] = ["@agent-native/core"]

    receipt = module.build_receipt(contract)

    assert receipt["status"] == "FAIL"
    assert "external_side_effects_must_be_false" in receipt["errors"]
    assert "future_external_surfaces_must_not_be_enabled_in_dry_run" in receipt["errors"]
    assert "forbidden_external_package_import:@agent-native/core" in receipt["errors"]


def test_action_contract_writes_only_local_receipt_artifact(tmp_path):
    module = _load_module()
    receipt = module.build_receipt(module.example_contract())

    path = module.write_receipt(tmp_path, receipt)

    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["receipt_id"] == receipt["receipt_id"]
    assert written["external_side_effects"] is False
    assert written["runtime_executor_enabled"] is False
