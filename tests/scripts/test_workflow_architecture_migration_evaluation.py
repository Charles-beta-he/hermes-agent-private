from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[2] / "scripts" / "workflow_architecture_migration_evaluation.py"
    spec = importlib.util.spec_from_file_location("workflow_architecture_migration_evaluation", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_report_selects_incremental_strangler_path():
    mod = load()
    report = mod.build_report()
    assert report["status"] == "MIGRATION_RECOMMENDED_INCREMENTAL_STRANGLER"
    assert report["selected_path"] == "A"
    paths = {p["id"]: p for p in report["candidate_paths"]}
    assert paths["A"]["score"] > paths["B"]["score"]
    assert "no global autorun enablement" in report["hard_boundaries"]


def test_report_contains_phase_26_scaffold_next_step():
    mod = load()
    report = mod.build_report()
    assert "Phase 26" in report["next_phase"]
    assert any(lane["phase"] == "26" and "scaffold" in lane["name"] for lane in report["migration_lanes"])
    assert any(module.startswith("kernel.runtime:") for module in report["target_architecture"]["core_modules"])


def test_write_report_outputs_json_markdown_and_hash(tmp_path):
    mod = load()
    report = mod.build_report()
    artifacts = mod.write_report(report, tmp_path)
    json_path = Path(artifacts["json"])
    md_path = Path(artifacts["md"])
    assert json_path.exists()
    assert md_path.exists()
    assert len(artifacts["sha256"]) == 64
    saved = json.loads(json_path.read_text())
    assert saved["selected_path"] == "A"
    assert "Candidate Paths" in md_path.read_text()
