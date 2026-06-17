"""Plaintext-local delivery outbox for enterprise-grade gateway UX."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.delivery_outbox import DeliveryOutbox, drain_delivery_outbox
from gateway.platforms.base import BasePlatformAdapter, Platform, PlatformConfig, SendResult


class _OutboxAdapter(BasePlatformAdapter):
    def __init__(self, outbox: DeliveryOutbox):
        super().__init__(PlatformConfig(), Platform.TELEGRAM)
        self._delivery_outbox = outbox
        self._send_results: list[SendResult] = []
        self._send_calls: list[tuple[str, str]] = []

    async def send(self, chat_id, content, reply_to=None, metadata=None, **kwargs) -> SendResult:
        self._send_calls.append((chat_id, content))
        if self._send_results:
            return self._send_results.pop(0)
        return SendResult(success=True, message_id="ok")

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def send_typing(self, chat_id, metadata=None) -> None:
        pass

    async def get_chat_info(self, chat_id):
        return {"name": "test", "type": "direct", "chat_id": chat_id}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_outbox_persists_plaintext_content_with_ttl_for_redelivery(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.jsonl", ttl_seconds=3600)

    record = outbox.create_pending(
        platform="telegram",
        chat_id="5524172309",
        content="private final reply text",
        reply_to="42",
        metadata={"thread_id": "7", "session_key": "agent:main:telegram:dm:5524172309"},
    )
    outbox.mark_dead_letter(record.delivery_id, error="send_path_degraded", attempt_count=3)

    rows = _jsonl(tmp_path / "outbox.jsonl")
    assert rows[0]["status"] == "pending"
    assert rows[0]["content_storage"] == "plaintext-local"
    assert rows[0]["content"] == "private final reply text"
    assert rows[0]["content_sha256"]
    assert rows[0]["dedupe_key"]
    assert rows[-1]["status"] == "dead_letter"
    assert rows[-1]["last_error"] == "send_path_degraded"
    assert rows[-1]["attempt_count"] == 3


@pytest.mark.asyncio
async def test_send_with_retry_records_sent_outbox_status(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.jsonl", ttl_seconds=3600)
    adapter = _OutboxAdapter(outbox)
    adapter._send_results = [SendResult(success=True, message_id="4986")]

    result = await adapter._send_with_retry("5524172309", "enterprise reply", metadata={"session_key": "s1"})

    assert result.success is True
    rows = _jsonl(tmp_path / "outbox.jsonl")
    assert [row["status"] for row in rows] == ["pending", "sent"]
    assert rows[-1]["sent_message_id"] == "4986"
    assert rows[0]["content"] == "enterprise reply"


@pytest.mark.asyncio
async def test_send_with_retry_records_dead_letter_after_recovery_window(tmp_path, monkeypatch):
    outbox = DeliveryOutbox(tmp_path / "outbox.jsonl", ttl_seconds=3600)
    adapter = _OutboxAdapter(outbox)
    degraded = SendResult(success=False, error="send_path_degraded", retryable=True)
    adapter._send_results = [degraded, degraded, degraded, degraded, SendResult(success=True)]

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr("gateway.platforms.base.asyncio.sleep", _no_sleep)
    result = await adapter._send_with_retry("5524172309", "final reply", metadata={"session_key": "s1"})

    assert result.success is False
    rows = _jsonl(tmp_path / "outbox.jsonl")
    assert rows[0]["status"] == "pending"
    assert rows[-1]["status"] == "dead_letter"
    assert rows[-1]["last_error"] == "send_path_degraded"
    assert rows[-1]["attempt_count"] == 4
    assert rows[0]["content"] == "final reply"


def test_pending_redelivery_excludes_sent_and_expired_rows(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.jsonl", ttl_seconds=3600)
    pending = outbox.create_pending(platform="telegram", chat_id="1", content="pending reply")
    sent = outbox.create_pending(platform="telegram", chat_id="1", content="sent reply")
    expired = outbox.create_pending(platform="telegram", chat_id="1", content="expired reply")
    outbox.mark_sent(sent.delivery_id, sent_message_id="m1")
    outbox._append_update(expired.delivery_id, status="pending", expires_at=0)

    rows = outbox.pending_redeliveries()

    assert [row["delivery_id"] for row in rows] == [pending.delivery_id]
    assert rows[0]["content"] == "pending reply"


@pytest.mark.asyncio
async def test_drain_delivery_outbox_redelivers_pending_reply_and_marks_sent(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.jsonl", ttl_seconds=3600)
    record = outbox.create_pending(
        platform="telegram",
        chat_id="5524172309",
        content="reply recovered after restart",
        reply_to="42",
        metadata={"thread_id": "7", "session_key": "s1"},
    )
    adapter = _OutboxAdapter(outbox)
    adapter._send_results = [SendResult(success=True, message_id="4999")]

    stats = await drain_delivery_outbox({Platform.TELEGRAM: adapter}, outbox=outbox)

    assert stats == {"claimed": 1, "sent": 1, "failed": 0, "skipped": 0}
    assert adapter._send_calls == [("5524172309", "reply recovered after restart")]
    latest = outbox.latest(record.delivery_id)
    assert latest["status"] == "sent"
    assert latest["sent_message_id"] == "4999"


@pytest.mark.asyncio
async def test_drain_delivery_outbox_marks_dead_letter_when_redelivery_fails(tmp_path):
    outbox = DeliveryOutbox(tmp_path / "outbox.jsonl", ttl_seconds=3600)
    record = outbox.create_pending(platform="telegram", chat_id="5524172309", content="cannot deliver")
    adapter = _OutboxAdapter(outbox)
    adapter._send_results = [SendResult(success=False, error="still degraded", retryable=True)]

    stats = await drain_delivery_outbox({Platform.TELEGRAM: adapter}, outbox=outbox)

    assert stats == {"claimed": 1, "sent": 0, "failed": 1, "skipped": 0}
    latest = outbox.latest(record.delivery_id)
    assert latest["status"] == "dead_letter"
    assert latest["last_error"] == "still degraded"
