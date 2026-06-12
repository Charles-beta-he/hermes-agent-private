from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


def _load_validator_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_contract_validator.py"
    spec = importlib.util.spec_from_file_location("_workflow_contract_validator", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _valid_registry() -> dict:
    return json.loads((_repo_root() / "config" / "workflow_registry.json").read_text(encoding="utf-8"))


def _write_registry(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "workflow_registry.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _valid_model_registry() -> dict:
    return json.loads((_repo_root() / "config" / "model_registry.json").read_text(encoding="utf-8"))


def _write_model_registry(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "model_registry.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_default_workflow_registry_passes_phase1_contract():
    validator = _load_validator_module()

    result = validator.validate_registry(
        _repo_root() / "config" / "workflow_registry.json",
        _repo_root() / "schemas" / "workflow_contract.schema.json",
    )

    assert result.status == "PASS"
    assert result.validated_workflows == 1
    assert result.validated_modules >= 5
    assert result.validated_models >= 4
    assert result.validated_route_aliases >= 5
    assert result.runtime_enabled is False
    assert result.write_through_enabled is False
    assert result.external_side_effects_enabled is False
    assert result.errors == []


def test_registry_fails_when_runtime_enabled(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    data["defaults"]["runtime_enabled"] = True
    data["workflows"][0]["runtime_enabled"] = True

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("runtime_enabled=false" in err for err in result.errors)


def test_registry_fails_when_module_allows_external_side_effect(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    data["workflows"][0]["modules"][0]["side_effect_level"] = "external_write"

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("external/destructive side effects" in err for err in result.errors)


def test_registry_fails_without_acceptance_freeze(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    data["workflows"][0]["acceptance_policy"]["freeze_before_execution"] = False

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("freeze_before_execution" in err for err in result.errors)


def test_registry_fails_when_human_packet_is_incomplete(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    fields = data["workflows"][0]["human_gate"]["packet_required_fields"]
    data["workflows"][0]["human_gate"]["packet_required_fields"] = [
        field for field in fields if field != "rollback_plan"
    ]

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("rollback_plan" in err for err in result.errors)


def test_registry_fails_on_duplicate_workflow_id(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    data["workflows"].append(copy.deepcopy(data["workflows"][0]))

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("duplicate workflow_id" in err for err in result.errors)


def test_registry_fails_when_model_policy_lacks_fallback(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    data["workflows"][0]["modules"][0]["model_policy"]["fallback_required"] = False

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("fallback_required" in err for err in result.errors)


def test_registry_fails_when_module_route_alias_is_missing(tmp_path):
    validator = _load_validator_module()
    data = _valid_registry()
    data["workflows"][0]["modules"][0]["model_policy"]["route_alias"] = "best.missing.alias"

    result = validator.validate_registry(_write_registry(tmp_path, data))

    assert result.status == "FAIL"
    assert any("is not registered" in err for err in result.errors)


def test_model_registry_fails_when_dynamic_routing_enabled(tmp_path):
    validator = _load_validator_module()
    model_data = _valid_model_registry()
    model_data["production_controls"]["dynamic_routing_enabled"] = True

    result = validator.validate_registry(
        _repo_root() / "config" / "workflow_registry.json",
        _repo_root() / "schemas" / "workflow_contract.schema.json",
        _write_model_registry(tmp_path, model_data),
    )

    assert result.status == "FAIL"
    assert any("dynamic_routing_enabled=false" in err for err in result.errors)


def test_model_registry_fails_when_route_alias_uses_unregistered_model(tmp_path):
    validator = _load_validator_module()
    model_data = _valid_model_registry()
    model_data["route_aliases"]["best.goal_calibration.structured"]["primary_model_id"] = "abstract:nope"

    result = validator.validate_registry(
        _repo_root() / "config" / "workflow_registry.json",
        _repo_root() / "schemas" / "workflow_contract.schema.json",
        _write_model_registry(tmp_path, model_data),
    )

    assert result.status == "FAIL"
    assert any("primary_model_id" in err and "not registered" in err for err in result.errors)
