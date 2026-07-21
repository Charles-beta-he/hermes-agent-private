"""Durable append-only ledger and deterministic replay for WorkflowLoopEventV1."""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, NoReturn, cast

import jsonschema

from workflow_kernel.runtime_state import (
    PRIVATE_FILE_MODE,
    LedgerAnchor,
    exclusive_ledger_lock,
    open_ledger_file,
    read_ledger_descriptor_bytes,
)
from workflow_kernel.workflow_loop import (
    WorkflowLoopProjectionError,
    apply_workflow_loop_signal_v1,
    create_workflow_loop_snapshot_v1,
)


_SCHEMA_NAME = "workflow-loop-event-v1.schema.json"
_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})$"
)


class WorkflowLoopEventLedgerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowLoopEventLedgerError(code, message)


@lru_cache(maxsize=1)
def _validator() -> jsonschema.Draft202012Validator:
    schema_text = (
        resources.files("workflow_kernel.schemas")
        .joinpath(_SCHEMA_NAME)
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _canonical_body(body: Mapping[str, Any]) -> dict[str, Any]:
    sequence = body["sequence"]
    if isinstance(sequence, float) and sequence.is_integer():
        sequence = int(sequence)
    signal = body["signal"]
    signal_sequence = signal["expected_sequence"]
    if isinstance(signal_sequence, float) and signal_sequence.is_integer():
        signal_sequence = int(signal_sequence)
    return {
        "schema_version": body["schema_version"],
        "run_id": body["run_id"],
        "session_owner": body["session_owner"],
        "sequence": sequence,
        "previous_event_id": body["previous_event_id"],
        "idempotency_key": body["idempotency_key"],
        "occurred_at": body["occurred_at"],
        "expires_at": body["expires_at"],
        "definition_digest": body["definition_digest"],
        "plan_digest": body["plan_digest"],
        "signal": {
            "schema_version": signal["schema_version"],
            "signal_id": signal["signal_id"],
            "kind": signal["kind"],
            "expected_sequence": signal_sequence,
            "task_id": signal["task_id"],
            "reason": signal["reason"],
        },
    }


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def derive_workflow_loop_event_id_v1(body: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_compact_json(_canonical_body(body))).hexdigest()
    return f"wle_{digest}"


def _canonical_event_bytes(event: Mapping[str, Any]) -> bytes:
    return _compact_json(
        {**_canonical_body(event), "event_id": event["event_id"]}
    )


def _normalized_event(candidate: object) -> dict[str, Any]:
    errors = sorted(
        _validator().iter_errors(cast(Any, candidate)),
        key=lambda error: tuple(
            f"{type(part).__name__}:{part}" for part in error.absolute_path
        ),
    )
    if errors:
        _fail("schema_invalid", errors[0].message)
    if not isinstance(candidate, dict):
        _fail("schema_invalid", "event must be a JSON object")
    event = _canonical_body(candidate)
    event["event_id"] = candidate["event_id"]
    if derive_workflow_loop_event_id_v1(event) != event["event_id"]:
        _fail("identity_mismatch", "event_id does not match canonical event body")
    return event


def _reject_constant(value: str) -> NoReturn:
    _fail("invalid_json", f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key", f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _decode_ledger(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        _fail("torn_tail", "ledger does not end with a complete JSONL record")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("invalid_utf8", str(exc))
    events: list[dict[str, Any]] = []
    for index, line in enumerate(text[:-1].split("\n")):
        if not line:
            _fail("invalid_json", f"empty JSONL record at index {index}")
        try:
            candidate = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except WorkflowLoopEventLedgerError:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            _fail("invalid_json", f"invalid JSONL record at index {index}: {exc}")
        events.append(_normalized_event(candidate))
    return events


def _days_before_year(year: int) -> int:
    return 365 * year + (year + 3) // 4 - (year + 99) // 100 + (year + 399) // 400


def _instant(timestamp: str) -> tuple[int, str]:
    match = _TIMESTAMP.fullmatch(timestamp)
    if match is None:
        _fail("invalid_event", f"invalid RFC3339 instant: {timestamp}")
    prefix = match.group(1)
    year = int(prefix[0:4])
    month = int(prefix[5:7])
    day = int(prefix[8:10])
    hour = int(prefix[11:13])
    minute = int(prefix[14:16])
    second = int(match.group(2) or "00")
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    month_lengths = (
        31,
        29 if leap else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    if not 1 <= month <= 12 or not 1 <= day <= month_lengths[month - 1]:
        _fail("invalid_event", f"invalid RFC3339 calendar date: {timestamp}")
    if hour > 23 or minute > 59 or second > 59:
        _fail("invalid_event", f"invalid RFC3339 clock time: {timestamp}")
    days = _days_before_year(year) + sum(month_lengths[: month - 1]) + day - 1
    epoch_second = days * 86_400 + hour * 3_600 + minute * 60 + second
    offset = match.group(4)
    if offset != "Z":
        offset_hour = int(offset[1:3])
        offset_minute = int(offset[4:6])
        if offset_hour > 23 or offset_minute > 59:
            _fail("invalid_event", f"invalid RFC3339 offset: {timestamp}")
        offset_seconds = offset_hour * 3_600 + offset_minute * 60
        epoch_second += -offset_seconds if offset[0] == "+" else offset_seconds
    return epoch_second, match.group(3) or ""


def _compare_instants(left: tuple[int, str], right: tuple[int, str]) -> int:
    if left[0] < right[0]:
        return -1
    if left[0] > right[0]:
        return 1
    width = max(len(left[1]), len(right[1]))
    left_fraction = left[1].ljust(width, "0")
    right_fraction = right[1].ljust(width, "0")
    return (left_fraction > right_fraction) - (left_fraction < right_fraction)


def replay_workflow_loop_events_v1(
    plan: Mapping[str, Any], events: object
) -> dict[str, Any]:
    """Replay immutable observed loop events into a pure snapshot projection."""

    if not isinstance(events, list):
        _fail("invalid_events", "events must be a list")
    snapshot = create_workflow_loop_snapshot_v1(plan)
    applied = 0
    last_event: dict[str, Any] | None = None
    last_occurred_at: tuple[int, str] | None = None
    lease_expires_at: tuple[int, str] | None = None
    by_id: dict[str, bytes] = {}
    by_key: dict[str, bytes] = {}

    for candidate in events:
        event = _normalized_event(candidate)
        canonical = _canonical_event_bytes(event)
        existing_key = by_key.get(event["idempotency_key"])
        if existing_key is not None:
            if existing_key == canonical:
                continue
            _fail("idempotency_conflict", "idempotency key maps to a different event")
        existing_id = by_id.get(event["event_id"])
        if existing_id is not None:
            if existing_id == canonical:
                continue
            _fail("event_id_conflict", "event_id maps to a different event")

        if event["definition_digest"] != plan["definition_digest"]:
            _fail("definition_digest_mismatch", "event does not belong to execution plan")
        if event["plan_digest"] != plan["plan_digest"]:
            _fail("plan_digest_mismatch", "event does not belong to execution plan")
        if event["sequence"] != applied + 1:
            _fail("sequence_mismatch", "event sequence is not contiguous")
        expected_previous = None if last_event is None else last_event["event_id"]
        if event["previous_event_id"] != expected_previous:
            _fail("broken_link", "previous_event_id does not match ledger tip")
        if event["signal"]["expected_sequence"] != snapshot["sequence"]:
            _fail("signal_sequence_mismatch", "signal does not target current snapshot")
        if event["sequence"] != event["signal"]["expected_sequence"] + 1:
            _fail("signal_sequence_mismatch", "event and signal sequences differ")

        occurred_at = _instant(event["occurred_at"])
        expires_at = _instant(event["expires_at"])
        if last_occurred_at is not None and _compare_instants(
            occurred_at, last_occurred_at
        ) < 0:
            _fail("time_reversal", "event time precedes ledger tip")
        if last_event is not None:
            if event["run_id"] != last_event["run_id"]:
                _fail("mixed_run", "one ledger cannot contain multiple runs")
            if event["session_owner"] != last_event["session_owner"]:
                _fail("session_owner_mismatch", "session owner changed during run")
            assert lease_expires_at is not None
            if _compare_instants(expires_at, lease_expires_at) != 0:
                _fail("lease_expiry_mismatch", "session expiration changed during run")
        else:
            lease_expires_at = expires_at

        expiry_comparison = _compare_instants(occurred_at, expires_at)
        is_stale_signal = event["signal"]["kind"] == "stale_observed"
        if is_stale_signal and expiry_comparison < 0:
            _fail("session_not_expired", "stale signal occurred before session expiry")
        if not is_stale_signal and expiry_comparison >= 0:
            _fail("session_expired", "non-stale signal occurred after session expiry")

        try:
            snapshot = apply_workflow_loop_signal_v1(
                plan, snapshot, event["signal"]
            )
        except WorkflowLoopProjectionError as exc:
            _fail(exc.code, str(exc))
        applied += 1
        last_event = event
        last_occurred_at = occurred_at
        by_id[event["event_id"]] = canonical
        by_key[event["idempotency_key"]] = canonical

    return snapshot


def _open_existing(
    anchor: LedgerAnchor,
    ledger_path: Path,
    private_root: Path,
    flags: int = os.O_RDONLY,
) -> int | None:
    try:
        return open_ledger_file(
            ledger_path,
            flags,
            private_root=private_root,
            anchor=anchor,
        )
    except FileNotFoundError:
        return None


def read_workflow_loop_events(
    ledger_path: Path, *, private_root: Path
) -> list[dict[str, Any]]:
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        assert anchor is not None
        descriptor = _open_existing(anchor, ledger_path, private_root)
        if descriptor is None:
            return []
        try:
            return _decode_ledger(read_ledger_descriptor_bytes(descriptor))
        finally:
            os.close(descriptor)


def recover_torn_workflow_loop_event_tail(
    ledger_path: Path,
    plan: Mapping[str, Any],
    *,
    expected_sha256: str,
    private_root: Path,
) -> dict[str, object]:
    """Remove one confirmed-incomplete final record under exact-content CAS."""

    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        _fail("recovery_cas_invalid", "expected_sha256 must be lowercase SHA-256")
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        assert anchor is not None
        descriptor = _open_existing(anchor, ledger_path, private_root, os.O_RDWR)
        if descriptor is None:
            _fail("recovery_missing", "ledger does not exist")
        try:
            raw = read_ledger_descriptor_bytes(descriptor)
            actual_sha256 = hashlib.sha256(raw).hexdigest()
            if actual_sha256 != expected_sha256:
                _fail("recovery_cas_mismatch", "ledger changed after recovery authorization")
            if not raw or raw.endswith(b"\n"):
                _fail("tail_not_torn", "ledger has no unterminated tail")
            boundary = raw.rfind(b"\n") + 1
            prefix = raw[:boundary]
            tail = raw[boundary:]
            events = _decode_ledger(prefix)
            snapshot = replay_workflow_loop_events_v1(plan, events)
            try:
                tail_text = tail.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                if exc.reason != "unexpected end of data" or exc.end != len(tail):
                    _fail("invalid_utf8", str(exc))
            else:
                try:
                    json.loads(
                        tail_text,
                        object_pairs_hook=_unique_object,
                        parse_constant=_reject_constant,
                    )
                except json.JSONDecodeError:
                    pass
                else:
                    _fail(
                        "tail_not_confirmed_torn",
                        "unterminated tail is complete JSON and cannot be discarded",
                    )
            os.ftruncate(descriptor, boundary)
            try:
                os.fsync(descriptor)
            except OSError as exc:
                _fail("durability_unknown", f"tail recovery fsync failed: {exc}")
            return {
                "status": "recovered",
                "removed_bytes": len(tail),
                "count": snapshot["sequence"],
            }
        finally:
            os.close(descriptor)


def append_workflow_loop_event(
    ledger_path: Path,
    plan: Mapping[str, Any],
    candidate: object,
    *,
    private_root: Path,
) -> dict[str, Any]:
    """Validate, replay, append once, fsync, and return the projected readback."""

    event = _normalized_event(candidate)
    canonical = _canonical_event_bytes(event)
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        assert anchor is not None
        try:
            os.stat(
                anchor.name,
                dir_fd=anchor.directory_descriptor,
                follow_symlinks=False,
            )
            created = False
        except FileNotFoundError:
            created = True
        descriptor = open_ledger_file(
            ledger_path,
            os.O_RDWR | os.O_CREAT,
            private_root=private_root,
            anchor=anchor,
            mode=PRIVATE_FILE_MODE,
        )
        try:
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            events = _decode_ledger(read_ledger_descriptor_bytes(descriptor))
            snapshot = replay_workflow_loop_events_v1(plan, events)
            by_key = {
                item["idempotency_key"]: _canonical_event_bytes(item)
                for item in events
            }
            by_id = {item["event_id"]: _canonical_event_bytes(item) for item in events}
            existing_key = by_key.get(event["idempotency_key"])
            if existing_key is not None:
                if existing_key == canonical:
                    os.fsync(descriptor)
                    os.fsync(anchor.directory_descriptor)
                    return {
                        "status": "duplicate",
                        "event_id": event["event_id"],
                        "count": snapshot["sequence"],
                        "snapshot": snapshot,
                    }
                _fail("idempotency_conflict", "idempotency key maps to a different event")
            existing_id = by_id.get(event["event_id"])
            if existing_id is not None:
                if existing_id == canonical:
                    os.fsync(descriptor)
                    os.fsync(anchor.directory_descriptor)
                    return {
                        "status": "duplicate",
                        "event_id": event["event_id"],
                        "count": snapshot["sequence"],
                        "snapshot": snapshot,
                    }
                _fail("event_id_conflict", "event_id maps to a different event")

            next_snapshot = replay_workflow_loop_events_v1(plan, [*events, event])
            record = canonical + b"\n"
            os.lseek(descriptor, 0, os.SEEK_END)
            written = os.write(descriptor, record)
            if written != len(record):
                _fail("short_write", f"short ledger write: {written} of {len(record)} bytes")
            try:
                os.fsync(descriptor)
                if created:
                    os.fsync(anchor.directory_descriptor)
            except OSError as exc:
                _fail("durability_unknown", f"ledger fsync failed: {exc}")
            return {
                "status": "appended",
                "event_id": event["event_id"],
                "count": next_snapshot["sequence"],
                "snapshot": next_snapshot,
            }
        finally:
            os.close(descriptor)
