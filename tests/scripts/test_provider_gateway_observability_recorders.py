from __future__ import annotations

import json


def _read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_provider_gateway_recorders_write_redacted_local_jsonl(tmp_path, monkeypatch):
    from scripts.provider_gateway_observability import (
        record_cron_delivery_event,
        record_gateway_delivery_event,
        record_provider_call_event,
    )

    event_log = tmp_path / "events.jsonl"
    monkeypatch.setenv("HERMES_PROVIDER_GATEWAY_OBSERVABILITY_EVENT_LOG", str(event_log))

    assert record_provider_call_event(
        provider="openrouter",
        model="test-model",
        api_mode="chat_completions",
        status="success",
        latency_ms=123,
        input_tokens=10,
        output_tokens=5,
        reasoning_tokens=2,
        cached_tokens=3,
        estimated_cost_usd="0.0001",
        metadata={"Authorization": "Bearer fixture-secret-value", "safe": "ok"},
    )
    assert record_gateway_delivery_event(
        platform="telegram",
        status="failure",
        source="send_message_tool",
        chat_id="chat-fixture-id",
        thread_id="42",
        error_class="GatewayDeliveryError",
        error_summary="failed with token=secret",
    )
    assert record_cron_delivery_event(
        job_id="job-secret-id",
        status="success",
        delivery_target_count=1,
        metadata={"deliver": "origin"},
    )

    rows = _read_rows(event_log)
    assert [row["surface"] for row in rows] == ["provider", "gateway", "cron"]
    assert rows[0]["metadata"]["Authorization"] == "[REDACTED]"
    assert rows[0]["metadata"]["safe"] == "ok"
    assert rows[0]["input_tokens"] == 10
    assert rows[1]["metadata"]["chat_id_hash"].startswith("sha256:")
    assert rows[1]["metadata"]["thread_id_hash"].startswith("sha256:")
    assert "chat-fixture-id" not in event_log.read_text(encoding="utf-8")
    assert rows[2]["metadata"]["job_id_hash"].startswith("sha256:")
    assert rows[2]["metadata"]["delivery_target_count"] == 1


def test_send_message_observers_append_without_message_body(tmp_path, monkeypatch):
    from tools.send_message_tool import _observe_gateway_send_failure, _observe_gateway_send_success

    event_log = tmp_path / "events.jsonl"
    monkeypatch.setenv("HERMES_PROVIDER_GATEWAY_OBSERVABILITY_EVENT_LOG", str(event_log))

    _observe_gateway_send_success("telegram", chat_id="chat-fixture-id", thread_id="42")
    _observe_gateway_send_failure(
        "telegram",
        chat_id="chat-fixture-id",
        thread_id="42",
        error="Authorization: Bearer fixture-secret-value failed",
    )

    raw = event_log.read_text(encoding="utf-8")
    rows = _read_rows(event_log)
    assert [row["status"] for row in rows] == ["success", "failure"]
    assert all(row["operation"] == "delivery" for row in rows)
    assert "chat-fixture-id" not in raw
    assert "fixture-secret-value" not in raw
    assert "[REDACTED]" in raw
