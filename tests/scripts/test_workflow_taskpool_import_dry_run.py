from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_importer_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_taskpool_import_dry_run.py"
    spec = importlib.util.spec_from_file_location("_workflow_taskpool_import_dry_run", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_executor_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_executor_stub.py"
    spec = importlib.util.spec_from_file_location("_workflow_executor_stub_for_import", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _acceptance_bundle() -> dict:
    path = (
        _repo_root()
        / "docs"
        / "private"
        / "workflow-kernel-acceptance"
        / "decision_brief_pack.run_preview_78eee6afcec1f12a.acc_3c811427ac76ce88.acceptance.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _executor_result() -> dict:
    executor = _load_executor_module()
    return executor.build_dry_run_result(_acceptance_bundle())


def test_taskpool_import_dry_run_generates_candidate_tasks_without_write_authority():
    importer = _load_importer_module()

    result = importer.build_import_dry_run(_executor_result())

    assert result["dry_run_only"] is True
    assert result["taskpool_write_enabled"] is False
    assert result["taskpool_claim_enabled"] is False
    assert result["runtime_enabled"] is False
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["boundary"] == "taskpool_import_dry_run_only_no_canonical_write_no_claim"
    assert len(result["candidate_tasks"]) == 19
    assert all(candidate["write_enabled"] is False for candidate in result["candidate_tasks"])
    assert all(candidate["frontmatter_preview"]["dry_run_candidate_only"] is True for candidate in result["candidate_tasks"])
    assert any(candidate["status_candidate"] == "blocked" for candidate in result["candidate_tasks"])


def test_taskpool_import_dry_run_writes_only_local_artifact(tmp_path):
    importer = _load_importer_module()
    result = importer.build_import_dry_run(_executor_result())

    path = importer.write_import_dry_run(tmp_path, result)

    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["import_id"] == result["import_id"]
    assert written["taskpool_write_enabled"] is False
    assert written["human_gate_packet"]["recommended_default"] == "continue_taskpool_import_dry_run_only"


def test_taskpool_import_dry_run_rejects_runtime_enabled_executor_result():
    importer = _load_importer_module()
    executor_result = _executor_result()
    executor_result["runtime_enabled"] = True

    try:
        importer.build_import_dry_run(executor_result)
    except SystemExit as exc:
        assert "runtime_enabled=true" in str(exc)
    else:
        raise AssertionError("expected SystemExit for runtime-enabled executor result")
