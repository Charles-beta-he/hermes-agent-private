from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_projection_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "workflow_projection.py"
    spec = importlib.util.spec_from_file_location("_workflow_projection", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_projection_builds_lockfile_and_route_traces_without_runtime_authority():
    projection_mod = _load_projection_module()
    projection = projection_mod.build_projection(
        _load_json(_repo_root() / "config" / "workflow_registry.json"),
        _load_json(_repo_root() / "config" / "model_registry.json"),
        "decision_brief_pack",
    )

    assert projection["status"] == "PASS"
    assert projection["projection_only"] is True
    assert projection["operator_summary"]["runtime_enabled"] is False
    assert projection["operator_summary"]["write_through_enabled"] is False
    assert projection["operator_summary"]["external_side_effects_enabled"] is False
    lockfile = projection["run_lockfile"]
    assert lockfile["runtime_enabled"] is False
    assert lockfile["write_through_enabled"] is False
    assert lockfile["external_side_effects_enabled"] is False
    assert lockfile["run_mode"] == "pinned"
    assert len(lockfile["modules"]) == 5
    assert len(projection["route_traces"]) == 5
    assert all(trace["run_mode"] == "pinned" for trace in projection["route_traces"])
    assert all(trace["fallback_chain"] for trace in projection["route_traces"])
    assert all(trace["eval_gate"]["passed"] for trace in projection["route_traces"])


def test_projection_write_creates_read_only_artifacts(tmp_path):
    projection_mod = _load_projection_module()
    projection = projection_mod.build_projection(
        _load_json(_repo_root() / "config" / "workflow_registry.json"),
        _load_json(_repo_root() / "config" / "model_registry.json"),
        "decision_brief_pack",
    )

    written = projection_mod._write_projection(tmp_path, projection)

    assert set(written) == {"run_lockfile", "route_traces", "operator_summary"}
    for path in written.values():
        assert Path(path).exists()
    summary = _load_json(Path(written["operator_summary"]))
    assert summary["boundary"] == "read_only_projection_no_task_claim_no_model_call_no_adapter_write"
