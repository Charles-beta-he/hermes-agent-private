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
                "done",
                {
                    "status": "done",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_claim_execution_id": "clex_test",
                    "workflow_runtime_execution_id": "rtex_test",
                    "workflow_module_execution_id": "modex_test",
                    "workflow_review_done_execution_id": "done_test",
                    "workflow_model_call_id": "mcall_test",
                    "workflow_model_call_status": "model_call_completed_read_only",
                    "workflow_model_call_record": {"response_artifact": {"sha256": "abc"}},
                    "workflow_consolidation_group_id": "contract_acceptance_governance",
                    "requires_human_gate": False,
                    "external_side_effects_enabled": False,
                    "evidence": [],
                },
            ),
            FakeTask(
                "group-safety",
                Path("/tmp/group-safety.md"),
                "done",
                {
                    "status": "done",
                    "workflow_runtime_packet_id": "rti_test",
                    "workflow_claim_execution_id": "clex_safety",
                    "workflow_runtime_execution_id": "rtex_safety",
                    "workflow_module_execution_id": "modex_safety",
                    "workflow_review_done_execution_id": "done_safety",
                    "workflow_model_call_id": "mcall_safety",
                    "workflow_model_call_status": "model_call_completed_read_only",
                    "workflow_consolidation_group_id": "safety_flags",
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
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_adapter_write_gate.py"
    spec = importlib.util.spec_from_file_location("_workflow_runtime_adapter_write_gate", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_adapter_write_records_local_outbox_and_task_annotation(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_adapter_write_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        model_call_id="mcall_test",
        group_id="contract_acceptance_governance",
        output_dir=tmp_path,
        write=True,
    )

    task = orchestrator.tasks[0]
    assert result["adapter_write_enabled"] is True
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert result["verification"]["status"] == "PASS"
    assert result["outbox_artifact"]["readback_digest_match"] is True
    assert Path(result["outbox_artifact"]["path"]).exists()
    assert task.fm["workflow_adapter_write_status"] == "adapter_write_local_outbox_simulated"
    assert task.fm["adapter_write_enabled"] is True
    assert task.fm["external_side_effects_enabled"] is False
    assert "proof:runtime_adapter_write_local_outbox_no_external_side_effect" in task.fm["evidence"]
    assert orchestrator.refreshed is True


def test_adapter_write_preview_creates_outbox_but_does_not_write_task(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_adapter_write_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        model_call_id="mcall_test",
        group_id="contract_acceptance_governance",
        output_dir=tmp_path,
        write=False,
    )

    assert result["taskpool_write_enabled"] is False
    assert result["readback_verified"] is False
    assert Path(result["outbox_artifact"]["path"]).exists()
    assert orchestrator.saved == []
    assert "workflow_adapter_write_status" not in orchestrator.tasks[0].fm


def test_adapter_write_idempotent_after_write(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()
    first = module.execute_adapter_write_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        model_call_id="mcall_test",
        group_id="contract_acceptance_governance",
        output_dir=tmp_path,
        write=True,
    )

    second = module.execute_adapter_write_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        model_call_id="mcall_test",
        group_id="contract_acceptance_governance",
        output_dir=tmp_path,
        write=True,
    )

    assert second["adapter_write_id"] == first["adapter_write_id"]
    assert second["readback_verified"] is True


def test_adapter_write_rejects_human_gated_group(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_adapter_write_gate(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_safety",
            runtime_execution_id="rtex_safety",
            module_execution_id="modex_safety",
            done_execution_id="done_safety",
            model_call_id="mcall_safety",
            group_id="safety_flags",
            output_dir=tmp_path,
            write=True,
        )
    except SystemExit as exc:
        assert "refuses human-gated" in str(exc)
    else:
        raise AssertionError("expected human-gated rejection")


def test_adapter_write_rejects_external_side_effect_already_enabled(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()
    orchestrator.tasks[0].fm["external_side_effects_enabled"] = True

    try:
        module.execute_adapter_write_gate(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_test",
            runtime_execution_id="rtex_test",
            module_execution_id="modex_test",
            done_execution_id="done_test",
            model_call_id="mcall_test",
            group_id="contract_acceptance_governance",
            output_dir=tmp_path,
            write=True,
        )
    except SystemExit as exc:
        assert "external side effects" in str(exc)
    else:
        raise AssertionError("expected external-side-effect rejection")
