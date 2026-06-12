from __future__ import annotations

import json

from agent.tool_intent_review import (
    SCHEMA_VERSION,
    TOOL_INTENT_REVIEW_SCHEMA,
    review_tool_intent,
    write_review_sidecar,
)


def test_schema_exposes_required_taskpool_evidence_fields() -> None:
    required = set(TOOL_INTENT_REVIEW_SCHEMA["required"])
    assert {
        "tool_name",
        "risk_level",
        "decision",
        "reason_codes",
        "evidence_refs",
        "cron_mode",
        "degraded_flags",
    } <= required
    assert TOOL_INTENT_REVIEW_SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION


def test_read_only_browser_snapshot_allows_observational_call() -> None:
    review = review_tool_intent(
        "browser_snapshot",
        {"url": "https://example.test"},
        intent_summary="observe current page only",
        cron_mode=False,
    )
    payload = review.to_payload()
    assert payload["risk_level"] == "low"
    assert payload["decision"] == "allow"
    assert "read_only_or_observational" in payload["reason_codes"]


def test_terminal_write_without_evidence_does_not_allow_in_cron() -> None:
    review = review_tool_intent(
        "terminal",
        {"command": "rm -rf /tmp/hermes-intent-review-demo"},
        intent_summary="delete generated scratch data",
        cron_mode=True,
    )
    payload = review.to_payload()
    assert payload["risk_level"] == "critical"
    assert payload["decision"] in {"block", "degrade"}
    assert payload["decision"] != "allow"
    assert "structured_pretool_evidence_ref" in payload["missing_evidence"]
    assert "missing_structured_evidence" in payload["reason_codes"]


def test_write_with_structured_evidence_can_allow_and_emit_sidecar(tmp_path) -> None:
    review = review_tool_intent(
        "write_file",
        {"path": "/tmp/hermes-intent-review.txt", "content": "ok"},
        intent_summary="write bounded test artifact",
        evidence_refs=["command:pytest tests/agent/test_tool_intent_review.py"],
        cron_mode=False,
    )
    assert review.decision == "allow"
    assert review.risk_level == "high"
    out = write_review_sidecar(review, tmp_path / "sidecar.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["tool_name"] == "write_file"
    assert payload["evidence_refs"] == ["command:pytest tests/agent/test_tool_intent_review.py"]
