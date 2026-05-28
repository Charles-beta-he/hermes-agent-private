from __future__ import annotations

from pathlib import Path

from scripts.taskpool_security_regression_gate import build_report, load_scores


FIXTURES = Path(__file__).parent / "fixtures" / "taskpool_security_regression"


def test_offline_fixtures_cover_required_failure_labels():
    scores = load_scores([FIXTURES])
    report = build_report(scores)

    assert report["fixture_count"] == 4
    assert report["missing_fixture_labels"] == []
    assert set(report["covered_fixture_labels"]) == {
        "prompt_injection",
        "tool_poisoning",
        "memory_poisoning",
        "false_positive_over_investigation",
    }


def test_score_json_contains_task_failure_evidence_pass_and_gate_fields():
    scores = load_scores([FIXTURES])
    report = build_report(scores)

    for score in report["scores"]:
        assert score["task_id"]
        assert score["failure_label"]
        assert score["evidence_path"].startswith("tests/fixtures/taskpool_security_regression/")
        assert "pass" in score
        assert score["status"] in {"pass", "fail"}
        assert score["recommended_gate"] in {
            "manual_evidence_required",
            "tool_output_quarantine",
            "memory_evidence_review",
            "human_evidence_required",
        }


def test_taskpool_dry_run_blocks_auto_completion_and_requires_human_evidence():
    scores = load_scores([FIXTURES])
    report = build_report(scores, dry_run=True)

    assert report["summary"]["auto_completion_blocked"] is True
    assert report["summary"]["requires_human_evidence"] is True
    assert report["taskpool_dry_run"] == {
        "mode": "read_only",
        "would_mark_done": False,
        "auto_completion_blocked": True,
        "requires_human_evidence": True,
        "blocking_failure_labels": [
            "false_positive_over_investigation",
            "memory_poisoning",
            "prompt_injection",
            "tool_poisoning",
        ],
    }
