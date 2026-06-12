from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_acceptance_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "acceptance_compiler.py"
    spec = importlib.util.spec_from_file_location("_acceptance_compiler", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _projection_dir() -> Path:
    return _repo_root() / "docs" / "private" / "workflow-kernel-projections"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_projection_inputs():
    run_id = "run_preview_78eee6afcec1f12a"
    lockfile = _load_json(_projection_dir() / f"decision_brief_pack.{run_id}.run_lockfile.json")
    traces = _load_json(_projection_dir() / f"decision_brief_pack.{run_id}.route_traces.json")
    return lockfile, traces


def test_acceptance_compiler_freezes_dry_run_bundle_without_execution_authority():
    compiler = _load_acceptance_module()
    lockfile, traces = _load_projection_inputs()

    bundle = compiler.compile_acceptance_bundle(lockfile, traces)

    assert bundle["frozen"] is True
    assert bundle["dry_run_only"] is True
    assert bundle["runtime_enabled"] is False
    assert bundle["write_through_enabled"] is False
    assert bundle["external_side_effects_enabled"] is False
    assert bundle["boundary"] == "dry_run_acceptance_only_no_execution"
    assert len(bundle["criteria"]) >= len(lockfile["acceptance_criteria"])
    criterion_ids = {criterion["criterion_id"] for criterion in bundle["criteria"]}
    assert "safety_flag:runtime_enabled" in criterion_ids
    assert "model_route:goal_calibrator.v1" in criterion_ids
    assert bundle["human_gate_packet_template"]["recommended_default"] == "continue_dry_run_only"


def test_acceptance_compiler_writes_local_artifact(tmp_path):
    compiler = _load_acceptance_module()
    lockfile, traces = _load_projection_inputs()
    bundle = compiler.compile_acceptance_bundle(lockfile, traces)

    path = compiler._write_bundle(tmp_path, bundle)

    assert path.exists()
    written = _load_json(path)
    assert written["bundle_id"] == bundle["bundle_id"]
    assert written["boundary"] == "dry_run_acceptance_only_no_execution"
