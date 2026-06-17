"""Metadata-only gateway delivery outbox.

This module records outbound delivery state without persisting message bodies.
It is intentionally small and platform-agnostic: adapters can use it to expose
enterprise-grade delivery observability while keeping content persistence behind
a separate explicit policy gate.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - import safety for isolated tests
    get_hermes_home = None  # type: ignore[assignment]


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: str
    dedupe_key: str
    platform: str
    chat_id: str
    reply_to: str | None
    thread_id: str | None
    session_key: str | None
    content_sha256: str
    content_storage: str
    status: str
    attempt_count: int
    last_error: str | None
    next_attempt_at: float | None
    created_at: float
    expires_at: float
    sent_message_id: str | None = None


class DeliveryOutbox:
    """Append-only metadata outbox for outbound gateway deliveries.

    The outbox never stores raw content. It records only hashes and routing
    metadata so operators can see pending/sent/dead-letter state without making
    private replies durable by default.
    """

    def __init__(self, path: str | os.PathLike[str], *, ttl_seconds: int = 24 * 3600):
        self.path = Path(path).expanduser()
        self.ttl_seconds = int(ttl_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default(cls) -> "DeliveryOutbox":
        home = Path(get_hermes_home()) if get_hermes_home else Path.home() / ".hermes"
        ttl = _int_env("HERMES_GATEWAY_DELIVERY_OUTBOX_TTL_SECONDS", 24 * 3600)
        return cls(home / "state" / "gateway_delivery_outbox.jsonl", ttl_seconds=ttl)

    def create_pending(
        self,
        *,
        platform: str,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryRecord:
        now = time.time()
        metadata = metadata or {}
        thread_id = _string_or_none(metadata.get("thread_id") or metadata.get("message_thread_id"))
        session_key = _string_or_none(metadata.get("session_key") or metadata.get("session_id"))
        content_sha = hashlib.sha256((content or "").encode("utf-8", "surrogatepass")).hexdigest()
        dedupe_basis = "|".join([
            str(platform or ""),
            str(chat_id or ""),
            str(thread_id or ""),
            str(session_key or ""),
            str(reply_to or ""),
            content_sha,
        ])
        dedupe_key = hashlib.sha256(dedupe_basis.encode("utf-8", "surrogatepass")).hexdigest()
        record = DeliveryRecord(
            delivery_id=uuid.uuid4().hex,
            dedupe_key=dedupe_key,
            platform=str(platform or ""),
            chat_id=str(chat_id or ""),
            reply_to=_string_or_none(reply_to),
            thread_id=thread_id,
            session_key=session_key,
            content_sha256=content_sha,
            content_storage="none",
            status="pending",
            attempt_count=0,
            last_error=None,
            next_attempt_at=None,
            created_at=now,
            expires_at=now + self.ttl_seconds,
            sent_message_id=None,
        )
        self._append(record)
        return record

    def mark_sent(self, delivery_id: str, *, sent_message_id: str | None, attempt_count: int = 1) -> None:
        self._append_update(
            delivery_id,
            status="sent",
            sent_message_id=_string_or_none(sent_message_id),
            attempt_count=attempt_count,
            last_error=None,
            next_attempt_at=None,
        )

    def mark_dead_letter(self, delivery_id: str, *, error: str | None, attempt_count: int = 1) -> None:
        self._append_update(
            delivery_id,
            status="dead_letter",
            last_error=_string_or_none(error),
            attempt_count=attempt_count,
            next_attempt_at=None,
        )

    def _append_update(self, delivery_id: str, **updates: Any) -> None:
        base = self.latest(delivery_id)
        if base is None:
            now = time.time()
            row = {
                "delivery_id": delivery_id,
                "dedupe_key": "",
                "platform": "",
                "chat_id": "",
                "reply_to": None,
                "thread_id": None,
                "session_key": None,
                "content_sha256": "",
                "content_storage": "none",
                "status": "unknown",
                "attempt_count": 0,
                "last_error": None,
                "next_attempt_at": None,
                "created_at": now,
                "expires_at": now,
                "sent_message_id": None,
            }
        else:
            row = dict(base)
        row.update(updates)
        self._append_dict(row)

    def latest(self, delivery_id: str) -> dict[str, Any] | None:
        latest = None
        if not self.path.exists():
            return None
        for line in self.path.read_text(errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("delivery_id") == delivery_id:
                latest = row
        return latest

    def _append(self, record: DeliveryRecord) -> None:
        self._append_dict(asdict(record))

    def _append_dict(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default
