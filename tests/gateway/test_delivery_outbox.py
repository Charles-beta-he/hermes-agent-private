"""Metadata-only delivery outbox for enterprise-grade gateway UX."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.delivery_outbox import DeliveryOutbox
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


def test_outbox_records_metadata_without_content(tmp_path):
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
    assert rows[0]["content_storage"] == "none"
    assert rows[0]["content_sha256"]
    assert rows[0]["dedupe_key"]
    serialized = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    assert "private final reply text" not in serialized
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
    assert "enterprise reply" not in "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


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
    assert "final reply" not in "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
