from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_sequence_map.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_sequence_map", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sequence_is_sorted_and_dependencies_are_prior():
    mod = load()
    errors = mod.validate_sequence(mod.PHASES)
    assert errors == []


def test_build_map_has_current_boundary_and_safe_default():
    mod = load()
    report = mod.build_map()
    assert report["status"] == "WORKFLOW_SEQUENCE_MAP_READY"
    assert report["safe_default_track"] == "EXTERNAL_DISPATCH_GOVERNED_EXPANSION"
    assert "LoopRuntimeContract adoption gate" in report["recommended_next"]
    assert "external message delivery without exact target/payload/mode" in report["closed_boundaries_without_explicit_authorization"]


def test_next_ladder_requires_authority_for_future_expansion():
    mod = load()
    report = mod.build_map()
    assert mod.NEXT_LADDER == []
    assert report["next_ladder"] == []
    assert "LoopRuntimeContract adoption gate" in report["recommended_next"]


def test_sequence_map_includes_loop_runtime_contract_adoption_phase():
    mod = load()
    report = mod.build_map()
    phase93 = next(p for p in report["phases"] if p["phase"] == 93)
    assert phase93["name"] == "LoopRuntimeContract Adoption Gate"
    assert phase93["depends_on"] == [92]
    assert phase93["authority"] == "local dry-run contract doctor only"
    assert "loop_runtime_enabled=false" in phase93["gate"]
    assert phase93["next_unlocked"] == []


def test_write_report_creates_artifacts(tmp_path):
    mod = load()
    report = mod.build_map()
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
