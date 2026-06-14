from __future__ import annotations

import copy

from scripts.workflow_kernel_validate_static_registry import validate_registry
from workflow_kernel.static_registry import MODULE_NAMES, registry_snapshot


def test_static_registry_validator_passes_default_snapshot() -> None:
    report = validate_registry(registry_snapshot())

    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert report["runtime_enabled"] is False
    assert report["write_through_enabled"] is False
    assert report["external_side_effects_enabled"] is False
    assert report["checked"]["module_count"] == len(MODULE_NAMES)
    assert report["errors"] == []


def test_static_registry_validator_fails_if_runtime_flag_enabled() -> None:
    registry = registry_snapshot()
    registry["safety_flags"] = copy.deepcopy(registry["safety_flags"])
    registry["safety_flags"]["runtime_enabled"] = True

    report = validate_registry(registry)

    assert report["status"] == "FAIL"
    assert "safety flag must stay false: runtime_enabled" in report["errors"]


def test_static_registry_validator_requires_module_evidence_and_policy() -> None:
    registry = registry_snapshot()
    registry["modules"] = copy.deepcopy(registry["modules"])
    registry["modules"][0]["evidence_required"] = False
    registry["modules"][0]["model_policy"] = {}

    report = validate_registry(registry)

    assert report["status"] == "FAIL"
    assert any(error.startswith("module evidence_required must be true") for error in report["errors"])
    assert any(error.startswith("module missing model_policy") for error in report["errors"])
