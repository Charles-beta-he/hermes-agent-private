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
    assert report["safe_default_track"] == "A_READ_ONLY_CONTINUE"
    assert report["recommended_next"].startswith("Phase39")
    assert "mutate ~/.hermes/cron/jobs.json" in report["closed_boundaries_without_explicit_authorization"]


def test_next_ladder_requires_authority_for_runtime_crossing():
    mod = load()
    by_track = {item["track"]: item for item in mod.NEXT_LADDER}
    assert by_track["A_READ_ONLY_CONTINUE"]["authority_needed"] == "none"
    assert by_track["B_SINGLE_WRAPPER_RUNTIME_ENABLE_DRY_RUN"]["authority_needed"] == "explicit B"
    assert by_track["C_SINGLE_WRAPPER_SCHEDULER_NATIVE_ROLLOUT"]["authority_needed"] == "explicit C"


def test_write_report_creates_artifacts(tmp_path):
    mod = load()
    report = mod.build_map()
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
