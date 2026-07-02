"""Persistent cron delivery compensation queue.

Stores generated cron delivery payloads when the platform send fails after the
job output was already produced.  The queue is profile-local under
``HERMES_HOME/cron`` and is intentionally small/standalone so recovery can run
from the scheduler, CLI, or a no-agent watchdog without re-running the LLM job.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hermes_constants import get_hermes_home
from utils import atomic_replace

logger = logging.getLogger(__name__)

CRON_DIR = get_hermes_home().resolve() / "cron"
PENDING_DELIVERIES_FILE = CRON_DIR / "pending_deliveries.json"
DELIVERY_RECEIPTS_FILE = CRON_DIR / "delivery_receipts.jsonl"

_SCHEMA_VERSION = "hermes_cron_delivery_queue_v1"


def _utc_ts(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).isoformat()


def _canonical_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": str(target.get("platform") or "").lower(),
        "chat_id": str(target.get("chat_id") or ""),
        "thread_id": None if target.get("thread_id") is None else str(target.get("thread_id")),
    }


def _pending_id(*, job_id: str, target: dict[str, Any], content: str, output_file: str | None) -> str:
    payload = {
        "job_id": str(job_id),
        "target": _canonical_target(target),
        "content_hash": hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest(),
        "output_file": output_file or "",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return "cron-pending-" + digest[:20]


def _load_store() -> dict[str, Any]:
    try:
        raw = json.loads(PENDING_DELIVERIES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": _SCHEMA_VERSION, "items": []}
    except Exception as exc:
        logger.warning("failed to read cron pending delivery queue: %s", exc)
        return {"schema_version": _SCHEMA_VERSION, "items": []}
    if not isinstance(raw, dict):
        return {"schema_version": _SCHEMA_VERSION, "items": []}
    items = raw.get("items")
    if not isinstance(items, list):
        raw["items"] = []
    raw.setdefault("schema_version", _SCHEMA_VERSION)
    return raw


def _save_store(store: dict[str, Any]) -> None:
    PENDING_DELIVERIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PENDING_DELIVERIES_FILE.with_name(PENDING_DELIVERIES_FILE.name + ".tmp")
    tmp_path.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    atomic_replace(tmp_path, PENDING_DELIVERIES_FILE)


def _append_receipt(receipt: dict[str, Any]) -> None:
    DELIVERY_RECEIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DELIVERY_RECEIPTS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def enqueue_pending_delivery(
    *,
    job: dict[str, Any],
    target: dict[str, Any],
    content: str,
    error: str,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    """Create or update one pending delivery record.

    The id is deterministic for job+target+content+output_file, so repeated
    scheduler failures update the same pending item instead of fan-out dupes.
    """
    now = time.time()
    job_id = str(job.get("id") or "")
    output_text = str(output_file) if output_file is not None else None
    target_norm = _canonical_target(target)
    item_id = _pending_id(job_id=job_id, target=target_norm, content=content, output_file=output_text)
    store = _load_store()
    items = store.setdefault("items", [])
    for item in items:
        if str(item.get("id")) == item_id and item.get("status") == "pending":
            item["last_error"] = str(error or "")[:500]
            item["updated_at"] = _utc_ts(now)
            item["updated_at_epoch"] = now
            _save_store(store)
            return dict(item)

    item = {
        "schema_version": _SCHEMA_VERSION,
        "id": item_id,
        "status": "pending",
        "job_id": job_id,
        "job_name": str(job.get("name") or job_id),
        "target": target_norm,
        "content": str(content or ""),
        "output_file": output_text,
        "attempts": 0,
        "last_error": str(error or "")[:500],
        "created_at": _utc_ts(now),
        "created_at_epoch": now,
        "updated_at": _utc_ts(now),
        "updated_at_epoch": now,
    }
    items.append(item)
    _save_store(store)
    return dict(item)


def list_pending_deliveries() -> list[dict[str, Any]]:
    store = _load_store()
    return [dict(item) for item in store.get("items", []) if item.get("status") == "pending"]


def _default_sender(item: dict[str, Any]) -> dict[str, Any]:
    import asyncio
    import concurrent.futures

    from gateway.config import Platform, load_gateway_config
    from tools.send_message_tool import _send_to_platform

    target = item.get("target") or {}
    platform = Platform(str(target.get("platform") or "").lower())
    cfg = load_gateway_config()
    pconfig = cfg.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        return {"success": False, "error": f"platform '{platform.value}' not configured/enabled"}
    coro = _send_to_platform(
        platform,
        pconfig,
        str(target.get("chat_id") or ""),
        str(item.get("content") or ""),
        thread_id=target.get("thread_id"),
    )
    try:
        return asyncio.run(coro)
    except RuntimeError:
        coro.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _send_to_platform(
                platform,
                pconfig,
                str(target.get("chat_id") or ""),
                str(item.get("content") or ""),
                thread_id=target.get("thread_id"),
            )).result(timeout=30)


def drain_pending_deliveries(
    *,
    sender: Callable[[dict[str, Any]], dict[str, Any] | str] | None = None,
    limit: int = 5,
    max_attempts: int = 10,
) -> list[dict[str, Any]]:
    """Attempt queued deliveries once, recording a receipt for every attempt."""
    if sender is None:
        sender = _default_sender
    limit = max(1, int(limit))
    max_attempts = max(1, int(max_attempts))
    now = time.time()
    emitted: list[dict[str, Any]] = []
    store = _load_store()
    items = store.setdefault("items", [])
    kept: list[dict[str, Any]] = []
    attempted = 0

    for item in items:
        if item.get("status") != "pending":
            kept.append(item)
            continue
        if attempted >= limit or int(item.get("attempts") or 0) >= max_attempts:
            kept.append(item)
            continue
        attempted += 1
        error_text = None
        result: dict[str, Any]
        try:
            raw = sender(dict(item))
            result = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception as exc:  # noqa: BLE001 - receipt should capture any send boundary failure
            result = {"success": False, "error": f"{exc.__class__.__name__}: {exc}"}
        success = bool(result.get("success")) and not result.get("error")
        if not success:
            error_text = str(result.get("error") or "send failed")[:500]
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = error_text
            item["updated_at"] = _utc_ts(now)
            item["updated_at_epoch"] = now
            kept.append(item)
        receipt = {
            "schema_version": _SCHEMA_VERSION,
            "id": "cron-delivery-receipt-" + hashlib.sha256(json.dumps({
                "pending_delivery_id": item.get("id"),
                "attempt": int(item.get("attempts") or 0),
                "ts": now,
            }, sort_keys=True).encode("utf-8")).hexdigest()[:20],
            "pending_delivery_id": item.get("id"),
            "job_id": item.get("job_id"),
            "target": item.get("target"),
            "status": "sent_verified" if success else "send_failed",
            "created_at": _utc_ts(now),
            "created_at_epoch": now,
            "message_id": result.get("message_id") or result.get("id"),
            "error_type": error_text,
            "external_send_executed": success,
            "payload_body_persisted": True,
            "token_value_persisted": False,
        }
        _append_receipt(receipt)
        emitted.append(receipt)

    store["items"] = kept
    _save_store(store)
    return emitted
