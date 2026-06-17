"""Plaintext-local gateway delivery outbox.

This module records outbound delivery state and, when explicitly authorized by
policy, stores the generated reply body for bounded automatic redelivery after a
gateway restart or transient send-path failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - import safety for isolated tests
    get_hermes_home = None  # type: ignore[assignment]


DEFAULT_TTL_SECONDS = 24 * 3600


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
    content: str | None
    status: str
    attempt_count: int
    last_error: str | None
    next_attempt_at: float | None
    created_at: float
    expires_at: float
    sent_message_id: str | None = None


class DeliveryOutbox:
    """Append-only plaintext-local outbox for outbound gateway deliveries.

    Records are append-only for crash tolerance: state transitions append a full
    replacement row for the same delivery_id. ``latest_rows()`` collapses them
    in memory. ``prune_expired()`` rewrites the file to enforce the content TTL.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        content_storage: str = "plaintext-local",
    ):
        self.path = Path(path).expanduser()
        self.ttl_seconds = int(ttl_seconds)
        self.content_storage = _content_storage(content_storage)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default(cls) -> "DeliveryOutbox":
        home = Path(get_hermes_home()) if get_hermes_home else Path.home() / ".hermes"
        ttl = _int_env("HERMES_GATEWAY_DELIVERY_OUTBOX_TTL_SECONDS", DEFAULT_TTL_SECONDS)
        storage = os.environ.get("HERMES_GATEWAY_DELIVERY_OUTBOX_CONTENT_STORAGE", "plaintext-local")
        return cls(
            home / "state" / "gateway_delivery_outbox.jsonl",
            ttl_seconds=ttl,
            content_storage=storage,
        )

    def create_pending(
        self,
        *,
        platform: str,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryRecord:
        # TTL enforcement before adding new private content keeps the file from
        # growing into a permanent transcript store.
        self.prune_expired()
        now = time.time()
        metadata = metadata or {}
        thread_id = _string_or_none(metadata.get("thread_id") or metadata.get("message_thread_id"))
        session_key = _string_or_none(metadata.get("session_key") or metadata.get("session_id"))
        content_text = content or ""
        content_sha = hashlib.sha256(content_text.encode("utf-8", "surrogatepass")).hexdigest()
        dedupe_basis = "|".join([
            str(platform or ""),
            str(chat_id or ""),
            str(thread_id or ""),
            str(session_key or ""),
            str(reply_to or ""),
            content_sha,
        ])
        dedupe_key = hashlib.sha256(dedupe_basis.encode("utf-8", "surrogatepass")).hexdigest()
        storage = self.content_storage
        record = DeliveryRecord(
            delivery_id=uuid.uuid4().hex,
            dedupe_key=dedupe_key,
            platform=str(platform or ""),
            chat_id=str(chat_id or ""),
            reply_to=_string_or_none(reply_to),
            thread_id=thread_id,
            session_key=session_key,
            content_sha256=content_sha,
            content_storage=storage,
            content=content_text if storage == "plaintext-local" else None,
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

    def mark_sending(self, delivery_id: str, *, attempt_count: int | None = None) -> None:
        updates: dict[str, Any] = {"status": "sending", "last_error": None}
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        self._append_update(delivery_id, **updates)

    def mark_pending(self, delivery_id: str, *, error: str | None = None, attempt_count: int | None = None) -> None:
        updates: dict[str, Any] = {"status": "pending", "last_error": _string_or_none(error)}
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        self._append_update(delivery_id, **updates)

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

    def pending_redeliveries(self, *, now: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.prune_expired(now=now)
        now = time.time() if now is None else now
        rows: list[dict[str, Any]] = []
        for row in self.latest_rows().values():
            if row.get("status") != "pending":
                continue
            if float(row.get("expires_at") or 0) <= now:
                continue
            if row.get("content_storage") != "plaintext-local" or not row.get("content"):
                continue
            rows.append(row)
        rows.sort(key=lambda r: float(r.get("created_at") or 0))
        return rows[: max(0, int(limit))]

    def latest_rows(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self._iter_rows():
            delivery_id = row.get("delivery_id")
            if delivery_id:
                latest[str(delivery_id)] = row
        return latest

    def latest(self, delivery_id: str) -> dict[str, Any] | None:
        return self.latest_rows().get(delivery_id)

    def prune_expired(self, *, now: float | None = None) -> int:
        """Rewrite the append-only file to remove expired latest records.

        Returns the number of latest delivery records pruned. If the file is
        missing, returns 0. The rewrite keeps one latest row per live delivery,
        which also compacts old state-transition rows and removes old plaintext.
        """
        if not self.path.exists():
            return 0
        now = time.time() if now is None else now
        latest = self.latest_rows()
        live = [row for row in latest.values() if float(row.get("expires_at") or 0) > now]
        removed = len(latest) - len(live)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            for row in sorted(live, key=lambda r: float(r.get("created_at") or 0)):
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        tmp.replace(self.path)
        return removed

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
                "content_storage": self.content_storage,
                "content": None,
                "status": "unknown",
                "attempt_count": 0,
                "last_error": None,
                "next_attempt_at": None,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
                "sent_message_id": None,
            }
        else:
            row = dict(base)
        row.update(updates)
        self._append_dict(row)

    def _append(self, record: DeliveryRecord) -> None:
        self._append_dict(asdict(record))

    def _append_dict(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    def _iter_rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row.setdefault("content", None)
                row.setdefault("content_storage", "none")
                rows.append(row)
        return rows


async def drain_delivery_outbox(
    adapters: Mapping[Any, Any],
    *,
    outbox: DeliveryOutbox | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Redeliver pending plaintext-local records through live adapters.

    This is intentionally adapter-agnostic and calls ``adapter.send`` directly
    instead of ``_send_with_retry`` to avoid creating a second outbox row for the
    same recovered payload. Callers can run it on gateway startup after platform
    adapters are connected.
    """
    outbox = outbox or DeliveryOutbox.default()
    stats = {"claimed": 0, "sent": 0, "failed": 0, "skipped": 0}
    for row in outbox.pending_redeliveries(limit=limit):
        delivery_id = str(row.get("delivery_id") or "")
        platform = str(row.get("platform") or "")
        adapter = _adapter_for_platform(adapters, platform)
        if not delivery_id or adapter is None:
            stats["skipped"] += 1
            continue
        content = row.get("content")
        if not isinstance(content, str) or not content:
            stats["skipped"] += 1
            continue
        attempt_count = int(row.get("attempt_count") or 0) + 1
        outbox.mark_sending(delivery_id, attempt_count=attempt_count)
        stats["claimed"] += 1
        metadata = {
            "thread_id": row.get("thread_id"),
            "message_thread_id": row.get("thread_id"),
            "session_key": row.get("session_key"),
            "outbox_redelivery": True,
            "delivery_id": delivery_id,
        }
        try:
            result = await adapter.send(
                chat_id=str(row.get("chat_id") or ""),
                content=content,
                reply_to=row.get("reply_to"),
                metadata=metadata,
            )
        except Exception as exc:
            outbox.mark_dead_letter(delivery_id, error=str(exc), attempt_count=attempt_count)
            stats["failed"] += 1
            continue
        if getattr(result, "success", False):
            outbox.mark_sent(
                delivery_id,
                sent_message_id=str(getattr(result, "message_id", "") or "") or None,
                attempt_count=attempt_count,
            )
            stats["sent"] += 1
        else:
            outbox.mark_dead_letter(
                delivery_id,
                error=str(getattr(result, "error", "") or "redelivery_failed"),
                attempt_count=attempt_count,
            )
            stats["failed"] += 1
    return stats


def _adapter_for_platform(adapters: Mapping[Any, Any], platform: str) -> Any | None:
    wanted = platform.lower()
    for key, adapter in adapters.items():
        key_value = str(getattr(key, "value", key)).lower()
        adapter_name = str(getattr(adapter, "name", "")).lower()
        if key_value == wanted or adapter_name == wanted:
            return adapter
    return None


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


def _content_storage(value: str | None) -> str:
    normalized = (value or "plaintext-local").strip().lower()
    if normalized in {"0", "false", "none", "metadata-only"}:
        return "none"
    if normalized in {"plaintext", "plaintext-local", "local"}:
        return "plaintext-local"
    return "plaintext-local"
