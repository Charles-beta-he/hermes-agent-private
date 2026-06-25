from __future__ import annotations

import importlib.util
import subprocess
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
                    "workflow_review_done_status": "done_local_review_closed",
                    "workflow_consolidation_group_id": "contract_acceptance_governance",
                    "requires_human_gate": False,
                    "adapter_write_enabled": False,
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
                    "workflow_review_done_status": "done_local_review_closed",
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
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_model_call_gate.py"
    spec = importlib.util.spec_from_file_location("_workflow_runtime_model_call_gate", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ok_runner(prompt, *, provider, model, timeout):
    assert "MODEL_CALL_GATE_OK" in prompt
    assert provider == "test-provider"
    assert model == "test-model"
    return subprocess.CompletedProcess(["hermes"], 0, stdout="MODEL_CALL_GATE_OK\n", stderr="")


def bad_marker_runner(prompt, *, provider, model, timeout):
    return subprocess.CompletedProcess(["hermes"], 0, stdout="WRONG\n", stderr="")


def failing_runner(prompt, *, provider, model, timeout):
    return subprocess.CompletedProcess(["hermes"], 2, stdout="", stderr="provider failed")


def test_model_call_write_records_read_only_artifacts(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_model_call_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        group_id="contract_acceptance_governance",
        provider="test-provider",
        model="test-model",
        output_dir=tmp_path,
        write=True,
        runner=ok_runner,
    )

    task = orchestrator.tasks[0]
    assert result["model_call_enabled"] is True
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert result["verification"]["status"] == "PASS"
    assert result["response_artifact"]["non_empty"] is True
    assert result["response_artifact"]["expected_marker_present"] is True
    assert Path(result["prompt_artifact"]["path"]).exists()
    assert Path(result["response_artifact"]["path"]).exists()
    assert task.fm["workflow_model_call_status"] == "model_call_completed_read_only"
    assert task.fm["model_call_enabled"] is True
    assert task.fm["adapter_write_enabled"] is False
    assert task.fm["external_side_effects_enabled"] is False
    assert "proof:runtime_model_call_gate_read_only_no_adapter_no_external" in task.fm["evidence"]
    assert orchestrator.refreshed is True


def test_model_call_preview_calls_model_but_does_not_write_task(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.execute_model_call_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        group_id="contract_acceptance_governance",
        provider="test-provider",
        model="test-model",
        output_dir=tmp_path,
        write=False,
        runner=ok_runner,
    )

    assert result["taskpool_write_enabled"] is False
    assert result["readback_verified"] is False
    assert orchestrator.saved == []
    assert "workflow_model_call_status" not in orchestrator.tasks[0].fm


def test_model_call_idempotent_after_write(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()
    first = module.execute_model_call_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        group_id="contract_acceptance_governance",
        provider="test-provider",
        model="test-model",
        output_dir=tmp_path,
        write=True,
        runner=ok_runner,
    )

    def should_not_run(prompt, *, provider, model, timeout):
        raise AssertionError("idempotent path should not call model again")

    second = module.execute_model_call_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        group_id="contract_acceptance_governance",
        provider="test-provider",
        model="test-model",
        output_dir=tmp_path,
        write=True,
        runner=should_not_run,
    )

    assert second["model_call_id"] == first["model_call_id"]
    assert second["readback_verified"] is True


def test_model_call_exact_task_id_resolves_duplicate_done_chain_and_allows_existing_adapter_flag(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()
    existing_id = "mcall_legacy"
    orchestrator.tasks[0].fm.update({
        "workflow_model_call_id": existing_id,
        "workflow_model_call_status": "model_call_completed_read_only",
        "workflow_model_call_record": {"response_artifact": {"sha256": "abc"}},
        "model_call_enabled": True,
        "adapter_write_enabled": True,
    })
    orchestrator.tasks.append(
        FakeTask("group-contract-copy", Path("/tmp/group-contract-copy.md"), "done", dict(orchestrator.tasks[0].fm))
    )

    def should_not_run(prompt, *, provider, model, timeout):
        raise AssertionError("idempotent exact readback should not call model")

    result = module.execute_model_call_gate(
        orchestrator,
        runtime_packet_id="rti_test",
        claim_execution_id="clex_test",
        runtime_execution_id="rtex_test",
        module_execution_id="modex_test",
        done_execution_id="done_test",
        group_id="contract_acceptance_governance",
        task_id="group-contract",
        provider="test-provider",
        model="test-model",
        output_dir=tmp_path,
        write=True,
        runner=should_not_run,
    )

    assert result["model_call_id"] == existing_id
    assert result["selected_task_id"] == "group-contract"
    assert result["readback_verified"] is True
    assert result["boundary"] == "runtime_model_call_idempotent_readback_before_external_write"
    assert orchestrator.saved == []


def test_model_call_rejects_human_gated_group(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_model_call_gate(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_safety",
            runtime_execution_id="rtex_safety",
            module_execution_id="modex_safety",
            done_execution_id="done_safety",
            group_id="safety_flags",
            provider="test-provider",
            model="test-model",
            output_dir=tmp_path,
            write=True,
            runner=ok_runner,
        )
    except SystemExit as exc:
        assert "refuses human-gated" in str(exc)
    else:
        raise AssertionError("expected human-gated rejection")


def test_model_call_rejects_missing_marker(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_model_call_gate(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_test",
            runtime_execution_id="rtex_test",
            module_execution_id="modex_test",
            done_execution_id="done_test",
            group_id="contract_acceptance_governance",
            provider="test-provider",
            model="test-model",
            output_dir=tmp_path,
            write=True,
            runner=bad_marker_runner,
        )
    except SystemExit as exc:
        assert "missing expected marker" in str(exc)
    else:
        raise AssertionError("expected marker rejection")


def test_model_call_records_failure_artifact(tmp_path):
    module = _load_module()
    orchestrator = FakeOrchestrator()

    try:
        module.execute_model_call_gate(
            orchestrator,
            runtime_packet_id="rti_test",
            claim_execution_id="clex_test",
            runtime_execution_id="rtex_test",
            module_execution_id="modex_test",
            done_execution_id="done_test",
            group_id="contract_acceptance_governance",
            provider="test-provider",
            model="test-model",
            output_dir=tmp_path,
            write=True,
            runner=failing_runner,
        )
    except SystemExit as exc:
        assert "model call failed" in str(exc)
    else:
        raise AssertionError("expected model call failure")
    assert list(tmp_path.glob("*.failure.json"))
