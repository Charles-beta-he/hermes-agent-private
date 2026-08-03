"""Descriptor-pinned CAS/fence precursor for canonical Markdown task notes.

This module is deliberately not integrated with the live taskpool.  Callers must
supply an existing task-root directory; the 4C certification exercises only
isolated temporary roots.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NoReturn, cast

SCHEMA_VERSION = "taskpool_transition_receipt_v1"
MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_IDEMPOTENCY_ENTRIES = 64
MAX_IDEMPOTENCY_BYTES = 65_536
MAX_TEXT_BYTES = 4_096
MAX_EVIDENCE_ITEMS = 64
MAX_COUNTER = (1 << 63) - 1
ZERO_DIGEST = "sha256:" + "0" * 64

OWNED_FIELDS = (
    "taskpool_revision",
    "taskpool_fence",
    "taskpool_claimed_by",
    "taskpool_run_id",
    "taskpool_claim_expires_at",
    "taskpool_idempotency_v1",
)
COUNTER_FIELDS = ("taskpool_revision", "taskpool_fence")
CLAIM_FIELDS = (
    "taskpool_claimed_by",
    "taskpool_run_id",
    "taskpool_claim_expires_at",
)
CLAIM_BOUND = {"claimed", "running"}
SUPPORTED_TRANSITIONS = {
    "pending": {"claimed"},
    "retry": {"claimed"},
    "claimed": {"running", "pending", "retry", "blocked", "expired"},
    "running": {"review", "retry", "blocked", "expired"},
}
KNOWN_STATUSES = {
    "proposed",
    "pending",
    "claimed",
    "running",
    "retry",
    "review",
    "certified",
    "blocked",
    "done",
    "archived",
    "expired",
}
EVIDENCE_PREFIXES = (
    "command:",
    "file:",
    "search:",
    "brainctl:",
    "artifact:",
    "url:",
    "proof:",
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TOP_LEVEL = re.compile(r"([A-Za-z0-9_-]+):(.*)\Z")
_INTEGER = re.compile(r"0|[1-9][0-9]*\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
RECEIPT_FIELDS = {
    "schema_version",
    "task_id",
    "idempotency_key",
    "request_digest",
    "from_status",
    "to_status",
    "previous_revision",
    "new_revision",
    "previous_fence",
    "new_fence",
    "actor",
    "run_id",
    "occurred_at",
    "lease_expires_at",
    "canonical_post_write_sha256",
    "idempotent_replay",
}

# Tests may install a deterministic rendezvous. Production callers cannot pass a
# hook through the public API.
_TEST_HOOK: Callable[[str], None] | None = None


class TaskpoolCASError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise TaskpoolCASError(code, message)


def _hook(stage: str) -> None:
    if _TEST_HOOK is not None:
        _TEST_HOOK(stage)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _request_digest(value: dict[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _parse_utc(value: str, *, label: str) -> datetime:
    if type(value) is not str or not _UTC_INSTANT.fullmatch(value):
        _fail("invalid_time", f"{label} must be a canonical UTC instant")
    candidate = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        _fail("invalid_time", f"{label} is not an ISO-8601 instant")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("invalid_time", f"{label} must use UTC")
    return parsed


def _utc_now() -> tuple[str, datetime]:
    current = datetime.now(timezone.utc)
    rendered = current.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return rendered, current


def _validated_token(value: str, *, label: str, allow_empty: bool = False) -> str:
    if type(value) is not str:
        _fail(f"invalid_{label}", f"{label} has an invalid token shape")
    if allow_empty and value == "":
        return value
    if not _TOKEN.fullmatch(value):
        _fail(f"invalid_{label}", f"{label} has an invalid token shape")
    return value


def _validated_text(value: str, *, label: str, allow_empty: bool = True) -> str:
    if type(value) is not str or "\n" in value or "\r" in value or "\0" in value:
        _fail(f"invalid_{label}", f"{label} must be one clean line")
    if len(value) > MAX_TEXT_BYTES or value != value.strip():
        _fail(f"invalid_{label}", f"{label} must be bounded without surrounding whitespace")
    if not allow_empty and not value:
        _fail(f"invalid_{label}", f"{label} must not be empty")
    if not all(character.isprintable() for character in value):
        _fail(f"invalid_{label}", f"{label} must contain only printable characters")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(f"invalid_{label}", f"{label} must contain Unicode scalar values")
    if len(encoded) > MAX_TEXT_BYTES:
        _fail(f"invalid_{label}", f"{label} exceeds its byte bound")
    return value


def _strict_json_object(value: str) -> dict[str, object]:
    def reject_constant(_value: str) -> NoReturn:
        raise ValueError("non-finite JSON number")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError, RecursionError, OverflowError):
        _fail("invalid_task_metadata", "idempotency map is not strict JSON")
    if not isinstance(parsed, dict):
        _fail("invalid_task_metadata", "idempotency map must be a JSON object")
    return cast(dict[str, object], parsed)


def _validate_stored_receipt(key: str, digest: str, receipt: object) -> dict[str, object]:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        _fail("invalid_task_metadata", "stored idempotency receipt has an invalid exact shape")
    value = cast(dict[str, object], receipt)
    string_fields = (
        "schema_version",
        "task_id",
        "idempotency_key",
        "request_digest",
        "from_status",
        "to_status",
        "actor",
        "run_id",
        "occurred_at",
        "canonical_post_write_sha256",
    )
    if any(not isinstance(value.get(field), str) for field in string_fields):
        _fail("invalid_task_metadata", "stored idempotency receipt has invalid field types")
    counters = ("previous_revision", "new_revision", "previous_fence", "new_fence")
    if any(
        isinstance(value.get(field), bool)
        or not isinstance(value.get(field), int)
        or cast(int, value[field]) < 0
        or cast(int, value[field]) > MAX_COUNTER
        for field in counters
    ):
        _fail("invalid_task_metadata", "stored idempotency receipt counters are invalid")
    previous_revision = cast(int, value["previous_revision"])
    new_revision = cast(int, value["new_revision"])
    previous_fence = cast(int, value["previous_fence"])
    new_fence = cast(int, value["new_fence"])
    from_status = cast(str, value["from_status"])
    to_status = cast(str, value["to_status"])
    actor = cast(str, value["actor"])
    run_id = cast(str, value["run_id"])
    task_id = cast(str, value["task_id"])
    lease = value["lease_expires_at"]
    if (
        value["schema_version"] != SCHEMA_VERSION
        or value["idempotency_key"] != key
        or value["request_digest"] != digest
        or value["idempotent_replay"] is not False
        or not _TASK_ID.fullmatch(task_id)
        or not _TOKEN.fullmatch(actor)
        or not _TOKEN.fullmatch(run_id)
        or to_status not in SUPPORTED_TRANSITIONS.get(from_status, set())
        or new_revision != previous_revision + 1
        or new_fence != previous_fence + 1
        or previous_revision != previous_fence
        or new_revision != new_fence
        or not _DIGEST.fullmatch(cast(str, value["canonical_post_write_sha256"]))
        or value["canonical_post_write_sha256"] == ZERO_DIGEST
    ):
        _fail("invalid_task_metadata", "stored idempotency receipt binding is invalid")
    try:
        occurred = _parse_utc(cast(str, value["occurred_at"]), label="stored_receipt_occurred_at")
        lease_value = _parse_utc(lease, label="stored_receipt_lease_expires_at") if isinstance(lease, str) else None
    except TaskpoolCASError:
        _fail("invalid_task_metadata", "stored idempotency receipt time is invalid")
    if lease is not None and not isinstance(lease, str):
        _fail("invalid_task_metadata", "stored idempotency receipt lease has an invalid type")
    if to_status in CLAIM_BOUND:
        if lease_value is None or lease_value <= occurred:
            _fail("invalid_task_metadata", "stored claim-bound receipt requires a later lease")
    elif lease is not None:
        _fail("invalid_task_metadata", "stored non-claim receipt retains a lease")
    return value


def _validate_replay_receipt(receipt: dict[str, object], request: dict[str, object]) -> None:
    expected = {
        "task_id": request["task_id"],
        "idempotency_key": request["idempotency_key"],
        "request_digest": _request_digest(request),
        "from_status": request["expected_status"],
        "to_status": request["to_status"],
        "previous_revision": request["expected_revision"],
        "previous_fence": request["expected_revision"],
        "actor": request["actor"],
        "run_id": request["run_id"],
    }
    if any(receipt.get(field) != expected_value for field, expected_value in expected.items()):
        _fail("invalid_task_metadata", "stored replay receipt does not bind the exact request")
    expected_fence = request["expected_fence"]
    from_status = cast(str, request["expected_status"])
    if from_status in CLAIM_BOUND and expected_fence is None:
        _fail("invalid_task_metadata", "stored claim-bound replay omits its required fence")
    if expected_fence is not None and receipt.get("previous_fence") != expected_fence:
        _fail("invalid_task_metadata", "stored replay receipt does not bind the requested fence")
    to_status = cast(str, request["to_status"])
    reason = cast(str, request["reason"])
    evidence = cast(list[str], request["evidence"])
    requested_lease = request["lease_expires_at"]
    if to_status in {"retry", "blocked"} and not reason.strip():
        _fail("invalid_task_metadata", "stored replay request omits its required reason")
    if to_status == "review" and (
        not evidence or any(not item.startswith(EVIDENCE_PREFIXES) for item in evidence)
    ):
        _fail("invalid_task_metadata", "stored review replay request has invalid evidence")
    if to_status == "claimed":
        if requested_lease is None or receipt.get("lease_expires_at") != requested_lease:
            _fail("invalid_task_metadata", "stored claim replay lease does not bind the exact request")
    elif requested_lease is not None:
        _fail("invalid_task_metadata", "stored non-claim replay request supplies a forbidden lease")
    if to_status not in CLAIM_BOUND and receipt.get("lease_expires_at") is not None:
        _fail("invalid_task_metadata", "stored replay receipt retains a forbidden lease")


def _validate_integer(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_COUNTER:
        _fail(f"invalid_{label}", f"{label} must be an unsigned 63-bit integer")
    return value


def _frontmatter_key_candidate(line: str) -> str | None:
    """Decode conservative YAML scalar key spellings for ambiguity checks."""

    if ":" not in line:
        return None
    raw_key = line.split(":", 1)[0].strip()
    if raw_key.startswith("!!str "):
        raw_key = raw_key.removeprefix("!!str ").strip()
    elif raw_key.startswith("!<tag:yaml.org,2002:str> "):
        raw_key = raw_key.removeprefix("!<tag:yaml.org,2002:str> ").strip()
    elif raw_key.startswith("!") and " " in raw_key:
        raw_key = raw_key.split(" ", 1)[1].strip()
    if raw_key.startswith("&") and " " in raw_key:
        raw_key = raw_key.split(" ", 1)[1].strip()
    if len(raw_key) >= 2 and raw_key[0] == raw_key[-1] and raw_key[0] in {'"', "'"}:
        try:
            decoded = ast.literal_eval(raw_key)
        except (SyntaxError, ValueError):
            return None
        return decoded if isinstance(decoded, str) else None
    return raw_key


@dataclass(frozen=True)
class _TaskNote:
    lines: tuple[str, ...]
    body: str
    positions: dict[str, int]
    values: dict[str, str]

    def render(self, updates: dict[str, str], *, body: str | None = None) -> bytes:
        lines = list(self.lines)
        positions = dict(self.positions)
        for key, value in updates.items():
            rendered = f"{key}: {value}"
            if key in positions:
                lines[positions[key]] = rendered
            else:
                positions[key] = len(lines)
                lines.append(rendered)
        text = "---\n" + "\n".join(lines) + "\n---\n" + (self.body if body is None else body)
        return text.encode("utf-8")


def _parse_note(data: bytes) -> _TaskNote:
    if len(data) > MAX_NOTE_BYTES:
        _fail("invalid_task_note", "task note exceeds the bounded parser size")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _fail("invalid_task_note", "task note is not valid UTF-8")
    if "\0" in text or "\r" in text or not text.startswith("---\n"):
        _fail("invalid_task_note", "task note has invalid frontmatter framing")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        _fail("invalid_task_note", "task note is missing its closing frontmatter delimiter")
    raw = text[4:closing]
    body = text[closing + 5 :]
    if any(separator in raw for separator in ("\u0085", "\u2028", "\u2029")):
        _fail("invalid_task_note", "non-LF YAML line separators are not allowed in task frontmatter")
    lines = raw.split("\n")
    positions: dict[str, int] = {}
    values: dict[str, str] = {}
    relevant = {"type", "status", *OWNED_FIELDS}
    for index, line in enumerate(lines):
        if "\t" in line:
            _fail("invalid_task_note", "tabs are not allowed in task frontmatter")
        stripped_line = line.lstrip()
        if stripped_line.startswith(("?", ":", "*", "!", "&", "<<:")):
            _fail("invalid_task_note", "ambiguous YAML key indirection is not allowed")
        match = _TOP_LEVEL.fullmatch(line)
        candidate_key = _frontmatter_key_candidate(line)
        if candidate_key == "<<":
            _fail("invalid_task_note", "YAML merge-key indirection is not allowed")
        if candidate_key in relevant and (match is None or match.group(1) != candidate_key):
            code = "invalid_task_metadata" if candidate_key in OWNED_FIELDS else "invalid_task_note"
            _fail(code, f"ambiguous spelling of owned top-level field: {candidate_key}")
        if not match:
            continue
        key, raw_value = match.groups()
        if key not in relevant:
            continue
        if key in positions:
            code = "invalid_task_metadata" if key in OWNED_FIELDS else "invalid_task_note"
            _fail(code, f"duplicate top-level field: {key}")
        positions[key] = index
        values[key] = raw_value.strip()
    if values.get("type") != "task":
        _fail("invalid_task_note", "task note must contain exactly one plain `type: task`")
    status_value = values.get("status")
    if status_value not in KNOWN_STATUSES:
        _fail("invalid_task_note", "task note has a missing or unknown status")
    return _TaskNote(tuple(lines), body, positions, values)


def _validate_idempotency_history(
    note: _TaskNote,
    *,
    status_value: str,
    revision: int,
    owner: str,
    run_id: str,
    lease: str,
    idempotency: dict[str, object],
) -> None:
    if len(idempotency) != revision:
        _fail("invalid_task_metadata", "idempotency history length does not match revision")
    if revision == 0:
        return
    by_revision: dict[int, tuple[str, dict[str, object]]] = {}
    for key, raw_entry in idempotency.items():
        entry = cast(dict[str, object], raw_entry)
        digest = cast(str, entry["request_digest"])
        receipt = _validate_stored_receipt(key, digest, entry["receipt"])
        new_revision = cast(int, receipt["new_revision"])
        if new_revision in by_revision:
            _fail("invalid_task_metadata", "idempotency history duplicates a revision")
        by_revision[new_revision] = (key, receipt)
    if set(by_revision) != set(range(1, revision + 1)):
        _fail("invalid_task_metadata", "idempotency history has a revision gap")
    first_receipt = by_revision[1][1]
    if first_receipt["from_status"] in CLAIM_BOUND:
        _fail("invalid_task_metadata", "idempotency history starts from an impossible legacy claim")
    previous: dict[str, object] | None = None
    for index in range(1, revision + 1):
        _, receipt = by_revision[index]
        if previous is not None:
            if receipt["from_status"] != previous["to_status"]:
                _fail("invalid_task_metadata", "idempotency history status chain is discontinuous")
            previous_occurred = _parse_utc(
                cast(str, previous["occurred_at"]), label="stored_receipt_occurred_at"
            )
            occurred = _parse_utc(
                cast(str, receipt["occurred_at"]), label="stored_receipt_occurred_at"
            )
            if occurred < previous_occurred:
                _fail("invalid_task_metadata", "idempotency history occurrence time is discontinuous")
            if receipt["from_status"] in CLAIM_BOUND:
                if receipt["actor"] != previous["actor"] or receipt["run_id"] != previous["run_id"]:
                    _fail("invalid_task_metadata", "idempotency history claim authority is discontinuous")
                previous_lease = _parse_utc(
                    cast(str, previous["lease_expires_at"]), label="stored_receipt_lease_expires_at"
                )
                if occurred >= previous_lease:
                    _fail("invalid_task_metadata", "idempotency history uses expired claim authority")
                if (
                    receipt["to_status"] in CLAIM_BOUND
                    and receipt["lease_expires_at"] != previous["lease_expires_at"]
                ):
                    _fail("invalid_task_metadata", "idempotency history claim lease is discontinuous")
        previous = receipt
    latest_key, latest = by_revision[revision]
    if latest["to_status"] != status_value:
        _fail("invalid_task_metadata", "latest idempotency receipt does not match task status")
    if status_value in CLAIM_BOUND:
        if latest["actor"] != owner or latest["run_id"] != run_id or latest["lease_expires_at"] != lease:
            _fail("invalid_task_metadata", "latest receipt does not bind current claim metadata")
    normalized = cast(dict[str, object], copy.deepcopy(idempotency))
    latest_entry = cast(dict[str, object], normalized[latest_key])
    latest_receipt = cast(dict[str, object], latest_entry["receipt"])
    latest_receipt["canonical_post_write_sha256"] = ZERO_DIGEST
    normalized_bytes = note.render(
        {"taskpool_idempotency_v1": _canonical_json(normalized)},
        body=note.body,
    )
    expected_digest = "sha256:" + hashlib.sha256(normalized_bytes).hexdigest()
    if latest["canonical_post_write_sha256"] != expected_digest:
        _fail("invalid_task_metadata", "latest receipt does not bind canonical task bytes")


def _parse_metadata(note: _TaskNote) -> tuple[int, int, str, str, str, dict[str, object]]:
    present = {key for key in OWNED_FIELDS if key in note.positions}
    status_value = note.values["status"]
    if not present:
        if status_value in CLAIM_BOUND:
            _fail("invalid_task_metadata", "claim-bound legacy task lacks fencing metadata")
        return 0, 0, "", "", "", {}
    if not set(COUNTER_FIELDS).issubset(present):
        _fail("invalid_task_metadata", "owned metadata requires both revision and fence")
    try:
        revision_raw = note.values["taskpool_revision"]
        fence_raw = note.values["taskpool_fence"]
    except KeyError:
        _fail("invalid_task_metadata", "owned metadata counters are incomplete")
    if (
        not _INTEGER.fullmatch(revision_raw)
        or not _INTEGER.fullmatch(fence_raw)
        or len(revision_raw) > 19
        or len(fence_raw) > 19
    ):
        _fail("invalid_task_metadata", "revision and fence must be canonical non-negative integers")
    revision = int(revision_raw)
    fence = int(fence_raw)
    if revision > MAX_COUNTER or fence > MAX_COUNTER:
        _fail("invalid_task_metadata", "revision and fence exceed the unsigned 63-bit bound")
    if revision != fence:
        _fail("invalid_task_metadata", "revision and fence must advance in lockstep")
    owner = note.values.get("taskpool_claimed_by", "")
    run_id = note.values.get("taskpool_run_id", "")
    lease = note.values.get("taskpool_claim_expires_at", "")
    raw_map = note.values.get("taskpool_idempotency_v1", "{}")
    idempotency = _strict_json_object(raw_map)
    try:
        canonical_idempotency = _canonical_json(idempotency)
        canonical_idempotency_bytes = canonical_idempotency.encode("utf-8")
    except (RecursionError, ValueError, OverflowError, UnicodeEncodeError):
        _fail("invalid_task_metadata", "idempotency map cannot be rendered as canonical JSON")
    if canonical_idempotency != raw_map:
        _fail("invalid_task_metadata", "idempotency map must use canonical JSON bytes")
    if len(canonical_idempotency_bytes) > MAX_IDEMPOTENCY_BYTES:
        _fail("invalid_task_metadata", "idempotency map exceeds its byte bound")
    if len(idempotency) > MAX_IDEMPOTENCY_ENTRIES:
        _fail("invalid_task_metadata", "idempotency map exceeds its entry bound")
    for key, entry in idempotency.items():
        if not isinstance(key, str) or not _TOKEN.fullmatch(key) or not isinstance(entry, dict):
            _fail("invalid_task_metadata", "idempotency map contains an invalid entry")
        entry = cast(dict[str, object], entry)
        if set(entry) != {"request_digest", "receipt"}:
            _fail("invalid_task_metadata", "idempotency map entry has an invalid exact shape")
        digest = entry.get("request_digest")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            _fail("invalid_task_metadata", "idempotency map entry is incomplete")
        _validate_stored_receipt(key, digest, entry.get("receipt"))
    claim_values = (owner, run_id, lease)
    if status_value in CLAIM_BOUND:
        if not all(claim_values):
            _fail("invalid_task_metadata", "claim-bound task requires owner, run ID, and lease")
        if not _TOKEN.fullmatch(owner) or not _TOKEN.fullmatch(run_id):
            _fail("invalid_task_metadata", "claim-bound task has invalid owner or run ID")
        try:
            _parse_utc(lease, label="stored_claim_expires_at")
        except TaskpoolCASError:
            _fail("invalid_task_metadata", "claim-bound task has an invalid lease")
    elif any(claim_values):
        _fail("invalid_task_metadata", "non-claim-bound task retains claim metadata")
    _validate_idempotency_history(
        note,
        status_value=status_value,
        revision=revision,
        owner=owner,
        run_id=run_id,
        lease=lease,
        idempotency=idempotency,
    )
    return revision, fence, owner, run_id, lease, idempotency


def _absolute_path(path: Path) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if ".." in path.parts:
        _fail("unsafe_task_root", "task root may not contain parent references")
    return path.absolute()


def _no_follow() -> int:
    value = int(getattr(os, "O_NOFOLLOW", 0))
    if not value or not hasattr(os, "O_DIRECTORY"):
        _fail("platform_unsupported", "descriptor-relative no-follow directories are required")
    return value


def _close_descriptor(descriptor: int) -> None:
    """Best-effort finalization that cannot replace the primary transaction result."""

    try:
        os.close(descriptor)
    except BaseException:
        pass


def _open_existing_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | _no_follow()
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            try:
                if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise OSError("not a directory")
            except BaseException:
                _close_descriptor(next_descriptor)
                raise
            _close_descriptor(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        _close_descriptor(descriptor)
        raise


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _verify_root(root: Path, descriptor: int, pinned: os.stat_result) -> None:
    verification_descriptor: int | None = None
    try:
        current_fd = os.fstat(descriptor)
        verification_descriptor = _open_existing_directory(root)
        current_path = os.fstat(verification_descriptor)
    except (OSError, ValueError, UnicodeError, TaskpoolCASError) as exc:
        _fail("root_replaced", f"task root identity is unavailable: {exc}")
    finally:
        if verification_descriptor is not None:
            _close_descriptor(verification_descriptor)
    if (
        not stat.S_ISDIR(current_fd.st_mode)
        or not stat.S_ISDIR(current_path.st_mode)
        or not _same_identity(current_fd, pinned)
        or not _same_identity(current_path, pinned)
    ):
        _fail("root_replaced", "task root identity changed during the transaction")


def _open_task(root_descriptor: int, name: str) -> int:
    flags = os.O_RDONLY | _no_follow()
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(name, flags, dir_fd=root_descriptor)
    try:
        info = os.fstat(descriptor)
    except BaseException:
        _close_descriptor(descriptor)
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _close_descriptor(descriptor)
        _fail("unsafe_task_path", "task leaf must be a regular single-link file")
    return descriptor


def _verify_task(root_descriptor: int, name: str, pinned: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError as exc:
        _fail("task_replaced", f"task leaf identity is unavailable: {exc}")
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or not _same_identity(current, pinned):
        _fail("task_replaced", "task leaf identity changed during the transaction")


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_NOTE_BYTES:
            _fail("invalid_task_note", "task note exceeds the bounded parser size")
        chunks.append(chunk)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _fsync_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _cleanup_temp(root_descriptor: int, name: str, descriptor: int | None, identity: os.stat_result | None) -> None:
    if descriptor is None:
        return
    try:
        if identity is None:
            identity = os.fstat(descriptor)
        fd_info = os.fstat(descriptor)
        path_info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if (
            stat.S_ISREG(fd_info.st_mode)
            and stat.S_ISREG(path_info.st_mode)
            and fd_info.st_nlink == 1
            and path_info.st_nlink == 1
            and _same_identity(fd_info, identity)
            and _same_identity(path_info, identity)
        ):
            os.unlink(name, dir_fd=root_descriptor)
    except BaseException:
        # Best-effort finalization must never override the transaction's primary
        # success/failure classification, including post-attempt durability_unknown.
        pass


def _audit_body(body: str, *, now: str, from_status: str, to_status: str, actor: str, digest: str) -> str:
    prefix = body if body.endswith("\n") else body + "\n"
    return prefix + f"\n- {now}: Taskpool CAS transition `{from_status}` -> `{to_status}` by `{actor}`. Request: `{digest}`\n"


def _normalized_note_digest(note: _TaskNote, updates: dict[str, str], body: str, key: str, entry: dict[str, object]) -> str:
    normalized_entry = cast(dict[str, object], copy.deepcopy(entry))
    receipt = normalized_entry["receipt"]
    assert isinstance(receipt, dict)
    receipt = cast(dict[str, object], receipt)
    receipt["canonical_post_write_sha256"] = ZERO_DIGEST
    idempotency = json.loads(updates["taskpool_idempotency_v1"])
    idempotency[key] = normalized_entry
    normalized_updates = dict(updates)
    normalized_updates["taskpool_idempotency_v1"] = _canonical_json(idempotency)
    return "sha256:" + hashlib.sha256(note.render(normalized_updates, body=body)).hexdigest()


def _validate_request(
    *,
    task_id: str,
    expected_status: str,
    expected_revision: int,
    expected_fence: int | None,
    to_status: str,
    actor: str,
    run_id: str,
    lease_expires_at: str | None,
    reason: str,
    evidence: Sequence[str],
    idempotency_key: str,
) -> tuple[dict[str, object], datetime | None]:
    if type(task_id) is not str or not _TASK_ID.fullmatch(task_id) or task_id in {".", ".."}:
        _fail("invalid_task_id", "task_id must be one safe basename without an extension suffix requirement")
    if (
        type(expected_status) is not str
        or type(to_status) is not str
        or expected_status not in KNOWN_STATUSES
        or to_status not in KNOWN_STATUSES
    ):
        _fail("invalid_status", "status is unknown")
    revision = _validate_integer(expected_revision, label="expected_revision")
    if expected_fence is not None:
        fence = _validate_integer(expected_fence, label="expected_fence")
    else:
        fence = None
    actor_value = _validated_token(actor, label="actor")
    run_value = _validated_token(run_id, label="run_id")
    key_value = _validated_token(idempotency_key, label="idempotency_key")
    reason_value = _validated_text(reason, label="reason")
    if type(evidence) not in (list, tuple):
        _fail("invalid_evidence", "evidence must be a sequence of clean strings")
    try:
        if len(evidence) > MAX_EVIDENCE_ITEMS:
            _fail("invalid_evidence", "evidence exceeds its item bound")
        evidence_values = tuple(_validated_text(item, label="evidence", allow_empty=False) for item in evidence)
    except TaskpoolCASError:
        raise
    except Exception:
        _fail("invalid_evidence", "evidence must be a sequence of clean strings")
    lease_value = _parse_utc(lease_expires_at, label="lease_expires_at") if lease_expires_at is not None else None
    request: dict[str, object] = {
        "task_id": task_id,
        "expected_status": expected_status,
        "expected_revision": revision,
        "expected_fence": fence,
        "to_status": to_status,
        "actor": actor_value,
        "run_id": run_value,
        "lease_expires_at": lease_expires_at,
        "reason": reason_value,
        "evidence": list(evidence_values),
        "idempotency_key": key_value,
    }
    return request, lease_value


def transition_task_note(
    *,
    tasks_root: Path,
    task_id: str,
    expected_status: str,
    expected_revision: int,
    expected_fence: int | None,
    to_status: str,
    actor: str,
    run_id: str,
    lease_expires_at: str | None,
    reason: str,
    evidence: Sequence[str],
    idempotency_key: str,
) -> dict[str, object]:
    """Atomically apply one fenced lifecycle transition to an exact task note."""

    if os.name == "nt":  # pragma: no cover - exercised by platform contract inspection
        _fail("platform_unsupported", "4C taskpool CAS requires POSIX descriptor and flock semantics")
    request, requested_lease = _validate_request(
        task_id=task_id,
        expected_status=expected_status,
        expected_revision=expected_revision,
        expected_fence=expected_fence,
        to_status=to_status,
        actor=actor,
        run_id=run_id,
        lease_expires_at=lease_expires_at,
        reason=reason,
        evidence=evidence,
        idempotency_key=idempotency_key,
    )
    digest = _request_digest(request)
    try:
        root = _absolute_path(Path(tasks_root))
        for part in root.parts:
            if "\0" in part or any(0xD800 <= ord(character) <= 0xDFFF for character in part):
                _fail("invalid_tasks_root", "tasks_root contains an invalid path component")
            os.fsencode(part)
    except TaskpoolCASError:
        raise
    except Exception:
        _fail("invalid_tasks_root", "tasks_root is not a valid path")
    name = f"{task_id}.md"
    try:
        root_descriptor = _open_existing_directory(root)
    except TaskpoolCASError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        _fail("unsafe_task_root", f"cannot open task root without following links: {exc}")
    try:
        try:
            pinned_root = os.fstat(root_descriptor)
        except OSError as exc:
            _fail("unsafe_task_root", f"cannot inspect task root descriptor: {exc}")
        if not stat.S_ISDIR(pinned_root.st_mode):
            _fail("unsafe_task_root", "task root is not a directory")
        if pinned_root.st_uid != os.geteuid() or stat.S_IMODE(pinned_root.st_mode) & 0o022:
            _fail("unsafe_task_root", "task root must be owner-controlled and not group/world writable")
        try:
            import fcntl

            fcntl.flock(root_descriptor, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            _fail("platform_unsupported", f"cannot lock task root descriptor: {exc}")
        try:
            _verify_root(root, root_descriptor, pinned_root)
            _hook("after_lock")
            try:
                task_descriptor = _open_task(root_descriptor, name)
            except TaskpoolCASError:
                raise
            except OSError as exc:
                _fail("unsafe_task_path", f"cannot open exact task leaf safely: {exc}")
            try:
                try:
                    pinned_task = os.fstat(task_descriptor)
                except OSError as exc:
                    _fail("unsafe_task_path", f"cannot inspect exact task leaf descriptor: {exc}")
                _verify_task(root_descriptor, name, pinned_task)
                if pinned_task.st_uid != os.geteuid() or stat.S_IMODE(pinned_task.st_mode) & 0o022:
                    _fail("unsafe_task_path", "task leaf must be owner-controlled and not group/world writable")
                try:
                    task_bytes = _read_descriptor(task_descriptor)
                except OSError as exc:
                    _fail("read_failed", f"cannot read canonical task note: {exc}")
                note = _parse_note(task_bytes)
                revision, fence, owner, stored_run_id, stored_lease, idempotency = _parse_metadata(note)
                for raw_entry in idempotency.values():
                    if not isinstance(raw_entry, dict):
                        _fail("invalid_task_metadata", "stored idempotency entry is invalid")
                    entry_value = cast(dict[str, object], raw_entry)
                    raw_receipt = entry_value.get("receipt")
                    if not isinstance(raw_receipt, dict):
                        _fail("invalid_task_metadata", "stored idempotency receipt is invalid")
                    receipt_value = cast(dict[str, object], raw_receipt)
                    if receipt_value.get("task_id") != task_id:
                        _fail("invalid_task_metadata", "stored receipt task ID does not match the canonical leaf")
                existing = idempotency.get(idempotency_key)
                if existing is not None:
                    assert isinstance(existing, dict)
                    existing = cast(dict[str, object], existing)
                    if existing.get("request_digest") != digest:
                        _fail("idempotency_conflict", "idempotency key is bound to another request")
                    stored_receipt = existing.get("receipt")
                    if not isinstance(stored_receipt, dict):
                        _fail("invalid_task_metadata", "stored idempotency receipt is invalid")
                    stored_receipt = cast(dict[str, object], stored_receipt)
                    _validate_replay_receipt(stored_receipt, request)
                    replay = cast(dict[str, object], copy.deepcopy(stored_receipt))
                    replay["idempotent_replay"] = True
                    return replay
                if len(idempotency) >= MAX_IDEMPOTENCY_ENTRIES:
                    _fail("idempotency_capacity_exhausted", "idempotency map is full; no eviction is allowed")
                current_status = note.values["status"]
                if revision != expected_revision:
                    _fail("stale_revision", f"expected revision {expected_revision}, found {revision}")
                if current_status != expected_status:
                    _fail("status_mismatch", f"expected {expected_status}, found {current_status}")
                if to_status not in SUPPORTED_TRANSITIONS.get(current_status, set()):
                    _fail("unsupported_transition", f"unsupported transition: {current_status} -> {to_status}")
                if to_status in {"retry", "blocked"} and not reason.strip():
                    _fail("reason_required", f"transition to {to_status} requires a reason")
                if to_status == "review":
                    evidence_values = cast(list[str], request["evidence"])
                    if not evidence_values:
                        _fail("evidence_required", "transition to review requires structured evidence")
                    if any(not item.startswith(EVIDENCE_PREFIXES) for item in evidence_values):
                        _fail("invalid_evidence", "review evidence uses an unsupported prefix")
                now, now_value = _utc_now()
                if current_status in CLAIM_BOUND:
                    if expected_fence is None:
                        _fail("expected_fence_required", "claim-bound transition requires expected_fence")
                    if expected_fence != fence:
                        _fail("stale_fence", f"expected fence {expected_fence}, found {fence}")
                    if actor != owner:
                        _fail("claim_owner_mismatch", "actor does not own the current claim")
                    if run_id != stored_run_id:
                        _fail("claim_run_mismatch", "run_id does not own the current claim")
                    try:
                        current_lease = _parse_utc(stored_lease, label="stored_claim_expires_at")
                    except TaskpoolCASError:
                        _fail("invalid_task_metadata", "stored claim lease is invalid")
                    if current_lease <= now_value:
                        _fail("lease_expired", "claim lease is expired")
                elif expected_fence is not None and expected_fence != fence:
                    _fail("stale_fence", f"expected fence {expected_fence}, found {fence}")
                if to_status == "claimed":
                    if requested_lease is None or requested_lease <= now_value:
                        _fail("invalid_lease", "claim requires a strictly future UTC lease")
                elif lease_expires_at is not None:
                    _fail("invalid_lease", "only entry into claimed may supply a new lease")

                new_revision = revision + 1
                new_fence = fence + 1
                if to_status == "claimed":
                    new_owner, new_run_id, new_lease = actor, run_id, lease_expires_at or ""
                elif to_status == "running":
                    new_owner, new_run_id, new_lease = owner, stored_run_id, stored_lease
                else:
                    new_owner, new_run_id, new_lease = "", "", ""
                receipt: dict[str, object] = {
                    "schema_version": SCHEMA_VERSION,
                    "task_id": task_id,
                    "idempotency_key": idempotency_key,
                    "request_digest": digest,
                    "from_status": current_status,
                    "to_status": to_status,
                    "previous_revision": revision,
                    "new_revision": new_revision,
                    "previous_fence": fence,
                    "new_fence": new_fence,
                    "actor": actor,
                    "run_id": run_id,
                    "occurred_at": now,
                    "lease_expires_at": new_lease or None,
                    "canonical_post_write_sha256": ZERO_DIGEST,
                    "idempotent_replay": False,
                }
                entry: dict[str, object] = {"request_digest": digest, "receipt": receipt}
                updated_idempotency = copy.deepcopy(idempotency)
                updated_idempotency[idempotency_key] = entry
                updates = {
                    "status": to_status,
                    "taskpool_revision": str(new_revision),
                    "taskpool_fence": str(new_fence),
                    "taskpool_claimed_by": new_owner,
                    "taskpool_run_id": new_run_id,
                    "taskpool_claim_expires_at": new_lease,
                    "taskpool_idempotency_v1": _canonical_json(updated_idempotency),
                }
                audit_body = _audit_body(
                    note.body,
                    now=now,
                    from_status=current_status,
                    to_status=to_status,
                    actor=actor,
                    digest=digest,
                )
                normalized_digest = _normalized_note_digest(note, updates, audit_body, idempotency_key, entry)
                receipt["canonical_post_write_sha256"] = normalized_digest
                updated_idempotency[idempotency_key] = entry
                encoded_map = _canonical_json(updated_idempotency)
                if len(encoded_map.encode()) > MAX_IDEMPOTENCY_BYTES:
                    _fail("idempotency_capacity_exhausted", "idempotency map would exceed its byte bound")
                updates["taskpool_idempotency_v1"] = encoded_map
                candidate = note.render(updates, body=audit_body)
                if len(candidate) > MAX_NOTE_BYTES:
                    _fail("invalid_task_note", "candidate task note exceeds its size bound")

                temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
                temporary_descriptor: int | None = None
                temporary_identity: os.stat_result | None = None
                replaced = False
                replacement_attempted = False
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow()
                    if hasattr(os, "O_CLOEXEC"):
                        flags |= os.O_CLOEXEC
                    temporary_descriptor = os.open(
                        temporary_name,
                        flags,
                        0o600,
                        dir_fd=root_descriptor,
                    )
                    temporary_identity = os.fstat(temporary_descriptor)
                    if not stat.S_ISREG(temporary_identity.st_mode) or temporary_identity.st_nlink != 1:
                        raise OSError("unsafe temporary task leaf")
                    _write_all(temporary_descriptor, candidate)
                    canonical_mode = stat.S_IMODE(pinned_task.st_mode)
                    if os.fstat(temporary_descriptor).st_gid != pinned_task.st_gid:
                        os.fchown(temporary_descriptor, -1, pinned_task.st_gid)
                    os.fchmod(temporary_descriptor, canonical_mode)
                    final_temporary = os.fstat(temporary_descriptor)
                    if (
                        stat.S_IMODE(final_temporary.st_mode) != canonical_mode
                        or final_temporary.st_gid != pinned_task.st_gid
                    ):
                        raise OSError("cannot preserve canonical task mode and group")
                    _fsync_file(temporary_descriptor)
                    _hook("before_replace")
                    _verify_root(root, root_descriptor, pinned_root)
                    _verify_task(root_descriptor, name, pinned_task)
                    replacement_attempted = True
                    os.replace(
                        temporary_name,
                        name,
                        src_dir_fd=root_descriptor,
                        dst_dir_fd=root_descriptor,
                    )
                    replaced = True
                    _hook("after_replace")
                    _fsync_directory(root_descriptor)
                    readback_descriptor = _open_task(root_descriptor, name)
                    try:
                        pinned_readback = os.fstat(readback_descriptor)
                        readback = _read_descriptor(readback_descriptor)
                        _verify_task(root_descriptor, name, pinned_readback)
                        if (
                            stat.S_IMODE(pinned_readback.st_mode) != stat.S_IMODE(pinned_task.st_mode)
                            or pinned_readback.st_gid != pinned_task.st_gid
                        ):
                            _fail(
                                "readback_mismatch",
                                "committed task mode or group differs from the canonical task",
                            )
                        if readback != candidate:
                            _fail("readback_mismatch", "committed task note differs from the candidate")
                        readback_note = _parse_note(readback)
                        rb_revision, rb_fence, _, _, _, rb_idempotency = _parse_metadata(readback_note)
                        if (
                            readback_note.values["status"] != to_status
                            or rb_revision != new_revision
                            or rb_fence != new_fence
                            or idempotency_key not in rb_idempotency
                        ):
                            _fail("readback_mismatch", "committed task metadata does not match the receipt")
                        _verify_root(root, root_descriptor, pinned_root)
                        _verify_task(root_descriptor, name, pinned_readback)
                    finally:
                        _close_descriptor(readback_descriptor)
                    return copy.deepcopy(receipt)
                except TaskpoolCASError as exc:
                    if replacement_attempted:
                        _fail("durability_unknown", f"post-replace state could not be confirmed: {exc}")
                    raise
                except OSError as exc:
                    if replacement_attempted:
                        _fail("durability_unknown", f"post-replace durability is unknown: {exc}")
                    _fail("write_failed", f"task note commit failed before replacement: {exc}")
                except Exception:
                    if replacement_attempted:
                        _fail("durability_unknown", "post-replace durability is unknown")
                    _fail("write_failed", "task note commit failed before replacement")
                except BaseException:
                    if replacement_attempted:
                        _fail("durability_unknown", "post-replace durability is unknown")
                    raise
                finally:
                    if not replaced:
                        _cleanup_temp(root_descriptor, temporary_name, temporary_descriptor, temporary_identity)
                    if temporary_descriptor is not None:
                        _close_descriptor(temporary_descriptor)
            finally:
                _close_descriptor(task_descriptor)
        finally:
            try:
                fcntl.flock(root_descriptor, fcntl.LOCK_UN)
            except BaseException:
                pass
    finally:
        _close_descriptor(root_descriptor)
