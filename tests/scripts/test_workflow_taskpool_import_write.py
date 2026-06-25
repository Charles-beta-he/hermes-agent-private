from __future__ import annotations

import importlib.util
import json
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
    def __init__(self):
        self.tasks: list[FakeTask] = []
        self.refresh_called = False

    def iter_tasks(self):
        return list(self.tasks)

    def create_task_note(self, **kwargs):
        index = len(self.tasks) + 1
        task_id = f"task-{index:03d}"
        fm = dict(kwargs.get("extra_frontmatter") or {})
        fm.update(
            {
                "status": kwargs["status"],
                "lane": kwargs["lane"],
                "priority": kwargs["priority"],
                "architecture_target": kwargs["architecture_target"],
                "workflow_contract": kwargs["workflow_contract"],
            }
        )
        task = FakeTask(task_id, Path(f"/tmp/{task_id}.md"), kwargs["status"], fm)
        self.tasks.append(task)
        return {"status": "OK", "id": task_id, "created": str(task.path)}

    def refresh_derived_task_views(self, reason):
        self.refresh_called = True
        return {"status": "OK", "reason": reason}


def _load_writer_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_taskpool_import_write.py"
    spec = importlib.util.spec_from_file_location("_workflow_taskpool_import_write", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _import_dry_run() -> dict:
    path = (
        _repo_root()
        / "docs"
        / "private"
        / "workflow-kernel-taskpool-import-dry-runs"
        / "decision_brief_pack.run_preview_78eee6afcec1f12a.tpdry_a5678ba9e856a763.taskpool_import_dry_run.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_taskpool_import_write_creates_proposed_canonical_candidates_without_runtime_authority():
    writer = _load_writer_module()
    orchestrator = FakeOrchestrator()

    result = writer.write_candidates(_import_dry_run(), orchestrator, max_candidates=2)

    assert result["taskpool_write_enabled"] is True
    assert result["taskpool_claim_enabled"] is False
    assert result["runtime_enabled"] is False
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["readback_verified"] is True
    assert result["boundary"] == "taskpool_import_write_done_no_claim_no_runtime"
    assert len(result["created_tasks"]) == 2
    assert all(task["status"] == "proposed" for task in result["created_tasks"])
    assert all(task["readback_verified"] is True for task in result["created_tasks"])
    assert orchestrator.refresh_called is True


def test_taskpool_import_write_is_idempotent_by_candidate_id():
    writer = _load_writer_module()
    orchestrator = FakeOrchestrator()
    import_result = _import_dry_run()

    first = writer.write_candidates(import_result, orchestrator, max_candidates=1)
    second = writer.write_candidates(import_result, orchestrator, max_candidates=1)

    assert first["created_tasks"][0]["task_id"] == second["created_tasks"][0]["task_id"]
    assert second["created_tasks"][0]["idempotent_replay"] is True
    assert len(orchestrator.tasks) == 1


def test_taskpool_import_write_treats_existing_archived_candidate_as_closed_idempotent():
    writer = _load_writer_module()
    orchestrator = FakeOrchestrator()
    import_result = _import_dry_run()
    candidate_id = import_result["candidate_tasks"][0]["candidate_id"]
    orchestrator.tasks.append(
        FakeTask(
            "archived-task-001",
            Path("/tmp/archived-task-001.md"),
            "archived",
            {"workflow_import_candidate_id": candidate_id, "status": "archived"},
        )
    )

    result = writer.write_candidates(import_result, orchestrator, max_candidates=1)

    assert result["taskpool_write_enabled"] is True
    assert result["created_tasks"][0]["task_id"] == "archived-task-001"
    assert result["created_tasks"][0]["status"] == "archived"
    assert result["created_tasks"][0]["idempotent_replay"] is True
    assert result["created_tasks"][0]["closed_existing"] is True
    assert len(orchestrator.tasks) == 1


def test_taskpool_import_write_rejects_claim_enabled_dry_run_artifact():
    writer = _load_writer_module()
    data = _import_dry_run()
    data["taskpool_claim_enabled"] = True

    try:
        writer.write_candidates(data, FakeOrchestrator(), max_candidates=1)
    except SystemExit as exc:
        assert "taskpool_claim_enabled=true" in str(exc)
    else:
        raise AssertionError("expected SystemExit for claim-enabled artifact")
