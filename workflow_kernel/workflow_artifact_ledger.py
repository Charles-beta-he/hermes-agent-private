"""Versioned, typed, append-only WorkflowArtifact ledger.

The artifact ledger governs authoring state. It is deliberately separate from the
execution ledger: definitions are mutable only through typed, revision-bound
mutations, while each governed runtime run still consumes one immutable replayed
definition snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timezone
from decimal import Decimal
from functools import lru_cache
import hashlib
import importlib.resources
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from workflow_kernel.runtime_state import (
    PRIVATE_FILE_MODE,
    LedgerAnchor,
    exclusive_existing_ledger_lock_read_only,
    exclusive_ledger_lock,
    open_ledger_file,
    read_existing_private_file_bytes,
    read_ledger_descriptor_bytes,
)
from workflow_kernel.schema_formats import (
    is_workflow_event_timestamp,
    parse_rfc3339_datetime,
    workflow_format_checker,
)
from workflow_kernel.workflow_definition import (
    WorkflowDefinitionError,
    compile_workflow_definition_v1,
)


_MUTATION_SCHEMA = "workflow-mutation-v1.schema.json"
_EVENT_SCHEMA = "workflow-artifact-event-v1.schema.json"
_SIDE_EFFECT_RANK = {
    "none": 0,
    "local_read": 1,
    "local_write": 2,
    "external_read": 3,
    "external_write": 4,
}


class WorkflowArtifactLedgerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _LedgerWriteFailure(Exception):
    def __init__(self, bytes_written: int, cause: OSError):
        super().__init__(str(cause))
        self.bytes_written = bytes_written
        self.cause = cause


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowArtifactLedgerError(code, message)


@lru_cache(maxsize=4)
def _validator(schema_name: str) -> Validator:
    try:
        resource = importlib.resources.files("workflow_kernel.schemas").joinpath(
            schema_name
        )
        schema = json.loads(resource.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        _fail("schema_unavailable", f"artifact schema {schema_name!r} unavailable: {exc}")
    return Draft202012Validator(schema, format_checker=workflow_format_checker())


def _normalize_json_numbers(value: object) -> object:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalize_json_numbers(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _normalize_json_numbers(item) for key, item in value.items()}
    return value


def _compact_json(value: object) -> bytes:
    return json.dumps(
        _normalize_json_numbers(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return f"sha256:{hashlib.sha256(_compact_json(value)).hexdigest()}"


def derive_workflow_task_digest_v1(task: Mapping[str, Any]) -> str:
    return _sha256(task)


def derive_workflow_artifact_digest_v1(
    artifact_id: str, revision: int, definition: Mapping[str, Any]
) -> str:
    return _sha256(
        {
            "artifact_id": artifact_id,
            "revision": revision,
            "definition": definition,
        }
    )


def derive_workflow_artifact_event_id_v1(body: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in body.items() if key != "event_id"}
    return f"wae_{hashlib.sha256(_compact_json(canonical)).hexdigest()}"


def _schema_validate(schema_name: str, candidate: object) -> None:
    errors = sorted(
        _validator(schema_name).iter_errors(cast(Any, candidate)),
        key=lambda error: tuple(
            f"{type(part).__name__}:{part}" for part in error.absolute_path
        ),
    )
    if errors:
        _fail("schema_invalid", errors[0].message)


def _compile_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_json_numbers(definition)
    if not isinstance(normalized, dict):
        _fail("definition_invalid", "workflow definition must be an object")
    normalized_definition = cast(dict[str, Any], normalized)
    try:
        compile_workflow_definition_v1(normalized_definition)
    except WorkflowDefinitionError as exc:
        _fail("definition_invalid", f"{exc.code}: {exc}")
    return normalized_definition


def _parse_timestamp(value: str) -> tuple[int, Decimal]:
    if not is_workflow_event_timestamp(value):
        _fail("time_invalid", "event timestamp is outside the workflow RFC 3339 profile")
    try:
        parsed = parse_rfc3339_datetime(value)
    except ValueError as exc:
        _fail("time_invalid", str(exc))
    if parsed.tzinfo is None:
        _fail("time_invalid", "event timestamp must carry a timezone")
    utc_value = parsed.astimezone(timezone.utc)
    whole_seconds = (
        utc_value.toordinal() * 86_400
        + utc_value.hour * 3_600
        + utc_value.minute * 60
        + utc_value.second
    )
    fraction_match = re.search(r"\.(\d+)(?:Z|[+-]\d{2}:\d{2})$", value)
    fraction = (
        Decimal(f"0.{fraction_match.group(1)}")
        if fraction_match is not None
        else Decimal(0)
    )
    return whole_seconds, fraction


def _reject_constant(value: str) -> NoReturn:
    _fail("invalid_json", f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key", f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _require_exact(payload: Mapping[str, Any], expected: set[str], kind: str) -> None:
    actual = set(payload)
    if actual != expected:
        _fail(
            "payload_invalid",
            f"{kind} payload keys must be {sorted(expected)}, got {sorted(actual)}",
        )


def _normalized_mutation(candidate: object) -> dict[str, Any]:
    _schema_validate(_MUTATION_SCHEMA, candidate)
    assert isinstance(candidate, dict)
    normalized = _normalize_json_numbers(candidate)
    assert isinstance(normalized, dict)
    return cast(dict[str, Any], normalized)


def _validate_payload(event_type: str, payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _fail("payload_invalid", "event payload must be an object")
    payload = dict(payload)
    if event_type == "artifact_initialized":
        _require_exact(payload, {"revision", "definition", "artifact_digest"}, event_type)
        if isinstance(payload["revision"], bool) or not isinstance(
            payload["revision"], int
        ):
            _fail("payload_invalid", "initial revision must be an integer")
        definition = _compile_definition(payload["definition"])
        if definition["version"] != payload["revision"]:
            _fail("revision_mismatch", "initial definition version does not match revision")
        payload["definition"] = definition
    elif event_type == "mutation_committed":
        _require_exact(
            payload,
            {
                "mutation",
                "previous_revision",
                "new_revision",
                "previous_artifact_digest",
                "new_artifact_digest",
                "definition",
            },
            event_type,
        )
        payload["mutation"] = _normalized_mutation(payload["mutation"])
        for key in ("previous_revision", "new_revision"):
            if isinstance(payload[key], bool) or not isinstance(payload[key], int):
                _fail("payload_invalid", f"{key} must be an integer")
        payload["definition"] = _compile_definition(payload["definition"])
    else:
        _fail("payload_invalid", f"unsupported artifact event type: {event_type}")
    for key in tuple(payload):
        if key.endswith("digest"):
            value = payload[key]
            if not isinstance(value, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", value
            ):
                _fail("payload_invalid", f"{key} must be a sha256 digest")
    return payload


def _normalized_event(candidate: object) -> dict[str, Any]:
    _schema_validate(_EVENT_SCHEMA, candidate)
    if not isinstance(candidate, dict):
        _fail("schema_invalid", "artifact event must be an object")
    event = cast(dict[str, Any], _normalize_json_numbers(candidate))
    event["payload"] = _validate_payload(event["event_type"], event["payload"])
    if derive_workflow_artifact_event_id_v1(event) != event["event_id"]:
        _fail("identity_mismatch", "event_id does not match canonical event body")
    return event


def _authority_widened(current: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    current_authority = current["authority"]
    candidate_authority = candidate["authority"]
    return (
        _SIDE_EFFECT_RANK[candidate_authority["side_effect"]]
        > _SIDE_EFFECT_RANK[current_authority["side_effect"]]
        or not set(candidate_authority["capabilities"]).issubset(
            current_authority["capabilities"]
        )
    )


def _apply_mutation_to_definition(
    current_definition: Mapping[str, Any], mutation: Mapping[str, Any]
) -> dict[str, Any]:
    candidate = deepcopy(dict(current_definition))
    immutable_authority = deepcopy(candidate["authority"])
    tasks = candidate["tasks"]
    assert isinstance(tasks, list)

    for operation in mutation["operations"]:
        op = operation["op"]
        by_id = {task["id"]: task for task in tasks}
        if op == "add_task":
            task = deepcopy(operation["task"])
            if task["id"] in by_id:
                _fail("task_exists", f"task {task['id']!r} already exists")
            task_authority = task["authority"]
            if (
                task_authority["side_effect"] != "none"
                or task_authority["capabilities"]
            ):
                _fail(
                    "authority_widening",
                    "artifact v1 cannot introduce an authority-bearing task",
                )
            tasks.append(task)
        elif op == "replace_task":
            task_id = operation["task_id"]
            current = by_id.get(task_id)
            if current is None:
                _fail("task_unknown", f"task {task_id!r} does not exist")
            if derive_workflow_task_digest_v1(current) != operation["expected_task_digest"]:
                _fail("stale_task", f"task {task_id!r} digest changed")
            replacement = deepcopy(operation["task"])
            if replacement["id"] != task_id:
                _fail("task_identity_change", "replace_task cannot rename a task")
            if _authority_widened(current, replacement):
                _fail("authority_widening", f"task {task_id!r} authority would widen")
            tasks[tasks.index(current)] = replacement
        elif op == "remove_task":
            task_id = operation["task_id"]
            current = by_id.get(task_id)
            if current is None:
                _fail("task_unknown", f"task {task_id!r} does not exist")
            if derive_workflow_task_digest_v1(current) != operation["expected_task_digest"]:
                _fail("stale_task", f"task {task_id!r} digest changed")
            tasks.remove(current)
        elif op == "set_metadata":
            for key in ("title", "goal", "non_goals", "constraints"):
                candidate[key] = deepcopy(operation[key])
        elif op == "set_loop_contract":
            for key in ("loop_policy", "stop_conditions", "handoff"):
                candidate[key] = deepcopy(operation[key])
        else:  # schema validation should make this unreachable
            _fail("operation_unknown", f"unsupported mutation operation: {op}")

    if candidate["authority"] != immutable_authority:
        _fail("authority_widening", "workflow authority is immutable in artifact v1")
    candidate["version"] = mutation["base_revision"] + 1
    return _compile_definition(candidate)


def project_workflow_artifact_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema_version": "workflow_artifact_v1",
        "artifact_id": None,
        "revision": 0,
        "artifact_digest": None,
        "definition": None,
        "event_count": 0,
        "last_event_id": None,
    }
    by_key: dict[str, bytes] = {}
    by_id: dict[str, bytes] = {}
    last_time: tuple[int, Decimal] | None = None
    for raw_event in events:
        event = _normalized_event(raw_event)
        canonical = _compact_json(event)
        existing_key = by_key.get(event["idempotency_key"])
        if existing_key is not None:
            if existing_key == canonical:
                _fail("duplicate_event", "duplicate physical event record")
            _fail("idempotency_conflict", "idempotency key maps to another event")
        existing_id = by_id.get(event["event_id"])
        if existing_id is not None:
            if existing_id == canonical:
                _fail("duplicate_event", "duplicate physical event record")
            _fail("event_id_conflict", "event id maps to another event")
        if event["sequence"] != state["event_count"] + 1:
            _fail("sequence_mismatch", "artifact event sequence is not contiguous")
        if event["previous_event_id"] != state["last_event_id"]:
            _fail("broken_link", "previous_event_id does not match artifact tip")
        if state["artifact_id"] is not None and event["artifact_id"] != state["artifact_id"]:
            _fail("mixed_artifact", "one ledger cannot contain multiple artifacts")
        event_time = _parse_timestamp(event["occurred_at"])
        if last_time is not None and event_time < last_time:
            _fail("time_reversal", "artifact event time precedes ledger tip")

        payload = event["payload"]
        if event["event_type"] == "artifact_initialized":
            if state["event_count"] != 0:
                _fail("invalid_transition", "artifact_initialized must be the first event")
            revision = payload["revision"]
            expected = derive_workflow_artifact_digest_v1(
                event["artifact_id"], revision, payload["definition"]
            )
            if expected != payload["artifact_digest"]:
                _fail("artifact_digest_mismatch", "initial artifact digest is invalid")
            state.update(
                artifact_id=event["artifact_id"],
                revision=revision,
                artifact_digest=expected,
                definition=payload["definition"],
            )
        else:
            if state["event_count"] == 0:
                _fail("invalid_transition", "first event must initialize the artifact")
            mutation = payload["mutation"]
            if mutation["artifact_id"] != state["artifact_id"]:
                _fail("artifact_mismatch", "mutation artifact does not match ledger")
            if (
                mutation["base_revision"] != state["revision"]
                or payload["previous_revision"] != state["revision"]
            ):
                _fail("revision_mismatch", "committed mutation revision is not contiguous")
            if (
                mutation["base_artifact_digest"] != state["artifact_digest"]
                or payload["previous_artifact_digest"] != state["artifact_digest"]
            ):
                _fail("artifact_digest_mismatch", "committed mutation base digest is invalid")
            current_definition = state["definition"]
            if not isinstance(current_definition, Mapping):
                _fail("invalid_transition", "artifact projection has no definition")
            candidate = _apply_mutation_to_definition(current_definition, mutation)
            new_revision = state["revision"] + 1
            if payload["new_revision"] != new_revision:
                _fail("revision_mismatch", "committed mutation new revision is invalid")
            artifact_id = state["artifact_id"]
            if not isinstance(artifact_id, str):
                _fail("invalid_transition", "artifact projection has no identity")
            expected = derive_workflow_artifact_digest_v1(
                artifact_id, new_revision, candidate
            )
            if payload["definition"] != candidate or payload["new_artifact_digest"] != expected:
                _fail("artifact_digest_mismatch", "committed mutation result is invalid")
            state.update(
                revision=new_revision,
                artifact_digest=expected,
                definition=candidate,
            )

        state["event_count"] += 1
        state["last_event_id"] = event["event_id"]
        by_key[event["idempotency_key"]] = canonical
        by_id[event["event_id"]] = canonical
        last_time = event_time
    return state


def _decode_ledger(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        _fail("torn_tail", "artifact ledger does not end with a complete JSONL record")
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
        except WorkflowArtifactLedgerError:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            _fail("invalid_json", f"invalid JSONL record at index {index}: {exc}")
        events.append(_normalized_event(candidate))
    project_workflow_artifact_events(events)
    return events


def _read_events_from_anchor(
    ledger_path: Path, private_root: Path, anchor: LedgerAnchor
) -> list[dict[str, Any]]:
    try:
        descriptor = open_ledger_file(
            ledger_path,
            os.O_RDONLY,
            private_root=private_root,
            anchor=anchor,
        )
    except FileNotFoundError:
        return []
    try:
        return _decode_ledger(read_ledger_descriptor_bytes(descriptor))
    finally:
        os.close(descriptor)


def _read_events_synchronized(
    ledger_path: Path, *, private_root: Path
) -> list[dict[str, Any]]:
    with exclusive_existing_ledger_lock_read_only(
        ledger_path, private_root=private_root
    ) as anchor:
        if anchor is not None:
            return _read_events_from_anchor(ledger_path, private_root, anchor)
    try:
        raw = read_existing_private_file_bytes(ledger_path, private_root=private_root)
    except FileNotFoundError:
        return []
    with exclusive_existing_ledger_lock_read_only(
        ledger_path, private_root=private_root
    ) as anchor:
        if anchor is not None:
            return _read_events_from_anchor(ledger_path, private_root, anchor)
    return _decode_ledger(raw)


def read_workflow_artifact_history(
    ledger_path: Path, *, private_root: Path
) -> list[dict[str, Any]]:
    return _read_events_synchronized(ledger_path, private_root=private_root)


def read_workflow_artifact(
    ledger_path: Path, *, private_root: Path
) -> dict[str, Any]:
    events = _read_events_synchronized(ledger_path, private_root=private_root)
    if not events:
        _fail("artifact_missing", f"workflow artifact ledger is missing: {ledger_path}")
    return project_workflow_artifact_events(events)


def _write_all(descriptor: int, record: bytes) -> None:
    while True:
        try:
            written = os.write(descriptor, record)
            break
        except InterruptedError:
            continue
        except OSError as exc:
            raise _LedgerWriteFailure(0, exc) from exc
    if written != len(record):
        raise _LedgerWriteFailure(
            max(written, 0),
            OSError(
                "artifact ledger append was short; refusing a second write to the record"
            ),
        )


def _receipt_from_event(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    if event["event_type"] == "artifact_initialized":
        previous_revision = 0
        previous_digest = None
        new_revision = payload["revision"]
        new_digest = payload["artifact_digest"]
    else:
        previous_revision = payload["previous_revision"]
        previous_digest = payload["previous_artifact_digest"]
        new_revision = payload["new_revision"]
        new_digest = payload["new_artifact_digest"]
    return {
        "schema_version": "workflow_artifact_receipt_v1",
        "artifact_id": event["artifact_id"],
        "event_id": event["event_id"],
        "idempotency_key": event["idempotency_key"],
        "previous_revision": previous_revision,
        "new_revision": new_revision,
        "previous_artifact_digest": previous_digest,
        "new_artifact_digest": new_digest,
        "readback_verified": True,
    }


def _append_event_transaction(
    *,
    ledger_path: Path,
    private_root: Path,
    artifact_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    occurred_at: str,
) -> dict[str, Any]:
    normalized_payload = _validate_payload(event_type, payload)
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        if anchor is None:
            _fail("lock_anchor_missing", "artifact ledger lock did not provide an anchor")
        descriptor = -1
        try:
            os.stat(anchor.name, dir_fd=anchor.directory_descriptor, follow_symlinks=False)
            created = False
            descriptor = open_ledger_file(
                ledger_path,
                os.O_RDWR,
                private_root=private_root,
                anchor=anchor,
            )
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            events = _decode_ledger(read_ledger_descriptor_bytes(descriptor))
        except FileNotFoundError:
            created = True
            events = []
        directory_sync_required = created or not events
        try:
            projection = project_workflow_artifact_events(events)
            for existing in events:
                if existing["idempotency_key"] != idempotency_key:
                    continue
                if (
                    existing["artifact_id"] == artifact_id
                    and existing["event_type"] == event_type
                    and existing["payload"] == normalized_payload
                ):
                    os.fsync(descriptor)
                    os.fsync(anchor.directory_descriptor)
                    return _receipt_from_event(existing)
                if (
                    event_type == "mutation_committed"
                    and existing["event_type"] == "mutation_committed"
                    and existing["artifact_id"] == artifact_id
                    and existing["payload"]["mutation"] == normalized_payload["mutation"]
                ):
                    os.fsync(descriptor)
                    os.fsync(anchor.directory_descriptor)
                    return _receipt_from_event(existing)
                _fail("idempotency_conflict", "idempotency key maps to another event")

            if event_type == "artifact_initialized":
                if events:
                    _fail("artifact_exists", "workflow artifact is already initialized")
            else:
                if not events:
                    _fail("artifact_missing", "workflow artifact must be initialized first")
                mutation = normalized_payload["mutation"]
                if artifact_id != projection["artifact_id"]:
                    _fail("artifact_mismatch", "mutation artifact does not match ledger")
                if mutation["base_revision"] != projection["revision"]:
                    _fail("stale_revision", "mutation base revision is stale")
                if mutation["base_artifact_digest"] != projection["artifact_digest"]:
                    _fail("stale_digest", "mutation base artifact digest is stale")

            body: dict[str, Any] = {
                "schema_version": "workflow_artifact_event_v1",
                "artifact_id": artifact_id,
                "sequence": len(events) + 1,
                "previous_event_id": events[-1]["event_id"] if events else None,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at,
                "event_type": event_type,
                "payload": normalized_payload,
            }
            event = {**body, "event_id": derive_workflow_artifact_event_id_v1(body)}
            _normalized_event(event)
            project_workflow_artifact_events([*events, event])
            record = _compact_json(event) + b"\n"
            if descriptor < 0:
                descriptor = open_ledger_file(
                    ledger_path,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL,
                    private_root=private_root,
                    anchor=anchor,
                    mode=PRIVATE_FILE_MODE,
                )
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
            record_offset = os.lseek(descriptor, 0, os.SEEK_END)
            try:
                _write_all(descriptor, record)
            except _LedgerWriteFailure as exc:
                try:
                    os.ftruncate(descriptor, record_offset)
                    os.fsync(descriptor)
                    if created:
                        os.unlink(anchor.name, dir_fd=anchor.directory_descriptor)
                    if directory_sync_required:
                        os.fsync(anchor.directory_descriptor)
                except OSError as rollback_exc:
                    _fail(
                        "durability_unknown",
                        "artifact append rollback durability is unknown after "
                        f"{exc.bytes_written} bytes: {rollback_exc}",
                    )
                _fail(
                    "ledger_write_failed",
                    f"artifact append failed after {exc.bytes_written} bytes: {exc.cause}",
                )
            try:
                os.fsync(descriptor)
                if directory_sync_required:
                    os.fsync(anchor.directory_descriptor)
            except OSError as exc:
                _fail("durability_unknown", f"artifact ledger fsync failed: {exc}")

            readback_events = _decode_ledger(read_ledger_descriptor_bytes(descriptor))
            readback = project_workflow_artifact_events(readback_events)
            if (
                readback["last_event_id"] != event["event_id"]
                or readback["artifact_digest"]
                != (
                    normalized_payload["artifact_digest"]
                    if event_type == "artifact_initialized"
                    else normalized_payload["new_artifact_digest"]
                )
            ):
                _fail("readback_mismatch", "artifact commit read-back does not match candidate")
            return _receipt_from_event(event)
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def initialize_workflow_artifact(
    definition: Mapping[str, Any],
    *,
    ledger_path: Path,
    private_root: Path,
    idempotency_key: str,
    now: str,
) -> dict[str, Any]:
    normalized = _compile_definition(definition)
    artifact_id = normalized["workflow_id"]
    revision = normalized["version"]
    digest = derive_workflow_artifact_digest_v1(artifact_id, revision, normalized)
    return _append_event_transaction(
        ledger_path=ledger_path,
        private_root=private_root,
        artifact_id=artifact_id,
        event_type="artifact_initialized",
        payload={
            "revision": revision,
            "definition": normalized,
            "artifact_digest": digest,
        },
        idempotency_key=idempotency_key,
        occurred_at=now,
    )


def _validated_candidate(
    mutation: Mapping[str, Any], state: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    normalized = _normalized_mutation(mutation)
    if normalized["artifact_id"] != state["artifact_id"]:
        _fail("artifact_mismatch", "mutation artifact does not match ledger")
    if normalized["base_revision"] != state["revision"]:
        _fail("stale_revision", "mutation base revision is stale")
    if normalized["base_artifact_digest"] != state["artifact_digest"]:
        _fail("stale_digest", "mutation base artifact digest is stale")
    candidate = _apply_mutation_to_definition(state["definition"], normalized)
    digest = derive_workflow_artifact_digest_v1(
        state["artifact_id"], state["revision"] + 1, candidate
    )
    return candidate, digest


def validate_workflow_mutation(
    mutation: Mapping[str, Any],
    *,
    ledger_path: Path,
    private_root: Path,
) -> dict[str, Any]:
    state = read_workflow_artifact(ledger_path, private_root=private_root)
    candidate, digest = _validated_candidate(mutation, state)
    return {
        "schema_version": "workflow_mutation_validation_v1",
        "valid": True,
        "artifact_id": state["artifact_id"],
        "current_revision": state["revision"],
        "current_artifact_digest": state["artifact_digest"],
        "candidate_revision": state["revision"] + 1,
        "candidate_artifact_digest": digest,
        "candidate_definition": candidate,
    }


def apply_workflow_mutation(
    mutation: Mapping[str, Any],
    *,
    ledger_path: Path,
    private_root: Path,
    now: str,
) -> dict[str, Any]:
    normalized = _normalized_mutation(mutation)
    # Candidate construction here is only a preflight. The transaction repeats
    # it from the locked ledger tip and rejects stale state before appending.
    state = read_workflow_artifact(ledger_path, private_root=private_root)
    for event in read_workflow_artifact_history(ledger_path, private_root=private_root):
        if event["idempotency_key"] != normalized["idempotency_key"]:
            continue
        if (
            event["event_type"] == "mutation_committed"
            and event["payload"]["mutation"] == normalized
        ):
            return _receipt_from_event(event)
        _fail("idempotency_conflict", "idempotency key maps to another event")
    candidate, new_digest = _validated_candidate(normalized, state)
    return _append_event_transaction(
        ledger_path=ledger_path,
        private_root=private_root,
        artifact_id=state["artifact_id"],
        event_type="mutation_committed",
        payload={
            "mutation": normalized,
            "previous_revision": state["revision"],
            "new_revision": state["revision"] + 1,
            "previous_artifact_digest": state["artifact_digest"],
            "new_artifact_digest": new_digest,
            "definition": candidate,
        },
        idempotency_key=normalized["idempotency_key"],
        occurred_at=now,
    )
