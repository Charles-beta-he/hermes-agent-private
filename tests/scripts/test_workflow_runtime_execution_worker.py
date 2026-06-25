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
                "claimed",
                {
                    "status": "claimed",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_claim_execution_id": "clex_test",
                    "workflow_consolidation_group_id": "contract_acceptance_governance",
                    "workflow_claim_packet_status": "consumed_claimed_not_running",
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
                "claimed",
                {
                    "status": "claimed",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_claim_execution_id": "clex_safety",
                    "workflow_consolidation_group_id": "safety_flags",
                    "workflow_claim_packet_status": "consumed_claimed_not_running",
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


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_execution_worker.py"
    spec = importlib.util.spec_from_file_location("_workflow_runtime_execution_worker", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_execution_write_moves_claimed_to_running_without_model_or_adapter():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.start_runtime_execution(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        group_id="contract_acceptance_governance",
        write=True,
    )

    task = orchestrator.tasks[0]
    assert result["taskpool_write_enabled"] is True
    assert result["taskpool_claim_enabled"] is True
    assert result["runtime_enabled"] is True
    assert result["runtime_worker_enabled"] is True
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert result["runtime_execution"]["previous_status"] == "claimed"
    assert result["runtime_execution"]["new_status"] == "running"
    assert result["runtime_execution"]["execution_status"] == "running_noop_started_not_reviewed"
    assert task.status == "running"
    assert task.fm["status"] == "running"
    assert task.fm["workflow_runtime_execution_status"] == "running_noop_started_not_reviewed"
    assert task.fm["model_call_enabled"] is False
    assert task.fm["adapter_write_enabled"] is False
    assert task.fm["external_side_effects_enabled"] is False
    assert orchestrator.refreshed is True


def test_runtime_execution_preview_does_not_write():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.start_runtime_execution(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        group_id="contract_acceptance_governance",
        write=False,
    )

    assert result["taskpool_write_enabled"] is False
    assert result["runtime_worker_enabled"] is False
    assert result["readback_verified"] is False
    assert orchestrator.tasks[0].status == "claimed"
    assert orchestrator.saved == []


def test_runtime_execution_idempotent_after_running():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    task = orchestrator.tasks[0]
    runtime_execution_id = module.build_runtime_execution_id("rti_test", "clex_test", "contract_acceptance_governance", task.id)
    task.status = "running"
    task.fm["status"] = "running"
    task.fm["workflow_runtime_execution_id"] = runtime_execution_id
    task.fm["workflow_runtime_execution_status"] = "running_noop_started_not_reviewed"

    result = module.start_runtime_execution(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        group_id="contract_acceptance_governance",
        write=True,
    )

    assert result["runtime_execution_id"] == runtime_execution_id
    assert result["taskpool_write_enabled"] is True
    assert result["readback_verified"] is True
    assert result["runtime_execution"]["new_status"] == "running"
    assert orchestrator.saved == []


def test_runtime_execution_exact_task_id_resolves_duplicate_claim_without_guessing():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    orchestrator.tasks.append(
        FakeTask(
            "group-contract-copy",
            Path("/tmp/group-contract-copy.md"),
            "claimed",
            {
                "status": "claimed",
                "workflow_runtime_packet_id": "rti_test",
                "workflow_claim_execution_id": "clex_test",
                "workflow_consolidation_group_id": "contract_acceptance_governance",
                "workflow_claim_packet_status": "consumed_claimed_not_running",
                "claimed_by": "workflow_runtime_claim_executor",
                "requires_human_gate": False,
                "model_call_enabled": False,
                "adapter_write_enabled": False,
                "external_side_effects_enabled": False,
                "evidence": [],
            },
        )
    )

    result = module.start_runtime_execution(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        group_id="contract_acceptance_governance",
        task_id="group-contract-copy",
        write=True,
    )

    assert result["selected_task_id"] == "group-contract-copy"
    assert orchestrator.tasks[0].status == "claimed"
    assert orchestrator.tasks[2].status == "running"
    assert orchestrator.saved == ["group-contract-copy"]


def test_runtime_execution_exact_task_id_must_match_runtime_claim_and_group():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.start_runtime_execution(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_test",
            group_id="contract_acceptance_governance",
            task_id="group-safety",
            write=True,
        )
    except SystemExit as exc:
        assert "does not match runtime_packet_id/claim_execution_id/group_id" in str(exc)
    else:
        raise AssertionError("expected SystemExit for mismatched exact task selector")


def test_runtime_execution_exact_done_task_returns_idempotent_terminal_readback_without_mutation():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    task = orchestrator.tasks[0]
    runtime_execution_id = "rtex_legacy"
    task.status = "done"
    task.fm["status"] = "done"
    task.fm["workflow_runtime_execution_id"] = runtime_execution_id
    task.fm["workflow_runtime_execution_status"] = "module_execution_review_ready"
    task.fm["model_call_enabled"] = True
    task.fm["adapter_write_enabled"] = True

    result = module.start_runtime_execution(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        group_id="contract_acceptance_governance",
        task_id="group-contract",
        write=True,
    )

    assert result["runtime_execution_id"] == runtime_execution_id
    assert result["selected_task_id"] == "group-contract"
    assert result["readback_verified"] is True
    assert result["runtime_execution"]["new_status"] == "done"
    assert result["boundary"] == "runtime_execution_worker_idempotent_terminal_readback"
    assert orchestrator.saved == []


def test_runtime_execution_rejects_human_gated_group():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.start_runtime_execution(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_safety",
            group_id="safety_flags",
            write=True,
        )
    except SystemExit as exc:
        assert "refuses human-gated" in str(exc)
    else:
        raise AssertionError("expected SystemExit for human-gated group")


def test_runtime_execution_rejects_non_claimed_task():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    orchestrator.tasks[0].status = "proposed"
    orchestrator.tasks[0].fm["status"] = "proposed"

    try:
        module.start_runtime_execution(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_test",
            group_id="contract_acceptance_governance",
            write=True,
        )
    except SystemExit as exc:
        assert "expects claimed" in str(exc)
    else:
        raise AssertionError("expected SystemExit for non-claimed task")
