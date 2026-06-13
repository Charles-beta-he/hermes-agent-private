"""Append-only JSONL ledger helpers for workflow-kernel runtime readiness."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from workflow_kernel.contracts import utc_now
from workflow_kernel.io import canonical_json_bytes


def _event_id(event_type: str, idempotency_key: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes({"event_type": event_type, "idempotency_key": idempotency_key, "payload": payload})).hexdigest()
    return f"wk_evt_{digest[:16]}"


def read_events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def append_event(path: str | Path, *, event_type: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    event_id = _event_id(event_type, idempotency_key, payload)
    for event in read_events(target):
        if event.get("event_id") == event_id:
            return event
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": utc_now(),
        "idempotency_key": idempotency_key,
        "payload": payload,
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event
