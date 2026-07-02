#!/usr/bin/env python3
"""Local-only Hermes provider/gateway observability projection.

Inspired by the CPA Manager Plus reference review, but intentionally scoped to
Hermes-owned local evidence:

- no network calls
- no provider/account mutation
- no credential reads
- no external telemetry
- JSONL input/output only

The module defines a small redacted event schema and a doctor-style summary that
can be consumed by future `hermes doctor` / dashboard surfaces without widening
core model tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "hermes.provider_gateway_observability.v1"
DEFAULT_EVENT_LOG = Path.home() / ".hermes" / "state" / "provider_gateway_observability" / "events.jsonl"

SECRET_KEY_FRAGMENTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "cookie",
    "credential",
    "auth_file",
    "oauth",
)
SENSITIVE_METADATA_KEY_FRAGMENTS = (
    "prompt",
    "message",
    "messages",
    "body",
    "content",
    "text",
    "chat_id",
    "thread_id",
    "job_id",
)
ALLOWED_EVENT_KEYS = {
    "schema_version",
    "created_at",
    "surface",
    "operation",
    "status",
    "provider",
    "model",
    "route",
    "error_class",
    "error_summary",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "estimated_cost_usd",
    "metadata",
    "manual_override",
    "event_id",
}
SECRET_TEXT_PATTERNS = (
    re.compile(r"\b(Bearer\s+)[^\s,;]+", re.IGNORECASE),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(
        r"\b(access[_-]?token|refresh[_-]?token|api[_-]?key|auth[_-]?token|token|secret)\s*=\s*([^\s,;]+)",
        re.IGNORECASE,
    ),
)

ALLOWED_SURFACES = {"provider", "gateway", "cron", "tool", "scheduler"}
ALLOWED_STATUSES = {"success", "failure", "timeout", "cancelled", "blocked", "degraded"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_event_id(event: dict[str, Any]) -> str:
    basis = json.dumps(
        {
            "created_at": event.get("created_at"),
            "surface": event.get("surface"),
            "operation": event.get("operation"),
            "provider": event.get("provider"),
            "model": event.get("model"),
            "status": event.get("status"),
            "error_class": event.get("error_class"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "pgw_" + hashlib.sha256(basis).hexdigest()[:20]


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def _is_sensitive_metadata_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized.endswith("_hash"):
        return False
    return any(fragment in normalized for fragment in SENSITIVE_METADATA_KEY_FRAGMENTS)


def redact_value(value: Any, *, key: str = "") -> Any:
    """Recursively redact secret-bearing keys while preserving safe metadata."""
    if _is_secret_key(key) or _is_sensitive_metadata_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, key=key) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: Any) -> str:
    """Redact common inline secret forms from short diagnostic text."""
    value = str(text or "")[:500]
    value = SECRET_TEXT_PATTERNS[0].sub(r"\1[REDACTED]", value)
    value = SECRET_TEXT_PATTERNS[1].sub("[REDACTED]", value)
    value = SECRET_TEXT_PATTERNS[2].sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = re.sub(r"gho_[A-Za-z0-9_]+", "[REDACTED]", value, flags=re.IGNORECASE)
    return value


def normalize_event(raw: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """Return a schema-valid, redacted provider/gateway observability event."""
    event = {key: raw[key] for key in ALLOWED_EVENT_KEYS if key in raw}
    event["schema_version"] = SCHEMA_VERSION
    event["created_at"] = str(event.get("created_at") or now or utc_now())
    event["surface"] = str(event.get("surface") or "provider").strip().lower()
    event["operation"] = str(event.get("operation") or "unknown").strip()[:120] or "unknown"
    event["status"] = str(event.get("status") or "failure").strip().lower()
    event["provider"] = _safe_optional_text(event.get("provider"), limit=120)
    event["model"] = _safe_optional_text(event.get("model"), limit=160)
    event["route"] = _safe_optional_text(event.get("route"), limit=160)
    event["error_class"] = _safe_optional_text(event.get("error_class"), limit=160)
    event["error_summary"] = _safe_optional_text(redact_text(event.get("error_summary")), limit=300)
    event["latency_ms"] = _safe_nonnegative_int(event.get("latency_ms"))
    event["input_tokens"] = _safe_nonnegative_int(event.get("input_tokens"))
    event["output_tokens"] = _safe_nonnegative_int(event.get("output_tokens"))
    event["reasoning_tokens"] = _safe_nonnegative_int(event.get("reasoning_tokens"))
    event["cached_tokens"] = _safe_nonnegative_int(event.get("cached_tokens"))
    event["estimated_cost_usd"] = _safe_decimal_string(event.get("estimated_cost_usd"))
    event["metadata"] = redact_value(event.get("metadata") or {})
    event["manual_override"] = bool(event.get("manual_override", False))
    event["event_id"] = str(event.get("event_id") or stable_event_id(event))
    validate_event(event)
    return event


def _safe_optional_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_nonnegative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _safe_decimal_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    allowed = set("0123456789.")
    if not text or any(ch not in allowed for ch in text) or text.count(".") > 1:
        return None
    return text[:64]


def validate_event(event: dict[str, Any]) -> None:
    if event.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_version mismatch")
    if event.get("surface") not in ALLOWED_SURFACES:
        raise ValueError(f"unsupported surface: {event.get('surface')}")
    if event.get("status") not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported status: {event.get('status')}")
    if not event.get("event_id"):
        raise ValueError("event_id is required")
    if not event.get("created_at"):
        raise ValueError("created_at is required")


def append_event(event: dict[str, Any], path: Path = DEFAULT_EVENT_LOG) -> Path:
    normalized = normalize_event(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _event_log_from_env() -> Path:
    """Return the event log path, allowing tests/operators to redirect locally."""
    import os

    override = os.environ.get("HERMES_PROVIDER_GATEWAY_OBSERVABILITY_EVENT_LOG", "").strip()
    return Path(override).expanduser() if override else DEFAULT_EVENT_LOG


def _hash_identifier(value: Any) -> str | None:
    """Hash potentially identifying platform/channel values for safe metadata."""
    text = str(value or "").strip()
    if not text:
        return None
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _best_effort_append(event: dict[str, Any], path: Path | None = None) -> bool:
    """Append an event without ever affecting the caller's control flow."""
    try:
        append_event(event, path or _event_log_from_env())
        return True
    except Exception as exc:  # noqa: BLE001 - observability must be fail-open
        logger.debug("provider/gateway observability append failed: %s", exc)
        return False


def record_provider_call_event(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_mode: str | None = None,
    status: str = "success",
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cached_tokens: int | None = None,
    estimated_cost_usd: str | None = None,
    error_class: str | None = None,
    error_summary: str | None = None,
    route: str | None = None,
    metadata: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Record a redacted provider call outcome.

    This intentionally accepts only scalar counters and short summaries. It must
    never receive raw prompt text, message bodies, headers, API keys, or provider
    account payloads.
    """
    safe_metadata = dict(metadata or {})
    if api_mode:
        safe_metadata.setdefault("api_mode", api_mode)
    return _best_effort_append(
        {
            "surface": "provider",
            "operation": "llm_request",
            "provider": provider,
            "model": model,
            "route": route,
            "status": status,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cached_tokens": cached_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "error_class": error_class,
            "error_summary": error_summary,
            "metadata": safe_metadata,
        },
        path,
    )


def record_gateway_delivery_event(
    *,
    platform: str | None = None,
    status: str = "success",
    source: str | None = None,
    chat_id: Any = None,
    thread_id: Any = None,
    error_class: str | None = None,
    error_summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Record a redacted gateway/platform delivery outcome.

    Channel/thread identifiers are hashed; message content is deliberately not
    accepted by the function signature.
    """
    safe_metadata = dict(metadata or {})
    if source:
        safe_metadata.setdefault("source", source)
    chat_hash = _hash_identifier(chat_id)
    thread_hash = _hash_identifier(thread_id)
    if chat_hash:
        safe_metadata.setdefault("chat_id_hash", chat_hash)
    if thread_hash:
        safe_metadata.setdefault("thread_id_hash", thread_hash)
    return _best_effort_append(
        {
            "surface": "gateway",
            "operation": "delivery",
            "provider": platform,
            "status": status,
            "error_class": error_class,
            "error_summary": error_summary,
            "metadata": safe_metadata,
        },
        path,
    )


def record_cron_delivery_event(
    *,
    job_id: str | None = None,
    status: str = "success",
    delivery_target_count: int | None = None,
    error_class: str | None = None,
    error_summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Record cron delivery outcome without output body or target identifiers."""
    safe_metadata = dict(metadata or {})
    if job_id:
        safe_metadata.setdefault("job_id_hash", _hash_identifier(job_id))
    if delivery_target_count is not None:
        safe_metadata.setdefault("delivery_target_count", delivery_target_count)
    return _best_effort_append(
        {
            "surface": "cron",
            "operation": "delivery",
            "status": status,
            "error_class": error_class,
            "error_summary": error_summary,
            "metadata": safe_metadata,
        },
        path,
    )


def iter_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            events.append(normalize_event(raw))
        except Exception as exc:
            events.append(
                normalize_event(
                    {
                        "surface": "provider",
                        "operation": "event_log_parse",
                        "status": "failure",
                        "error_class": type(exc).__name__,
                        "error_summary": "invalid provider_gateway_observability JSONL row",
                    }
                )
            )
    return events


def summarize_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(events)
    status_counts = Counter(str(e.get("status")) for e in rows)
    surface_counts = Counter(str(e.get("surface")) for e in rows)
    provider_counts = Counter(str(e.get("provider") or "unknown") for e in rows)
    failure_class_counts = Counter(str(e.get("error_class") or "unknown") for e in rows if e.get("status") != "success")
    latency_by_surface: dict[str, list[int]] = defaultdict(list)
    token_totals = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0}
    for e in rows:
        if isinstance(e.get("latency_ms"), int):
            latency_by_surface[str(e.get("surface"))].append(int(e["latency_ms"]))
        for key in token_totals:
            if isinstance(e.get(key), int):
                token_totals[key] += int(e[key])
    return {
        "event_count": len(rows),
        "status_counts": dict(status_counts),
        "surface_counts": dict(surface_counts),
        "provider_counts": dict(provider_counts),
        "failure_class_counts": dict(failure_class_counts),
        "latency_ms_by_surface": {
            surface: {
                "count": len(values),
                "avg": int(sum(values) / len(values)) if values else None,
                "max": max(values) if values else None,
            }
            for surface, values in sorted(latency_by_surface.items())
        },
        "token_totals": token_totals,
    }


def build_doctor_projection(path: Path = DEFAULT_EVENT_LOG) -> dict[str, Any]:
    events = list(iter_events(path))
    summary = summarize_events(events)
    failures = summary["event_count"] - int(summary["status_counts"].get("success", 0))
    if summary["event_count"] == 0:
        status = "NO_DATA"
    elif failures == 0:
        status = "OK"
    elif failures < summary["event_count"]:
        status = "DEGRADED"
    else:
        status = "FAILING"
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "status": status,
        "event_log": str(path),
        "summary": summary,
        "safe_boundaries": [
            "local JSONL only",
            "no network calls",
            "no credential reads or writes",
            "no provider/account mutation",
            "no external telemetry",
        ],
        "next_actions": next_actions_for_status(status),
    }


def next_actions_for_status(status: str) -> list[str]:
    if status == "NO_DATA":
        return ["wire provider/gateway call sites to append redacted local events before using this as health evidence"]
    if status == "OK":
        return ["keep as read-only evidence; do not mutate providers without a separate authority gate"]
    if status == "DEGRADED":
        return ["inspect failure_class_counts and provider_counts; keep any automation advisory until manual overrides are checked"]
    return ["treat provider/gateway health as blocked; collect redacted evidence and stop before credential/provider mutation"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes local provider/gateway observability projection")
    parser.add_argument("--event-log", default=str(DEFAULT_EVENT_LOG))
    parser.add_argument("--record-json", help="Record a single event from an inline JSON object")
    parser.add_argument("--doctor-json", action="store_true", help="Print a local doctor projection as JSON")
    args = parser.parse_args(argv)

    event_log = Path(args.event_log).expanduser()
    if args.record_json:
        append_event(json.loads(args.record_json), event_log)
    projection = build_doctor_projection(event_log)
    if args.doctor_json or not args.record_json:
        print(json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
