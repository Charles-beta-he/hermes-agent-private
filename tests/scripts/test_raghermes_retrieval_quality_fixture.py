from __future__ import annotations

import json
from pathlib import Path

from scripts import raghermes_retrieval_quality_fixture as quality


def test_fixture_retrieval_quality_passes_thresholds():
    report = quality.build_report(quality.DEFAULT_FIXTURE)

    assert report["boundary"] == "fixture_only_temp_sqlite_no_live_db_write_no_external_side_effect"
    assert report["fixture_sha256"].startswith("sha256:")
    assert report["report_hash"].startswith("sha256:")
    assert report["evaluation"]["status"] == "PASS"
    assert report["evaluation"]["case_count"] == 30
    assert report["evaluation"]["metrics"]["MRR@10"] >= 0.65
    assert report["evaluation"]["metrics"]["Recall@5"] >= 0.75
    assert report["evaluation"]["metrics"]["AuthorityViolationRate"] == 0.0
    assert report["evaluation"]["metrics"]["ProvenanceCompleteness"] == 1.0


def test_fixture_retrieval_quality_has_per_case_provenance():
    report = quality.build_report(quality.DEFAULT_FIXTURE)
    cases = report["evaluation"]["per_case"]

    assert {case["id"] for case in cases} >= {"lb-rag-001", "lb-rag-015", "lb-rag-030"}
    assert all(case["rank"] is not None for case in cases)
    assert all(case["provenance_complete"] for case in cases)
    assert not any(case["authority_violation"] for case in cases)


def test_write_report_artifact_round_trips(tmp_path: Path):
    report = quality.build_report(quality.DEFAULT_FIXTURE)
    path = quality.write_report(report, tmp_path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["report_hash"] == report["report_hash"]
    assert path.name.startswith("raghermes_retrieval_quality_fixture.")
