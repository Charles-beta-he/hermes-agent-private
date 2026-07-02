from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load():
    p = Path(__file__).resolve().parents[2] / "scripts" / "provider_gateway_observability.py"
    spec = importlib.util.spec_from_file_location("provider_gateway_observability", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_normalize_event_redacts_secret_metadata_and_preserves_safe_fields():
    mod = load()
    event = mod.normalize_event(
        {
            "surface": "provider",
            "operation": "chat_completion",
            "provider": "openai",
            "model": "gpt-test",
            "status": "failure",
            "latency_ms": "123",
            "input_tokens": 10,
            "estimated_cost_usd": "0.0012",
            "metadata": {
                "route": "fallback-a",
                "Authorization": "Bearer secret-token",
                "nested": {"api_key": "sk-test-secret", "status": "safe"},
            },
        },
        now="2026-07-02T00:00:00Z",
    )

    assert event["schema_version"] == mod.SCHEMA_VERSION
    assert event["event_id"].startswith("pgw_")
    assert event["latency_ms"] == 123
    assert event["input_tokens"] == 10
    assert event["estimated_cost_usd"] == "0.0012"
    assert event["metadata"]["route"] == "fallback-a"
    assert event["metadata"]["Authorization"] == "[REDACTED]"
    assert event["metadata"]["nested"]["api_key"] == "[REDACTED]"
    assert event["metadata"]["nested"]["status"] == "safe"


def test_validate_event_rejects_unsupported_surface_and_status():
    mod = load()
    try:
        mod.normalize_event({"surface": "billing", "status": "success"})
    except ValueError as exc:
        assert "unsupported surface" in str(exc)
    else:
        raise AssertionError("unsupported surface should fail")

    try:
        mod.normalize_event({"surface": "provider", "status": "raw_secret"})
    except ValueError as exc:
        assert "unsupported status" in str(exc)
    else:
        raise AssertionError("unsupported status should fail")


def test_append_and_doctor_projection_are_local_jsonl_only(tmp_path):
    mod = load()
    event_log = tmp_path / "events.jsonl"
    mod.append_event(
        {
            "surface": "gateway",
            "operation": "telegram_send",
            "provider": "telegram",
            "status": "success",
            "latency_ms": 50,
        },
        event_log,
    )
    mod.append_event(
        {
            "surface": "provider",
            "operation": "chat_completion",
            "provider": "openrouter",
            "model": "test-model",
            "status": "timeout",
            "error_class": "TimeoutError",
            "metadata": {"refresh_token": "secret"},
        },
        event_log,
    )

    rows = [json.loads(line) for line in event_log.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[1]["metadata"]["refresh_token"] == "[REDACTED]"

    projection = mod.build_doctor_projection(event_log)
    assert projection["status"] == "DEGRADED"
    assert projection["summary"]["event_count"] == 2
    assert projection["summary"]["status_counts"] == {"success": 1, "timeout": 1}
    assert projection["summary"]["provider_counts"]["openrouter"] == 1
    assert "no network calls" in projection["safe_boundaries"]
    assert "no provider/account mutation" in projection["safe_boundaries"]


def test_doctor_projection_no_data_boundary(tmp_path):
    mod = load()
    projection = mod.build_doctor_projection(tmp_path / "missing.jsonl")
    assert projection["status"] == "NO_DATA"
    assert projection["summary"]["event_count"] == 0
    assert projection["next_actions"] == [
        "wire provider/gateway call sites to append redacted local events before using this as health evidence"
    ]


def test_summarize_events_counts_tokens_and_latency():
    mod = load()
    events = [
        mod.normalize_event({"surface": "provider", "status": "success", "latency_ms": 100, "input_tokens": 3}),
        mod.normalize_event({"surface": "provider", "status": "failure", "latency_ms": 300, "output_tokens": 5, "error_class": "APIError"}),
    ]
    summary = mod.summarize_events(events)
    assert summary["latency_ms_by_surface"]["provider"] == {"count": 2, "avg": 200, "max": 300}
    assert summary["token_totals"]["input_tokens"] == 3
    assert summary["token_totals"]["output_tokens"] == 5
    assert summary["failure_class_counts"] == {"APIError": 1}
