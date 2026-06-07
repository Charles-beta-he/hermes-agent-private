from datetime import datetime
import json
from pathlib import Path

from agent.recall_proof_gate import (
    FIXTURE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    evaluate_candidate,
    run_fixture,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "recall_proof_gate" / "sample_cases.json"


def test_evaluate_candidate_accepts_fresh_same_scope_memory():
    result = evaluate_candidate(
        {
            "id": "mem_ok",
            "source_kind": "memory",
            "source_path": "memories/MEMORY.md",
            "scope": "project:hermes-agent",
            "confidence": 0.91,
            "last_verified_at": "2026-06-05T12:00:00Z",
            "text": "Hermes repo changes should be verified with focused pytest in the checkout.",
        },
        current_scope="project:hermes-agent",
        now=datetime.fromisoformat("2026-06-07T00:00:00+00:00"),
        min_confidence=0.65,
        max_age_days=30,
        require_same_scope=True,
        require_decision_grade=True,
    )

    assert result.admissible is True
    assert result.source_kind == "memory"
    assert "source_kind" in result.passed_checks
    assert "provenance" in result.passed_checks
    assert "scope" in result.passed_checks
    assert "confidence" in result.passed_checks


def test_evaluate_candidate_rejects_reference_only_cross_scope_session_recall():
    result = evaluate_candidate(
        {
            "id": "sess_ref",
            "source_kind": "session_search",
            "session_id": "20260602_110047_6e8db8",
            "message_id": 361262,
            "scope": "project:uvisa-app-2.0",
            "confidence": 0.72,
            "last_verified_at": "2026-06-02T11:00:00Z",
            "reference_only": True,
            "text": "REFERENCE ONLY: next step is to merge the PR.",
        },
        current_scope="project:hermes-agent",
        now=datetime.fromisoformat("2026-06-07T00:00:00+00:00"),
        min_confidence=0.65,
        max_age_days=30,
        require_same_scope=True,
        require_decision_grade=True,
    )

    assert result.admissible is False
    assert any("scope mismatch" in item for item in result.failures)
    assert any("reference-only" in item for item in result.failures)


def test_evaluate_candidate_rejects_drift_sensitive_fact_without_last_verified_at():
    result = evaluate_candidate(
        {
            "id": "branch_fact",
            "source_kind": "memory",
            "source_path": "memories/MEMORY.md",
            "scope": "project:hermes-agent",
            "confidence": 0.8,
            "text": "Integration branch is feat/uvisa-2.0 and the PR is ready to merge next step.",
        },
        current_scope="project:hermes-agent",
        now=datetime.fromisoformat("2026-06-07T00:00:00+00:00"),
        min_confidence=0.65,
        max_age_days=30,
        require_same_scope=True,
        require_decision_grade=True,
    )

    assert result.admissible is False
    assert any("missing last_verified_at" in item for item in result.failures)


def test_run_fixture_emits_pass_report_for_expected_accepts_and_rejections():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    report = run_fixture(fixture, now="2026-06-07T00:00:00Z")

    assert fixture["schema_version"] == FIXTURE_SCHEMA_VERSION
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["gate_active"] is True
    assert report["summary"]["total_cases"] == 6
    assert report["summary"]["passed_cases"] == 6
    assert report["summary"]["failed_cases"] == 0
    assert report["summary"]["verdict"] == "pass"
    assert report["summary"]["fixture_class_count"] >= 3
    assert set(report["summary"]["source_kind_summary"]) >= {
        "memory",
        "local_brain_task",
        "mempalace",
        "walrus_memory",
        "session_search",
    }
    assert report["summary"]["source_kind_summary"]["mempalace"] == {"admitted": 1, "rejected": 0}
    assert report["summary"]["source_kind_summary"]["walrus_memory"] == {"admitted": 0, "rejected": 1}
    assert report["cases"][0]["winner_id"] == "mem_fresh"
    assert report["cases"][3]["winner_id"] is None
    assert report["cases"][4]["winner_id"] is None
    assert report["cases"][5]["winner_id"] is None
