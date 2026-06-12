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
                f"group-{idx}",
                Path(f"/tmp/group-{idx}.md"),
                "proposed",
                {
                    "workflow_consolidation_id": "tpcon_test",
                    "workflow_consolidation_group_id": group,
                    "evidence": [],
                },
            )
            for idx, group in enumerate(["contract", "modules", "routes", "safety"], start=1)
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
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_integration.py"
    spec = importlib.util.spec_from_file_location("_workflow_runtime_integration", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_integration_write_records_claim_packets_without_claiming():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.record_runtime_integration(orchestrator, consolidation_id="tpcon_test", write=True)

    assert result["runtime_enabled"] is True
    assert result["runtime_record_only"] is True
    assert result["taskpool_write_enabled"] is True
    assert result["taskpool_claim_enabled"] is False
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert len(result["runtime_targets"]) == 4
    assert all(target["claim_packet_status"] == "prepared_not_claimed" for target in result["runtime_targets"])
    assert all(task.fm["workflow_claim_packet_status"] == "prepared_not_claimed" for task in orchestrator.tasks)
    assert all(task.status == "proposed" for task in orchestrator.tasks)
    assert orchestrator.refreshed is True


def test_runtime_integration_preview_does_not_write():
    module = _load_module()
    orchestrator = FakeOrchestrator()

    result = module.record_runtime_integration(orchestrator, consolidation_id="tpcon_test", write=False)

    assert result["taskpool_write_enabled"] is False
    assert result["runtime_enabled"] is True
    assert result["runtime_record_only"] is True
    assert result["readback_verified"] is False
    assert orchestrator.saved == []
    assert all("workflow_runtime_packet_id" not in task.fm for task in orchestrator.tasks)


def test_runtime_integration_rejects_non_proposed_task():
    module = _load_module()
    orchestrator = FakeOrchestrator()
    orchestrator.tasks[0].status = "pending"
    orchestrator.tasks[0].fm["status"] = "pending"

    try:
        module.record_runtime_integration(orchestrator, consolidation_id="tpcon_test", write=True)
    except SystemExit as exc:
        assert "expects proposed" in str(exc)
    else:
        raise AssertionError("expected SystemExit for non-proposed task")
