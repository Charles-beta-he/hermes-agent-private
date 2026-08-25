"""Contract tests for calendar-month usage attribution."""

import json
from pathlib import Path

import pytest

from agent import insights


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "usage"
    / "calendar-month-synthetic.json"
)


class BodyGuard(dict):
    """Session mapping that fails if aggregation probes message content."""

    _FORBIDDEN = {"body", "content", "messages", "prompt", "tool_payload"}

    def get(self, key, default=None):
        if key in self._FORBIDDEN:
            raise AssertionError(f"message body field read: {key}")
        return super().get(key, default)

    def __getitem__(self, key):
        if key in self._FORBIDDEN:
            raise AssertionError(f"message body field read: {key}")
        return super().__getitem__(key)


def test_calendar_month_usage_attribution_contract(monkeypatch):
    rows = [BodyGuard(row) for row in json.loads(FIXTURE.read_text())]
    monkeypatch.setattr(
        insights,
        "estimate_usage_cost",
        lambda *args, **kwargs: pytest.fail("aggregation made a model/API call"),
    )

    aggregate = getattr(insights, "aggregate_calendar_month_usage", None)
    assert callable(aggregate), "calendar-month aggregation API is missing"

    report = aggregate(rows, year=2026, month=7, timezone_name="Asia/Shanghai")
    assert report == {
        "window": {
            "start": "2026-07-01T00:00:00+08:00",
            "end": "2026-08-01T00:00:00+08:00",
        },
        "timezone": "Asia/Shanghai",
        "sessions": 3,
        "api_calls": 3,
        "tool_calls": 5,
        "fresh_input_tokens": 400,
        "cache_read_tokens": 300,
        "cache_write_tokens": 50,
        "output_tokens": 250,
        "reasoning_tokens": 65,
        "total_tokens": 1000,
        "token_class_percentages": {
            "fresh_input": 40.0,
            "cache_read": 30.0,
            "cache_write": 5.0,
            "output": 25.0,
        },
        "sources": [
            {
                "source": "cli",
                "sessions": 2,
                "api_calls": 3,
                "tool_calls": 5,
                "fresh_input_tokens": 400,
                "cache_read_tokens": 300,
                "cache_write_tokens": 50,
                "output_tokens": 250,
                "reasoning_tokens": 65,
                "total_tokens": 1000,
            },
            {
                "source": "gateway",
                "sessions": 1,
                "api_calls": 0,
                "tool_calls": 0,
                "fresh_input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            },
        ],
        "models": [
            {
                "model": "model-alpha",
                "sessions": 2,
                "api_calls": 3,
                "tool_calls": 5,
                "fresh_input_tokens": 400,
                "cache_read_tokens": 300,
                "cache_write_tokens": 50,
                "output_tokens": 250,
                "reasoning_tokens": 65,
                "total_tokens": 1000,
            },
            {
                "model": "model-beta",
                "sessions": 1,
                "api_calls": 0,
                "tool_calls": 0,
                "fresh_input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            },
        ],
    }
    assert sum(report["token_class_percentages"].values()) == pytest.approx(100.0)
    assert report["total_tokens"] == (
        report["fresh_input_tokens"]
        + report["output_tokens"]
        + report["cache_read_tokens"]
        + report["cache_write_tokens"]
    )
    assert report["reasoning_tokens"] not in {0, report["total_tokens"]}
    assert json.dumps(report, sort_keys=True) == json.dumps(
        aggregate(rows, year=2026, month=7, timezone_name="Asia/Shanghai"),
        sort_keys=True,
    )

    empty = aggregate(rows, year=2026, month=9, timezone_name="Asia/Shanghai")
    assert empty["sessions"] == 0
    assert empty["total_tokens"] == 0
    assert empty["reasoning_tokens"] == 0
    assert sum(empty["token_class_percentages"].values()) == 0
    assert empty["sources"] == []
    assert empty["models"] == []
