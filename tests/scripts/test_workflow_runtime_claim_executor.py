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
                "proposed",
                {
                    "status": "proposed",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_consolidation_group_id": "contract_acceptance_governance",
                    "workflow_claim_packet_status": "prepared_not_claimed",
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
                "proposed",
                {
                    "status": "proposed",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_consolidation_group_id": "safety_flags",
                    "workflow_claim_packet_status": "prepared_not_claimed",
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


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_claim_executor.py"
    spec = importlib.util.spec_from_file_location("_workflow_runtime_claim_executor", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_claim_write_consumes_one_packet_without_running_or_adapter():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_runtime_claim(
        orchestrator,
        runtime_packet_id="rti_test",
        group_id="contract_acceptance_governance",
        write=True,
    )

    task = orchestrator.tasks[0]
    assert result["taskpool_write_enabled"] is True
    assert result["taskpool_claim_enabled"] is True
    assert result["runtime_enabled"] is True
    assert result["runtime_record_only"] is False
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert result["claim_execution"]["previous_status"] == "proposed"
    assert result["claim_execution"]["new_status"] == "claimed"
    assert result["claim_execution"]["claim_packet_status"] == "consumed_claimed_not_running"
    assert task.status == "claimed"
    assert task.fm["status"] == "claimed"
    assert task.fm["workflow_claim_packet_status"] == "consumed_claimed_not_running"
    assert task.fm["claimed_by"] == "workflow_runtime_claim_executor"
    assert task.fm["model_call_enabled"] is False
    assert task.fm["adapter_write_enabled"] is False
    assert task.fm["external_side_effects_enabled"] is False
    assert orchestrator.refreshed is True


def test_runtime_claim_preview_does_not_write():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_runtime_claim(
        orchestrator,
        runtime_packet_id="rti_test",
        group_id="contract_acceptance_governance",
        write=False,
    )

    assert result["taskpool_write_enabled"] is False
    assert result["taskpool_claim_enabled"] is False
    assert result["runtime_record_only"] is True
    assert result["readback_verified"] is False
    assert orchestrator.tasks[0].status == "proposed"
    assert orchestrator.saved == []


def test_runtime_claim_idempotent_after_claimed():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    task = orchestrator.tasks[0]
    claim_id = module.build_claim_execution_id("rti_test", "contract_acceptance_governance", task.id)
    task.status = "claimed"
    task.fm["status"] = "claimed"
    task.fm["workflow_claim_execution_id"] = claim_id
    task.fm["workflow_claim_packet_status"] = "consumed_claimed_not_running"

    result = module.execute_runtime_claim(
        orchestrator,
        runtime_packet_id="rti_test",
        group_id="contract_acceptance_governance",
        write=True,
    )

    assert result["claim_execution_id"] == claim_id
    assert result["taskpool_write_enabled"] is True
    assert result["readback_verified"] is True
    assert result["claim_execution"]["new_status"] == "claimed"
    assert orchestrator.saved == []


def test_runtime_claim_exact_task_id_resolves_duplicate_group_without_guessing():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    orchestrator.tasks.append(
        FakeTask(
            "group-contract-copy",
            Path("/tmp/group-contract-copy.md"),
            "proposed",
            {
                "status": "proposed",
                "workflow_runtime_packet_id": "rti_test",
                "workflow_consolidation_group_id": "contract_acceptance_governance",
                "workflow_claim_packet_status": "prepared_not_claimed",
                "requires_human_gate": False,
                "model_call_enabled": False,
                "adapter_write_enabled": False,
                "external_side_effects_enabled": False,
                "evidence": [],
            },
        )
    )

    result = module.execute_runtime_claim(
        orchestrator,
        runtime_packet_id="rti_test",
        group_id="contract_acceptance_governance",
        task_id="group-contract-copy",
        write=True,
    )

    assert result["selected_task_id"] == "group-contract-copy"
    assert orchestrator.tasks[0].status == "proposed"
    assert orchestrator.tasks[2].status == "claimed"
    assert orchestrator.saved == ["group-contract-copy"]


def test_runtime_claim_exact_task_id_must_match_runtime_packet_and_group():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_runtime_claim(
            orchestrator,
            runtime_packet_id="rti_test",
            group_id="contract_acceptance_governance",
            task_id="group-safety",
            write=True,
        )
    except SystemExit as exc:
        assert "does not match runtime_packet_id/group_id" in str(exc)
    else:
        raise AssertionError("expected SystemExit for mismatched exact task selector")


def test_runtime_claim_exact_done_task_returns_idempotent_terminal_readback_without_mutation():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    task = orchestrator.tasks[0]
    claim_id = "clex_legacy"
    task.status = "done"
    task.fm["status"] = "done"
    task.fm["workflow_claim_execution_id"] = claim_id
    task.fm["workflow_claim_packet_status"] = "consumed_claimed_not_running"
    task.fm["workflow_runtime_execution_id"] = "rtex_existing"

    result = module.execute_runtime_claim(
        orchestrator,
        runtime_packet_id="rti_test",
        group_id="contract_acceptance_governance",
        task_id="group-contract",
        write=True,
    )

    assert result["claim_execution_id"] == claim_id
    assert result["selected_task_id"] == "group-contract"
    assert result["readback_verified"] is True
    assert result["claim_execution"]["new_status"] == "done"
    assert result["boundary"] == "runtime_claim_executor_idempotent_terminal_readback"
    assert orchestrator.saved == []


def test_runtime_claim_rejects_human_gated_group():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_runtime_claim(
            orchestrator,
            runtime_packet_id="rti_test",
            group_id="safety_flags",
            write=True,
        )
    except SystemExit as exc:
        assert "refuses human-gated" in str(exc)
    else:
        raise AssertionError("expected SystemExit for human-gated group")


def test_runtime_claim_rejects_non_proposed_task():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    orchestrator.tasks[0].status = "pending"
    orchestrator.tasks[0].fm["status"] = "pending"

    try:
        module.execute_runtime_claim(
            orchestrator,
            runtime_packet_id="rti_test",
            group_id="contract_acceptance_governance",
            write=True,
        )
    except SystemExit as exc:
        assert "expects proposed" in str(exc)
    else:
        raise AssertionError("expected SystemExit for non-proposed task")
