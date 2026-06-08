from __future__ import annotations

import json
from pathlib import Path

from scripts.session_jsonl_risk_audit import audit_jsonl


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_risk_index_detects_and_links_high_risk_events(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    _write_jsonl(
        source,
        [
            {
                "session_id": "sess-1",
                "message_id": "m1",
                "turn_index": 1,
                "tool_name": "write_file",
                "arguments": {"path": "/Users/charles/.hermes/profiles/other/skills/x/SKILL.md", "content": "ok"},
            },
            {
                "session_id": "sess-1",
                "message_id": "m2",
                "turn_index": 2,
                "tool_name": "terminal",
                "arguments": {"command": "git push origin private/main"},
            },
            {
                "session_id": "sess-1",
                "message_id": "m3",
                "turn_index": 3,
                "tool_name": "browser_navigate",
                "arguments": {"url": "https://example.invalid"},
            },
            {
                "session_id": "sess-1",
                "message_id": "m4",
                "turn_index": 4,
                "content": "Do not paste token=abcd1234SECRET here",
            },
            {
                "session_id": "sess-1",
                "message_id": "m1",
                "turn_index": 1,
                "tool_name": "write_file",
                "arguments": {"path": "/Users/charles/.hermes/profiles/other/skills/x/SKILL.md", "content": "ok"},
            },
        ],
    )

    result = audit_jsonl(source)

    assert result["schema"] == "hermes_session_jsonl_risk_index_v1"
    assert result["parse_errors"] == []
    kinds = {event["kind"] for event in result["events"]}
    assert {"file_write", "cross_profile_write", "shell_command", "browser_action", "secret_like_text"} <= kinds
    assert result["risk_counts_by_kind"]["file_write"] == 1  # duplicate write call is collapsed
    for event in result["events"]:
        assert event["event_id"]
        assert event["session_id"] == "sess-1"
        assert event["message_id"].startswith("m")
        assert "session:sess-1:message:" in event["evidence_ref"]
    cross_profile = [event for event in result["events"] if event["kind"] == "cross_profile_write"]
    assert cross_profile and cross_profile[0]["severity"] == "critical"
    assert cross_profile[0]["turn_index"] == 1


def test_risk_index_empty_for_safe_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "safe.jsonl"
    _write_jsonl(
        source,
        [
            {"session_id": "sess-safe", "message_id": "m1", "turn_index": 1, "role": "user", "content": "hello"},
            {"session_id": "sess-safe", "message_id": "m2", "turn_index": 2, "role": "assistant", "content": "hi"},
        ],
    )

    result = audit_jsonl(source)

    assert result["risk_count"] == 0
    assert result["risk_counts_by_kind"] == {}
    assert result["events"] == []
    assert result["line_count"] == 2


def test_risk_index_normalizes_nested_tool_calls_and_summarizes_skill_mcp(tmp_path: Path) -> None:
    source = tmp_path / "nested.jsonl"
    _write_jsonl(
        source,
        [
            {
                "session_id": "sess-nested",
                "message_id": "m10",
                "turn_index": 7,
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "skill_view",
                                "arguments": json.dumps({"name": "taskpool-control-plane"}),
                            }
                        },
                        {
                            "function": {
                                "name": "mcp_tool_call",
                                "arguments": json.dumps({"server": "browser", "tool": "snapshot"}),
                            }
                        },
                    ]
                },
            }
        ],
    )

    result = audit_jsonl(source)

    assert result["risk_counts_by_kind"]["skill_action"] == 1
    assert result["risk_counts_by_kind"]["mcp_action"] == 1
    assert result["summary"] == {
        "tool_events": 2,
        "skill_events": 1,
        "mcp_events": 1,
        "critical_events": 0,
        "sessions": ["sess-nested"],
    }
    assert {event["message_id"] for event in result["events"]} == {"m10"}
    assert {event["turn_index"] for event in result["events"]} == {7}
