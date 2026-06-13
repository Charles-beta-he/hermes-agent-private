from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FakeTask:
    id: str
    path: Path
    status: str
    fm: dict
    body: str = ""


class FakeOrchestrator:
    def __init__(self):
        self.tasks = [
            FakeTask(
                "group-contract",
                Path("/tmp/group-contract.md"),
                "running",
                {
                    "status": "running",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_claim_execution_id": "clex_test",
                    "workflow_runtime_execution_id": "rtex_test",
                    "workflow_consolidation_group_id": "contract_acceptance_governance",
                    "workflow_runtime_execution_status": "running_noop_started_not_reviewed",
                    "claimed_by": "workflow_runtime_claim_executor",
                    "requires_human_gate": False,
                    "model_call_enabled": False,
                    "adapter_write_enabled": False,
                    "external_side_effects_enabled": False,
                    "evidence": [],
                },
            ),
            FakeTask(
                "group-safety",
                Path("/tmp/group-safety.md"),
                "running",
                {
                    "status": "running",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_claim_execution_id": "clex_safety",
                    "workflow_runtime_execution_id": "rtex_safety",
                    "workflow_consolidation_group_id": "safety_flags",
                    "workflow_runtime_execution_status": "running_noop_started_not_reviewed",
                    "claimed_by": "workflow_runtime_claim_executor",
                    "requires_human_gate": True,
                    "evidence": [],
                },
            ),
        ]
        self.saved = []
        self.refreshed = False

    def iter_tasks(self):
        return list(self.tasks)

    def save_task(self, task):
        self.saved.append(task.id)

    def refresh_derived_task_views(self, reason):
        self.refreshed = True
        return {"status": "OK", "reason": reason}


class FakeValidationResult:
    def __init__(self, status="PASS"):
        self.status = status

    def to_dict(self):
        return {
            "status": self.status,
            "validated_workflows": 1,
            "validated_modules": 5,
            "validated_route_aliases": 5,
            "errors": [] if self.status == "PASS" else ["boom"],
            "warnings": [],
        }


class FakeValidator:
    DEFAULT_REGISTRY = Path("/tmp/workflow_registry.json")

    def __init__(self, status="PASS"):
        self.status = status

    def validate_registry(self, registry):
        assert registry == self.DEFAULT_REGISTRY
        return FakeValidationResult(self.status)


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_module_executor.py"
    spec = importlib.util.spec_from_file_location("_workflow_runtime_module_executor", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_module_write_moves_running_to_review_without_model_or_adapter():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_runtime_module(
        orchestrator,
        FakeValidator(),
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        group_id="contract_acceptance_governance",
        write=True,
    )

    task = orchestrator.tasks[0]
    assert result["taskpool_write_enabled"] is True
    assert result["runtime_enabled"] is True
    assert result["runtime_worker_enabled"] is True
    assert result["runtime_module_enabled"] is True
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert result["module_execution"]["previous_status"] == "running"
    assert result["module_execution"]["new_status"] == "review"
    assert result["module_execution"]["execution_status"] == "module_execution_review_ready"
    assert result["module_execution"]["validator_status"] == "PASS"
    assert task.status == "review"
    assert task.fm["status"] == "review"
    assert task.fm["workflow_module_execution_status"] == "module_execution_review_ready"
    assert task.fm["model_call_enabled"] is False
    assert task.fm["adapter_write_enabled"] is False
    assert task.fm["external_side_effects_enabled"] is False
    assert "command:workflow_contract_validator PASS" in task.fm["evidence"]
    assert orchestrator.refreshed is True


def test_runtime_module_preview_does_not_write():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_runtime_module(
        orchestrator,
        FakeValidator(),
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        group_id="contract_acceptance_governance",
        write=False,
    )

    assert result["taskpool_write_enabled"] is False
    assert result["readback_verified"] is False
    assert orchestrator.tasks[0].status == "running"
    assert orchestrator.saved == []


def test_runtime_module_idempotent_after_review():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    task = orchestrator.tasks[0]
    module_execution_id = module.build_module_execution_id("rtex_test", "contract_acceptance_governance", task.id)
    task.status = "review"
    task.fm["status"] = "review"
    task.fm["workflow_module_execution_id"] = module_execution_id
    task.fm["workflow_module_execution_status"] = "module_execution_review_ready"

    result = module.execute_runtime_module(
        orchestrator,
        FakeValidator(),
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        group_id="contract_acceptance_governance",
        write=True,
    )

    assert result["module_execution_id"] == module_execution_id
    assert result["readback_verified"] is True
    assert result["module_execution"]["new_status"] == "review"
    assert orchestrator.saved == []


def test_runtime_module_rejects_human_gated_group():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_runtime_module(
            orchestrator,
            FakeValidator(),
            runtime_packet_id="rti_test",
            claim_execution_id="clex_safety",
            runtime_execution_id="rtex_safety",
            group_id="safety_flags",
            write=True,
        )
    except SystemExit as exc:
        assert "refuses human-gated" in str(exc)
    else:
        raise AssertionError("expected SystemExit for human-gated group")


def test_runtime_module_rejects_failed_local_validation():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_runtime_module(
            orchestrator,
            FakeValidator(status="FAIL"),
            runtime_packet_id="rti_test",
            claim_execution_id="clex_test",
            runtime_execution_id="rtex_test",
            group_id="contract_acceptance_governance",
            write=True,
        )
    except SystemExit as exc:
        assert "local contract validation module failed" in str(exc)
    else:
        raise AssertionError("expected SystemExit for failed local validation")
