from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_executor_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_executor_stub.py"
    spec = importlib.util.spec_from_file_location("_workflow_executor_stub", script)
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


def test_executor_stub_builds_local_reversible_dry_run_without_side_effect_authority():
    executor = _load_executor_module()

    result = executor.build_dry_run_result(_acceptance_bundle())

    assert result["dry_run_only"] is True
    assert result["local_reversible_only"] is True
    assert result["runtime_enabled"] is False
    assert result["taskpool_claim_enabled"] is False
    assert result["model_call_enabled"] is False
    assert result["adapter_write_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["boundary"] == "local_reversible_executor_stub_only_no_taskpool_no_model_no_adapter"
    assert len(result["steps"]) == 19
    assert any(step["status"] == "blocked_by_boundary" for step in result["steps"])
    assert all("proof:executor_stub_no_runtime_side_effect" in step["evidence"] for step in result["steps"])


def test_executor_stub_writes_local_artifact(tmp_path):
    executor = _load_executor_module()
    result = executor.build_dry_run_result(_acceptance_bundle())

    path = executor.write_result(tmp_path, result)

    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["result_id"] == result["result_id"]
    assert written["boundary"] == "local_reversible_executor_stub_only_no_taskpool_no_model_no_adapter"


def test_executor_stub_rejects_non_dry_run_bundle():
    executor = _load_executor_module()
    bundle = _acceptance_bundle()
    bundle["dry_run_only"] = False

    try:
        executor.build_dry_run_result(bundle)
    except SystemExit as exc:
        assert "frozen and dry_run_only" in str(exc)
    else:
        raise AssertionError("expected SystemExit for non dry-run bundle")
