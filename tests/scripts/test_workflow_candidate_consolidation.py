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


class FakeOrchestrator:
    def __init__(self, source_tasks: list[FakeTask]):
        self.tasks = list(source_tasks)
        self.created = 0
        self.refresh_called = False

    def iter_tasks(self):
        return list(self.tasks)

    def create_task_note(self, **kwargs):
        self.created += 1
        task_id = f"consolidated-{self.created:03d}"
        fm = dict(kwargs.get("extra_frontmatter") or {})
        fm.update({"status": kwargs["status"], "lane": kwargs["lane"], "priority": kwargs["priority"]})
        task = FakeTask(task_id, Path(f"/tmp/{task_id}.md"), kwargs["status"], fm)
        self.tasks.append(task)
        return {"status": "OK", "id": task_id, "created": str(task.path)}

    def refresh_derived_task_views(self, reason):
        self.refresh_called = True
        return {"status": "OK", "reason": reason}


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_candidate_consolidation.py"
    spec = importlib.util.spec_from_file_location("_workflow_candidate_consolidation", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _source_tasks():
    criteria = [
        "workflow_contract_valid",
        "runtime_disabled_in_phase1",
        "all_modules_have_model_policy",
        "all_modules_have_evidence_required",
        "evidence_prefixes_valid",
        "human_gate_packet_fields_present",
        "module_contract:goal_calibrator.v1",
        "module_contract:source_research.v1",
        "module_contract:decision_synthesizer.v1",
        "module_contract:acceptance_compiler.v1",
        "module_contract:evidence_verifier.v1",
        "model_route:goal_calibrator.v1",
        "model_route:source_research.v1",
        "model_route:decision_synthesizer.v1",
        "model_route:acceptance_compiler.v1",
        "model_route:evidence_verifier.v1",
        "safety_flag:runtime_enabled",
        "safety_flag:write_through_enabled",
        "safety_flag:external_side_effects_enabled",
    ]
    tasks = []
    for index, criterion in enumerate(criteria, start=1):
        tasks.append(
            FakeTask(
                f"source-{index:03d}",
                Path(f"/tmp/source-{index:03d}.md"),
                "proposed",
                {
                    "workflow_import_write_id": "tpwrite_test",
                    "workflow_import_candidate_id": f"candidate-{index:03d}",
                    "workflow_source_criterion": criterion,
                    "requires_human_gate": criterion.startswith("safety_flag:"),
                },
            )
        )
    return tasks


def test_candidate_consolidation_groups_19_sources_into_4_proposed_tasks_without_runtime_authority():
    module = _load_module()
    orchestrator = FakeOrchestrator(_source_tasks())

    result = module.consolidate_candidates(orchestrator, source_write_id="tpwrite_test")

    assert result["taskpool_write_enabled"] is True
    assert result["taskpool_claim_enabled"] is False
    assert result["runtime_enabled"] is False
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["source_candidate_count"] == 19
    assert len(result["groups"]) == 4
    assert len(result["created_tasks"]) == 4
    assert all(task["status"] == "proposed" for task in result["created_tasks"])
    assert all(task["readback_verified"] is True for task in result["created_tasks"])
    assert orchestrator.refresh_called is True


def test_candidate_consolidation_is_idempotent_by_group_id():
    module = _load_module()
    orchestrator = FakeOrchestrator(_source_tasks())

    first = module.consolidate_candidates(orchestrator, source_write_id="tpwrite_test")
    second = module.consolidate_candidates(orchestrator, source_write_id="tpwrite_test")

    assert [t["task_id"] for t in first["created_tasks"]] == [t["task_id"] for t in second["created_tasks"]]
    assert all(task["idempotent_replay"] is True for task in second["created_tasks"])
    assert orchestrator.created == 4


def test_candidate_consolidation_fails_when_no_source_candidates():
    module = _load_module()
    orchestrator = FakeOrchestrator([])

    try:
        module.consolidate_candidates(orchestrator, source_write_id="missing")
    except SystemExit as exc:
        assert "no imported candidate tasks" in str(exc)
    else:
        raise AssertionError("expected SystemExit for missing source candidates")
