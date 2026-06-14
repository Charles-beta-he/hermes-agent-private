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
    assert report["recommended_next"].startswith("Stage-report cadence")
    assert "external message delivery without exact target/payload/mode" in report["closed_boundaries_without_explicit_authorization"]


def test_next_ladder_requires_authority_for_future_expansion():
    mod = load()
    by_track = {item["track"]: item for item in mod.NEXT_LADDER}
    assert by_track["PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH"]["phase"] == 83
    assert by_track["PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET"]["phase"] == 84


def test_write_report_creates_artifacts(tmp_path):
    mod = load()
    report = mod.build_map()
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
