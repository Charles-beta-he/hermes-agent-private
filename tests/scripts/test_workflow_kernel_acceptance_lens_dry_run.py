from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_kernel_acceptance_lens_dry_run.py"
    spec = importlib.util.spec_from_file_location("_workflow_kernel_acceptance_lens_dry_run", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture() -> dict:
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "private"
        / "workflow-kernel-acceptance-lens-dry-run"
        / "agent_native_five_layer_fixture.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_acceptance_lens_dry_run_accepts_five_layers_without_runtime_enablement():
    module = _load_module()

    result = module.build_acceptance_lens_dry_run(_fixture())

    assert result["dry_run_only"] is True
    assert result["local_reversible_only"] is True
    assert result["runtime_enabled"] is False
    assert result["write_through_enabled"] is False
    assert result["external_side_effects_enabled"] is False
    assert result["task_lifecycle_mutation_enabled"] is False
    assert result["model_call_enabled"] is False
    assert result["verdict"] == "implementation_ready_not_runtime_enabled"
    assert result["boundary"] == "acceptance_lens_dry_run_only_no_runtime_no_task_lifecycle_mutation"
    assert result["failures"] == []
    assert all(layer["ok"] is True for layer in result["layer_results"].values())
    assert all(ok is True for ok in result["flag_results"].values())


def test_acceptance_lens_dry_run_writes_only_local_receipt(tmp_path):
    module = _load_module()
    result = module.build_acceptance_lens_dry_run(_fixture())

    path = module.write_dry_run(tmp_path, result)

    assert path.exists()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["result_id"] == result["result_id"]
    assert written["verdict"] == "implementation_ready_not_runtime_enabled"
    assert written["external_side_effects_enabled"] is False


def test_acceptance_lens_dry_run_blocks_forbidden_true_flag():
    module = _load_module()
    fixture = _fixture()
    fixture["candidate"]["safety_flags"]["runtime_enabled"] = True

    result = module.build_acceptance_lens_dry_run(fixture)

    assert result["verdict"] == "blocked_by_acceptance_lens"
    assert "forbidden_flag_not_false:runtime_enabled" in result["failures"]


def test_acceptance_lens_dry_run_blocks_missing_layer_evidence():
    module = _load_module()
    fixture = _fixture()
    fixture["candidate"]["layers"]["tool_artifact_loop"]["evidence_refs"] = []

    result = module.build_acceptance_lens_dry_run(fixture)

    assert result["verdict"] == "blocked_by_acceptance_lens"
    assert "layer_not_satisfied:tool_artifact_loop" in result["failures"]
