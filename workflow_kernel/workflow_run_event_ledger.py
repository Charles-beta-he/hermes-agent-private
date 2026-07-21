from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from workflow_kernel.runtime_state import (
    PRIVATE_FILE_MODE,
    LedgerAnchor,
    exclusive_ledger_lock,
    open_ledger_file,
    read_ledger_descriptor_bytes,
)


_SCHEMA_NAME = "workflow-run-event-v1.schema.json"
_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})$"
)


class WorkflowRunEventLedgerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowRunEventLedgerError(code, message)


@lru_cache(maxsize=1)
def _validator() -> Validator:
    resource = importlib.resources.files("workflow_kernel.schemas").joinpath(_SCHEMA_NAME)
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _normalized_event(candidate: object) -> dict[str, Any]:
    json_candidate = cast(Any, candidate)
    errors = sorted(
        _validator().iter_errors(json_candidate),
        key=lambda error: tuple(f"{type(part).__name__}:{part}" for part in error.absolute_path),
    )
    if errors:
        _fail("schema_invalid", errors[0].message)
    if not isinstance(candidate, dict):
        _fail("schema_invalid", "event must be a JSON object")
    event = dict(candidate)
    sequence = event["sequence"]
    if isinstance(sequence, float) and sequence.is_integer():
        event["sequence"] = int(sequence)
    event["provenance"] = dict(event["provenance"])
    event["transition"] = dict(event["transition"])
    body = {key: value for key, value in event.items() if key != "event_id"}
    if derive_workflow_run_event_id_v1(body) != event["event_id"]:
        _fail("identity_mismatch", "event_id does not match the canonical event body")
    return event


def _canonical_body(body: dict[str, Any]) -> dict[str, Any]:
    sequence = body["sequence"]
    if isinstance(sequence, float) and sequence.is_integer():
        sequence = int(sequence)
    return {
        "schema_version": body["schema_version"],
        "run_id": body["run_id"],
        "sequence": sequence,
        "previous_event_id": body["previous_event_id"],
        "idempotency_key": body["idempotency_key"],
        "occurred_at": body["occurred_at"],
        "provenance": {
            "producer": body["provenance"]["producer"],
            "artifact_path": body["provenance"]["artifact_path"],
            "artifact_digest": body["provenance"]["artifact_digest"],
        },
        "transition": {
            "from": body["transition"]["from"],
            "to": body["transition"]["to"],
        },
    }


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_record_once(descriptor: int, record: bytes) -> int:
    return os.write(descriptor, record)


def _fsync_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)


def _confirm_duplicate_durability(descriptor: int, directory_descriptor: int) -> None:
    try:
        _fsync_descriptor(descriptor)
        _fsync_descriptor(directory_descriptor)
    except OSError as exc:
        _fail("durability_unknown", f"duplicate ledger fsync failed: {exc}")


def derive_workflow_run_event_id_v1(body: dict[str, Any]) -> str:
    digest = hashlib.sha256(_compact_json(_canonical_body(body))).hexdigest()
    return f"wre_{digest}"


def _canonical_event_bytes(event: dict[str, Any]) -> bytes:
    body = _canonical_body(event)
    return _compact_json({**body, "event_id": event["event_id"]})


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
        except WorkflowRunEventLedgerError:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            _fail("invalid_json", f"invalid JSONL record at index {index}: {exc}")
        events.append(_normalized_event(candidate))
    _replay(events)
    return events


@dataclass(frozen=True)
class _ReplayState:
    applied: int
    last_event: dict[str, Any] | None
    by_event_id: dict[str, bytes]
    by_idempotency_key: dict[str, bytes]


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
    month_lengths = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
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


def _instant_is_before(left: tuple[int, str], right: tuple[int, str]) -> bool:
    if left[0] != right[0]:
        return left[0] < right[0]
    width = max(len(left[1]), len(right[1]))
    return left[1].ljust(width, "0") < right[1].ljust(width, "0")


def _replay(events: list[dict[str, Any]]) -> _ReplayState:
    applied = 0
    last: dict[str, Any] | None = None
    last_instant: tuple[int, str] | None = None
    by_id: dict[str, bytes] = {}
    by_key: dict[str, bytes] = {}
    for event in events:
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
        if last is not None and event["run_id"] != last["run_id"]:
            _fail("mixed_run", "one ledger cannot contain multiple run_id values")
        if event["sequence"] != applied + 1:
            _fail("sequence_mismatch", "event sequence is not contiguous")
        expected_previous = None if last is None else last["event_id"]
        if event["previous_event_id"] != expected_previous:
            _fail("broken_link", "previous_event_id does not match the applied ledger tip")
        current_instant = _instant(event["occurred_at"])
        if last_instant is not None and _instant_is_before(current_instant, last_instant):
            _fail("time_reversal", "event time precedes the applied ledger tip")
        if last is None:
            if event["transition"]["from"] != "proposed":
                _fail("discontinuous_transition", "first event must transition from proposed")
        elif event["transition"]["from"] != last["transition"]["to"]:
            _fail("discontinuous_transition", "event transition does not continue the prior state")
        by_key[event["idempotency_key"]] = canonical
        by_id[event["event_id"]] = canonical
        applied += 1
        last = event
        last_instant = current_instant
    return _ReplayState(applied, last, by_id, by_key)


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


def read_workflow_run_events(
    ledger_path: Path,
    *,
    private_root: Path,
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


def recover_torn_workflow_run_event_tail(
    ledger_path: Path,
    *,
    expected_sha256: str,
    private_root: Path,
) -> dict[str, object]:
    """Remove one confirmed-incomplete final record under an exact-content CAS."""
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        _fail("recovery_cas_invalid", "expected_sha256 must be 64 lowercase hex characters")
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        assert anchor is not None
        descriptor = _open_existing(anchor, ledger_path, private_root, os.O_RDWR)
        if descriptor is None:
            _fail("recovery_missing", "ledger does not exist")
        try:
            raw = read_ledger_descriptor_bytes(descriptor)
            actual_sha256 = hashlib.sha256(raw).hexdigest()
            if actual_sha256 != expected_sha256:
                _fail("recovery_cas_mismatch", "ledger changed after recovery was authorized")
            if not raw or raw.endswith(b"\n"):
                _fail("tail_not_torn", "ledger has no unterminated tail")
            boundary = raw.rfind(b"\n") + 1
            prefix = raw[:boundary]
            tail = raw[boundary:]
            events = _decode_ledger(prefix)
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
                        "unterminated tail is complete JSON and cannot be discarded automatically",
                    )
            os.ftruncate(descriptor, boundary)
            try:
                _fsync_descriptor(descriptor)
            except OSError as exc:
                _fail("durability_unknown", f"tail recovery fsync failed: {exc}")
            return {
                "status": "recovered",
                "removed_bytes": len(tail),
                "count": _replay(events).applied,
            }
        finally:
            os.close(descriptor)


def append_workflow_run_event(
    ledger_path: Path,
    candidate: object,
    *,
    private_root: Path,
) -> dict[str, object]:
    event = _normalized_event(candidate)
    canonical = _canonical_event_bytes(event)
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        assert anchor is not None
        try:
            os.stat(anchor.name, dir_fd=anchor.directory_descriptor, follow_symlinks=False)
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
            state = _replay(events)
            existing_key = state.by_idempotency_key.get(event["idempotency_key"])
            if existing_key is not None:
                if existing_key == canonical:
                    _confirm_duplicate_durability(descriptor, anchor.directory_descriptor)
                    return {"status": "duplicate", "event_id": event["event_id"], "count": state.applied}
                _fail("idempotency_conflict", "idempotency key maps to a different event")
            existing_id = state.by_event_id.get(event["event_id"])
            if existing_id is not None:
                if existing_id == canonical:
                    _confirm_duplicate_durability(descriptor, anchor.directory_descriptor)
                    return {"status": "duplicate", "event_id": event["event_id"], "count": state.applied}
                _fail("event_id_conflict", "event_id maps to a different event")
            next_state = _replay([*events, event])
            record = canonical + b"\n"
            os.lseek(descriptor, 0, os.SEEK_END)
            written = _write_record_once(descriptor, record)
            if written != len(record):
                _fail("short_write", f"short ledger write: {written} of {len(record)} bytes")
            try:
                _fsync_descriptor(descriptor)
                if created:
                    _fsync_descriptor(anchor.directory_descriptor)
            except OSError as exc:
                _fail("durability_unknown", f"ledger fsync failed: {exc}")
            return {"status": "appended", "event_id": event["event_id"], "count": next_state.applied}
        finally:
            os.close(descriptor)
