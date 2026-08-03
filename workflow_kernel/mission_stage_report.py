"""Isolated F4 Mission stage-report adapter.

This module owns no Mission lifecycle state.  It validates one exact request and
asks the canonical :class:`MissionStore` to append a non-actionable report under
SQLite CAS/idempotency ownership.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from hermes_cli.missions import MissionStore


class MissionStageReportError(RuntimeError):
    """Stable F4 adapter failure."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise MissionStageReportError(code, message)


def _text(value: object, *, field: str, required: bool, maximum: int) -> str:
    if not isinstance(value, str):
        _fail("request_invalid", f"{field} must be a string")
    normalized = value.strip()
    valid_utf8 = True
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        valid_utf8 = False
    if not valid_utf8:
        _fail("request_invalid", f"{field} must be valid UTF-8 text")
    if required and not normalized:
        _fail("request_invalid", f"{field} is required")
    if len(normalized) > maximum:
        _fail("request_invalid", f"{field} exceeds {maximum} characters")
    return normalized


def _text_sequence(value: object, *, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        _fail("request_invalid", f"{field} must be a list or tuple")
    if len(value) > 256:
        _fail("request_invalid", f"{field} exceeds 256 entries")
    normalized = [
        _text(item, field=f"{field}[{index}]", required=True, maximum=4096)
        for index, item in enumerate(value)
    ]
    if len(set(normalized)) != len(normalized):
        _fail("request_invalid", f"{field} contains duplicate values")
    return normalized


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _same_canonical_value(left: object, right: object) -> bool:
    try:
        return _digest(left) == _digest(right)
    except (TypeError, ValueError, UnicodeError):
        return False


def _valid_durable_text(value: object, *, maximum: int) -> bool:
    if not isinstance(value, str) or not value or len(value) > maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_created_at(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return math.isfinite(value) and value >= 0
    return False


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class _Missing:
    pass


_MISSING = _Missing()


def append_mission_stage_report(
    *,
    mission_store_path: str | Path | _Missing = _MISSING,
    mission_id: str | _Missing = _MISSING,
    expected_revision: int | _Missing = _MISSING,
    idempotency_key: str | _Missing = _MISSING,
    summary: str | _Missing = _MISSING,
    evidence: Sequence[str] | _Missing = _MISSING,
    risks: Sequence[str] | _Missing = _MISSING,
    next_step: str | _Missing = _MISSING,
    needs_user: bool | _Missing = _MISSING,
    **unknown_fields: object,
) -> Mapping[str, Any]:
    """Append or replay one exact non-actionable Mission stage report."""

    supplied = {
        "mission_store_path": mission_store_path,
        "mission_id": mission_id,
        "expected_revision": expected_revision,
        "idempotency_key": idempotency_key,
        "summary": summary,
        "evidence": evidence,
        "risks": risks,
        "next_step": next_step,
        "needs_user": needs_user,
    }
    missing_fields = [name for name, value in supplied.items() if value is _MISSING]
    if missing_fields:
        _fail("request_invalid", f"missing required fields: {', '.join(missing_fields)}")
    if unknown_fields:
        _fail(
            "request_invalid",
            f"unknown request fields: {', '.join(sorted(unknown_fields))}",
        )

    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        _fail("request_invalid", "expected_revision must be a non-negative integer")
    if expected_revision < 0 or expected_revision > 2**63 - 2:
        _fail(
            "request_invalid",
            "expected_revision must be between 0 and 9223372036854775806",
        )
    if not isinstance(needs_user, bool):
        _fail("request_invalid", "needs_user must be a boolean")
    if not isinstance(mission_store_path, (str, Path)):
        _fail("request_invalid", "mission_store_path must be a path")
    if "\x00" in str(mission_store_path):
        _fail("request_invalid", "mission_store_path is not resolvable")
    store_path: Path | None = None
    try:
        store_path = Path(mission_store_path).expanduser()
    except (OSError, RuntimeError, ValueError):
        pass
    if store_path is None:
        _fail("request_invalid", "mission_store_path is not resolvable")
    store_exists: bool | None = None
    try:
        store_exists = store_path.is_file()
    except (OSError, ValueError):
        pass
    if store_exists is None:
        _fail("request_invalid", "mission_store_path cannot be inspected")
    if not store_exists:
        _fail("mission_not_found", "mission store does not exist")
    mission = _text(mission_id, field="mission_id", required=True, maximum=256)
    key = _text(idempotency_key, field="idempotency_key", required=True, maximum=512)
    normalized_summary = _text(summary, field="summary", required=True, maximum=4096)
    normalized_evidence = _text_sequence(evidence, field="evidence")
    normalized_risks = _text_sequence(risks, field="risks")
    normalized_next = _text(next_step, field="next_step", required=False, maximum=4096)
    payload = {
        "evidence": normalized_evidence,
        "risks": normalized_risks,
        "next_step": normalized_next,
        "needs_user": needs_user,
    }
    request_body = {
        "schema": "mission_stage_report_request_v1",
        "operation": "append_stage_report",
        "mission_id": mission,
        "expected_revision": expected_revision,
        "idempotency_key": key,
        "summary": normalized_summary,
        "payload": payload,
    }
    request_digest = _digest(request_body)
    event: Mapping[str, Any] | None = None
    failure: tuple[str, str] | None = None
    try:
        store = MissionStore(store_path)
        event = store.append_stage_report_idempotent(
            mission,
            expected_revision=expected_revision,
            idempotency_key=key,
            request_digest=request_digest,
            summary=normalized_summary,
            payload=payload,
        )
    except KeyError:
        failure = ("mission_not_found", f"mission does not exist: {mission}")
    except ValueError as exc:
        code = str(exc)
        if code in {"revision_conflict", "idempotency_conflict"}:
            failure = (code, code.replace("_", " "))
        else:
            failure = (
                "durability_unknown",
                "mission store operation outcome is unknown",
            )
    except RuntimeError as exc:
        if str(exc) == "readback_mismatch":
            failure = ("readback_mismatch", "durable event readback did not match")
        else:
            failure = (
                "durability_unknown",
                "mission store operation outcome is unknown",
            )
    except (sqlite3.Error, OSError):
        failure = (
            "durability_unknown",
            "mission store operation outcome is unknown",
        )
    except Exception:
        failure = (
            "durability_unknown",
            "mission store operation outcome is unknown",
        )
    if failure is not None:
        _fail(*failure)
    if not isinstance(event, Mapping):
        _fail("readback_mismatch", "durable event readback did not match")

    event_id = event.get("event_id")
    created_at = event.get("created_at")
    actual_revision = event.get("mission_revision")
    if (
        not _valid_durable_text(event_id, maximum=256)
        or not _valid_created_at(created_at)
        or type(actual_revision) is not int
        or event.get("mission_id") != mission
        or event.get("event_type") != "stage_report"
        or event.get("idempotency_key") != key
        or event.get("request_digest") != request_digest
        or event.get("summary") != normalized_summary
        or not _same_canonical_value(event.get("payload"), payload)
        or actual_revision != expected_revision + 1
    ):
        _fail("readback_mismatch", "durable event identity did not match the request")
    result: dict[str, Any] = {
        "mission_id": mission,
        "mission_revision": actual_revision,
        "request_digest": request_digest,
        "event": event,
    }
    digest_failed = False
    try:
        result["result_digest"] = _digest(result)
    except (TypeError, ValueError, UnicodeError):
        digest_failed = True
    if digest_failed:
        _fail("readback_mismatch", "durable event could not be canonically digested")
    return _freeze(result)


__all__ = ["MissionStageReportError", "append_mission_stage_report"]
