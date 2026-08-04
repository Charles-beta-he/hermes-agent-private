"""Canonical append-only execution ledger for the governed local workflow runtime.

The ledger is the only lifecycle authority. Runtime manifests are immutable input
contracts, never mutable state stores. Every admission decision is replayed and
validated while holding the existing private-ledger writer lock.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import re
from datetime import timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from workflow_kernel.schema_formats import (
    is_workflow_event_timestamp,
    parse_rfc3339_datetime,
    workflow_format_checker,
)
from workflow_kernel.runtime_state import (
    PRIVATE_FILE_MODE,
    LedgerAnchor,
    exclusive_existing_ledger_lock_read_only,
    exclusive_ledger_lock,
    open_ledger_file,
    read_existing_private_file_bytes,
    read_ledger_descriptor_bytes,
)


_EVENT_SCHEMA = "workflow-execution-event-v1.schema.json"
_LEAF_SCHEMA = "workflow-leaf-packet-v1.schema.json"
_RESOURCE_KEYS = ("spawns", "wall_ms", "tool_calls", "tokens", "side_effect_ops")
_EVENT_TYPES = {
    "run_accepted",
    "run_started",
    "run_cancel_requested",
    "budget_reserved",
    "budget_settled",
    "budget_released",
    "leaf_started",
    "leaf_packet",
    "run_verified",
    "run_completed",
}


class WorkflowExecutionLedgerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _LedgerWriteFailure(Exception):
    def __init__(self, bytes_written: int, cause: OSError):
        super().__init__(str(cause))
        self.bytes_written = bytes_written
        self.cause = cause


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowExecutionLedgerError(code, message)


@lru_cache(maxsize=2)
def _validator(schema_name: str) -> Validator:
    try:
        resource = importlib.resources.files("workflow_kernel.schemas").joinpath(
            schema_name
        )
        schema = json.loads(resource.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        _fail(
            "schema_unavailable",
            f"execution schema {schema_name!r} could not be loaded: {exc}",
        )
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


def _parse_timestamp(value: str) -> tuple[int, Decimal]:
    if not is_workflow_event_timestamp(value):
        _fail(
            "time_invalid",
            "event timestamp must match the workflow event RFC 3339 profile",
        )
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


def _write_all(descriptor: int, record: bytes) -> None:
    view = memoryview(record)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(descriptor, view[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            raise _LedgerWriteFailure(offset, exc) from exc
        if written <= 0:
            raise _LedgerWriteFailure(
                offset,
                OSError(
                    f"ledger write made no progress at {offset} of {len(record)} bytes"
                ),
            )
        offset += written


def derive_workflow_execution_event_id_v1(body: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in body.items() if key != "event_id"}
    return f"wee_{hashlib.sha256(_compact_json(canonical)).hexdigest()}"


def derive_workflow_leaf_packet_digest_v1(packet: Mapping[str, Any]) -> str:
    """Bind verifier receipts to the complete canonical target packet."""
    return f"sha256:{hashlib.sha256(_compact_json(packet)).hexdigest()}"


def _reject_constant(value: str) -> NoReturn:
    _fail("invalid_json", f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key", f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _schema_validate(schema_name: str, candidate: object) -> None:
    errors = sorted(
        _validator(schema_name).iter_errors(cast(Any, candidate)),
        key=lambda error: tuple(
            f"{type(part).__name__}:{part}" for part in error.absolute_path
        ),
    )
    if errors:
        _fail("schema_invalid", errors[0].message)


def _require_exact(
    payload: Mapping[str, Any], required: set[str], event_type: str
) -> None:
    actual = set(payload)
    if actual != required:
        _fail(
            "payload_invalid",
            f"{event_type} payload keys must be {sorted(required)}, got {sorted(actual)}",
        )


def _resource(value: object, *, field: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(_RESOURCE_KEYS):
        _fail("payload_invalid", f"{field} must contain exactly {_RESOURCE_KEYS}")
    resource = cast(dict[str, object], value)
    result: dict[str, int] = {}
    for key in _RESOURCE_KEYS:
        item = resource[key]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            _fail("payload_invalid", f"{field}.{key} must be a non-negative integer")
        result[key] = item
    return result


def _validate_payload(event_type: str, payload: object) -> dict[str, Any]:
    if event_type not in _EVENT_TYPES:
        _fail("payload_invalid", f"unsupported event_type: {event_type}")
    if not isinstance(payload, dict):
        _fail("payload_invalid", "payload must be an object")
    payload = dict(payload)
    if event_type == "run_accepted":
        _require_exact(
            payload,
            {
                "identity_schema_version",
                "identity",
                "identity_hash",
                "manifest_digest",
                "definition_digest",
                "plan_digest",
                "hard_budget",
                "task_order",
                "verification_task_ids",
            },
            event_type,
        )
        if payload["identity_schema_version"] != 1 or not isinstance(
            payload["identity"], dict
        ):
            _fail("payload_invalid", "run identity schema/version is invalid")
        for digest_key in (
            "identity_hash",
            "manifest_digest",
            "definition_digest",
            "plan_digest",
        ):
            value = payload[digest_key]
            if (
                not isinstance(value, str)
                or not value.startswith("sha256:")
                or len(value) != 71
            ):
                _fail("payload_invalid", f"{digest_key} must be a sha256 digest")
        if _sha256(payload["identity"]) != payload["identity_hash"]:
            _fail(
                "identity_mismatch", "identity_hash does not match canonical identity"
            )
        payload["hard_budget"] = _resource(payload["hard_budget"], field="hard_budget")
        if (
            not isinstance(payload["task_order"], list)
            or not payload["task_order"]
            or any(
                not isinstance(item, str) or not item for item in payload["task_order"]
            )
            or len(set(payload["task_order"])) != len(payload["task_order"])
        ):
            _fail(
                "payload_invalid", "task_order must contain unique non-empty task ids"
            )
        verification_task_ids = payload["verification_task_ids"]
        if (
            not isinstance(verification_task_ids, list)
            or any(
                not isinstance(item, str) or not item for item in verification_task_ids
            )
            or len(set(verification_task_ids)) != len(verification_task_ids)
            or not set(verification_task_ids).issubset(payload["task_order"])
        ):
            _fail("payload_invalid", "verification_task_ids must be unique task ids")
        verifier_targets = payload["identity"].get("verifier_targets")
        if (
            not isinstance(verifier_targets, dict)
            or set(verifier_targets) != set(verification_task_ids)
            or any(
                not isinstance(target, str)
                or target not in payload["task_order"]
                or target == verifier_id
                for verifier_id, target in verifier_targets.items()
            )
        ):
            _fail(
                "payload_invalid",
                "identity.verifier_targets must bind every verifier to one distinct accepted task",
            )
    elif event_type == "run_started":
        _require_exact(payload, set(), event_type)
    elif event_type == "run_cancel_requested":
        _require_exact(payload, {"reason", "message"}, event_type)
        reason = payload["reason"]
        message = payload["message"]
        if reason not in {"cancellation_requested", "cancellation_probe_failed"}:
            _fail("payload_invalid", "invalid cancellation reason")
        if reason == "cancellation_requested" and message is not None:
            _fail("payload_invalid", "ordinary cancellation must not include a message")
        if reason == "cancellation_probe_failed" and (
            not isinstance(message, str) or not message
        ):
            _fail("payload_invalid", "cancellation probe failure requires a message")
    elif event_type == "budget_reserved":
        _require_exact(
            payload, {"reservation_id", "task_id", "amounts", "expires_at"}, event_type
        )
        payload["amounts"] = _resource(payload["amounts"], field="amounts")
        if payload["amounts"]["spawns"] != 1:
            _fail(
                "payload_invalid",
                "each leaf reservation must reserve exactly one spawn",
            )
        for key in ("reservation_id", "task_id", "expires_at"):
            if not isinstance(payload[key], str) or not payload[key]:
                _fail("payload_invalid", f"{key} must be non-empty")
    elif event_type == "budget_settled":
        _require_exact(payload, {"reservation_id", "actual"}, event_type)
        payload["actual"] = _resource(payload["actual"], field="actual")
        if (
            not isinstance(payload["reservation_id"], str)
            or not payload["reservation_id"]
        ):
            _fail("payload_invalid", "reservation_id must be non-empty")
    elif event_type == "budget_released":
        _require_exact(payload, {"reservation_id", "reason"}, event_type)
        if any(
            not isinstance(payload[key], str) or not payload[key]
            for key in ("reservation_id", "reason")
        ):
            _fail("payload_invalid", "reservation_id and reason must be non-empty")
    elif event_type == "leaf_started":
        _require_exact(payload, {"leaf_id", "reservation_id"}, event_type)
        if any(
            not isinstance(payload[key], str) or not payload[key]
            for key in ("leaf_id", "reservation_id")
        ):
            _fail("payload_invalid", "leaf_id and reservation_id must be non-empty")
    elif event_type == "leaf_packet":
        _require_exact(payload, {"packet"}, event_type)
        _schema_validate(_LEAF_SCHEMA, payload["packet"])
        payload["packet"] = dict(payload["packet"])
        payload["packet"]["usage"] = _resource(
            payload["packet"]["usage"], field="packet.usage"
        )
    elif event_type == "run_verified":
        _require_exact(payload, {"verifier_leaf_ids"}, event_type)
        verifier_leaf_ids = payload["verifier_leaf_ids"]
        if (
            not isinstance(verifier_leaf_ids, list)
            or not verifier_leaf_ids
            or any(not isinstance(item, str) or not item for item in verifier_leaf_ids)
            or len(set(verifier_leaf_ids)) != len(verifier_leaf_ids)
        ):
            _fail(
                "payload_invalid",
                "verifier_leaf_ids must contain unique non-empty task ids",
            )
    elif event_type == "run_completed":
        _require_exact(
            payload, {"status", "verification_state", "convergence_state"}, event_type
        )
        if payload["status"] != "run_completed":
            _fail("payload_invalid", "run_completed status must be run_completed")
        if payload["verification_state"] not in {"verified", "unverified"}:
            _fail("payload_invalid", "invalid verification_state")
        if payload["convergence_state"] not in {"not_converged", "converged"}:
            _fail("payload_invalid", "invalid convergence_state")
    return payload


def _normalized_event(candidate: object) -> dict[str, Any]:
    _schema_validate(_EVENT_SCHEMA, candidate)
    if not isinstance(candidate, dict):
        _fail("schema_invalid", "event must be an object")
    event = dict(candidate)
    event["payload"] = _validate_payload(event["event_type"], event["payload"])
    if derive_workflow_execution_event_id_v1(event) != event["event_id"]:
        _fail("identity_mismatch", "event_id does not match canonical event body")
    return event


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
        except WorkflowExecutionLedgerError:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            _fail("invalid_json", f"invalid JSONL record at index {index}: {exc}")
        events.append(_normalized_event(candidate))
    project_execution_events(events)
    return events


def _zero_resource() -> dict[str, int]:
    return {key: 0 for key in _RESOURCE_KEYS}


def _add(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
    return {key: left[key] + right[key] for key in _RESOURCE_KEYS}


def _sum_resources(resources: list[Mapping[str, int]]) -> dict[str, int]:
    result = _zero_resource()
    for resource in resources:
        result = _add(result, resource)
    return result


def project_execution_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": None,
        "identity_schema_version": None,
        "identity": None,
        "identity_hash": None,
        "manifest_digest": None,
        "definition_digest": None,
        "plan_digest": None,
        "hard_budget": _zero_resource(),
        "task_order": [],
        "verification_task_ids": [],
        "verifier_targets": {},
        "cancellation_requested": False,
        "cancellation_reason": None,
        "cancellation_message": None,
        "status": "missing",
        "outcome": None,
        "verification_state": "unverified",
        "convergence_state": "not_converged",
        "reservations": {},
        "closed_reservations": {},
        "settled": _zero_resource(),
        "settlements": {},
        "started": {},
        "leaf_packets_by_id": {},
        "event_count": 0,
        "last_event_id": None,
    }
    by_key: dict[str, bytes] = {}
    by_id: dict[str, bytes] = {}
    last_time: tuple[int, Decimal] | None = None
    for event in events:
        event = _normalized_event(event)
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
        if state["run_id"] is not None and event["run_id"] != state["run_id"]:
            _fail("mixed_run", "one ledger cannot contain multiple run ids")
        if event["sequence"] != state["event_count"] + 1:
            _fail("sequence_mismatch", "event sequence is not contiguous")
        if event["previous_event_id"] != state["last_event_id"]:
            _fail("broken_link", "previous_event_id does not match ledger tip")
        event_time = _parse_timestamp(event["occurred_at"])
        if last_time is not None and event_time < last_time:
            _fail("time_reversal", "event time precedes ledger tip")
        event_type = event["event_type"]
        payload = event["payload"]
        if state["event_count"] == 0 and event_type != "run_accepted":
            _fail("invalid_transition", "first execution event must be run_accepted")
        if state["status"] == "run_completed":
            _fail("invalid_transition", "no events may follow run_completed")
        if event_type == "run_accepted":
            if state["event_count"] != 0:
                _fail("invalid_transition", "run_accepted may appear only once")
            state.update(
                run_id=event["run_id"],
                identity_schema_version=payload["identity_schema_version"],
                identity=payload["identity"],
                identity_hash=payload["identity_hash"],
                manifest_digest=payload["manifest_digest"],
                definition_digest=payload["definition_digest"],
                plan_digest=payload["plan_digest"],
                hard_budget=payload["hard_budget"],
                task_order=list(payload["task_order"]),
                verification_task_ids=list(payload["verification_task_ids"]),
                verifier_targets=dict(payload["identity"]["verifier_targets"]),
                status="accepted",
            )
        elif event_type == "run_started":
            if state["status"] != "accepted":
                _fail("invalid_transition", "run_started requires accepted state")
            state["status"] = "running"
        elif event_type == "run_cancel_requested":
            if state["status"] != "running" or state["cancellation_requested"]:
                _fail(
                    "invalid_transition",
                    "run cancellation requires an uncancelled running state",
                )
            state["cancellation_requested"] = True
            state["cancellation_reason"] = payload["reason"]
            state["cancellation_message"] = payload.get("message")
        elif event_type == "budget_reserved":
            if state["status"] != "running" or state["cancellation_requested"]:
                _fail(
                    "invalid_transition",
                    "budget reservation requires an uncancelled running state",
                )
            rid = payload["reservation_id"]
            task_id = payload["task_id"]
            if task_id not in state["task_order"]:
                _fail("leaf_unknown", "reservation task is not in task_order")
            if (
                rid in state["reservations"]
                or rid in state["closed_reservations"]
                or task_id in state["leaf_packets_by_id"]
                or task_id in state["started"]
            ):
                _fail("reservation_conflict", "reservation or task already exists")
            if any(
                item["task_id"] == task_id for item in state["reservations"].values()
            ):
                _fail(
                    "reservation_conflict",
                    "task already has an outstanding reservation",
                )
            reserved = _sum_resources([
                item["amounts"] for item in state["reservations"].values()
            ])
            projected = _add(_add(state["settled"], reserved), payload["amounts"])
            if any(
                projected[key] > state["hard_budget"][key] for key in _RESOURCE_KEYS
            ):
                _fail(
                    "hard_budget_exhausted",
                    f"reservation for {task_id} exceeds hard budget",
                )
            state["reservations"][rid] = payload
        elif event_type == "budget_settled":
            rid = payload["reservation_id"]
            reservation = state["reservations"].get(rid)
            if reservation is None:
                _fail(
                    "reservation_missing", "settlement has no outstanding reservation"
                )
            if state["started"].get(reservation["task_id"]) != rid:
                _fail(
                    "reservation_not_started",
                    "settlement requires a matching durable leaf start",
                )
            if any(
                payload["actual"][key] > reservation["amounts"][key]
                for key in _RESOURCE_KEYS
            ):
                _fail("reservation_exceeded", "actual usage exceeds reserved budget")
            if payload["actual"]["spawns"] < 1:
                _fail(
                    "started_reservation_refund",
                    "dispatched work must consume at least one reserved spawn",
                )
            state["settled"] = _add(state["settled"], payload["actual"])
            state["settlements"][rid] = payload["actual"]
            state["closed_reservations"][rid] = "settled"
            del state["reservations"][rid]
        elif event_type == "budget_released":
            rid = payload["reservation_id"]
            if rid not in state["reservations"]:
                _fail("reservation_missing", "release has no outstanding reservation")
            if rid in state["started"].values():
                _fail(
                    "started_reservation_release",
                    "dispatched work cannot refund its reservation",
                )
            state["closed_reservations"][rid] = "released"
            del state["reservations"][rid]
        elif event_type == "leaf_started":
            if state["cancellation_requested"]:
                _fail(
                    "invalid_transition",
                    "leaf start is forbidden after cancellation is requested",
                )
            rid = payload["reservation_id"]
            reservation = state["reservations"].get(rid)
            if reservation is None or reservation["task_id"] != payload["leaf_id"]:
                _fail(
                    "reservation_missing", "leaf start is not backed by its reservation"
                )
            prior_reservation = state["started"].get(payload["leaf_id"])
            if prior_reservation is not None:
                _fail("leaf_duplicate", "leaf already has a recorded start")
            state["started"][payload["leaf_id"]] = rid
        elif event_type == "leaf_packet":
            packet = payload["packet"]
            leaf_id = packet["leaf_id"]
            if leaf_id not in state["task_order"]:
                _fail("leaf_unknown", "packet leaf is not in task_order")
            if leaf_id in state["leaf_packets_by_id"]:
                _fail("leaf_duplicate", "leaf already has a terminal packet")
            if (
                packet["status"] not in {"blocked", "cancelled"}
                and leaf_id not in state["started"]
            ):
                _fail(
                    "invalid_transition",
                    "executed terminal packet requires leaf_started",
                )
            if leaf_id in state["started"]:
                rid = state["started"][leaf_id]
                if rid in state["reservations"]:
                    _fail(
                        "reservation_open", "leaf packet cannot precede settle/release"
                    )
                settlement = state["settlements"].get(rid)
                if settlement is None or packet["usage"] != settlement:
                    _fail(
                        "packet_usage_mismatch",
                        "packet usage must equal its durable settlement",
                    )
            elif packet["usage"] != _zero_resource():
                _fail(
                    "packet_usage_mismatch", "non-dispatched packet usage must be zero"
                )
            if packet["status"] == "completed":
                if (
                    not packet["value_present"]
                    or not packet["claims"]
                    or not packet["evidence"]
                    or packet["uncertainty"]
                ):
                    _fail(
                        "packet_semantics_invalid",
                        "completed packet lacks positive evidence semantics",
                    )
            elif not packet["uncertainty"]:
                _fail(
                    "packet_semantics_invalid",
                    "non-completed packet requires uncertainty evidence",
                )
            if not packet["value_present"] and packet["result"] is not None:
                _fail(
                    "packet_semantics_invalid",
                    "missing result must be represented as JSON null",
                )
            state["leaf_packets_by_id"][leaf_id] = packet
        elif event_type == "run_verified":
            if (
                state["status"] != "running"
                or state["verification_state"] != "unverified"
                or state["cancellation_requested"]
            ):
                _fail(
                    "invalid_transition",
                    "run verification requires an uncancelled unverified running state",
                )
            verifier_leaf_ids = payload["verifier_leaf_ids"]
            if verifier_leaf_ids != state["verification_task_ids"]:
                _fail(
                    "verification_inconsistent",
                    "verifier ids do not match the accepted execution plan",
                )
            if set(state["leaf_packets_by_id"]) != set(state["task_order"]):
                _fail(
                    "verification_incomplete",
                    "verification requires all terminal leaf packets",
                )
            if any(
                packet["status"] != "completed"
                for packet in state["leaf_packets_by_id"].values()
            ):
                _fail(
                    "verification_incomplete",
                    "verification requires every leaf to complete",
                )
            if any(
                state["leaf_packets_by_id"][leaf_id]["status"] != "completed"
                or not state["leaf_packets_by_id"][leaf_id]["evidence"]
                for leaf_id in verifier_leaf_ids
            ):
                _fail(
                    "verification_inconsistent",
                    "verifier receipts lack completed evidence",
                )
            for verifier_id in verifier_leaf_ids:
                target = state["verifier_targets"][verifier_id]
                packet = state["leaf_packets_by_id"][verifier_id]
                target_digest = derive_workflow_leaf_packet_digest_v1(
                    state["leaf_packets_by_id"][target]
                )
                if (
                    packet["result"] != {"verified": True, "dependency": target}
                    or {
                        "type": "runtime_receipt",
                        "ref": f"leaf:{target}:{target_digest}",
                    }
                    not in packet["evidence"]
                ):
                    _fail(
                        "verification_inconsistent",
                        "verifier receipt does not bind its accepted target",
                    )
            state["verification_state"] = "verified"
        elif event_type == "run_completed":
            if state["status"] != "running" or state["reservations"]:
                _fail(
                    "invalid_transition",
                    "run completion requires running state and no reservations",
                )
            if set(state["leaf_packets_by_id"]) != set(state["task_order"]):
                _fail("incomplete_run", "every task needs a terminal leaf packet")
            if payload["verification_state"] != state["verification_state"]:
                _fail(
                    "completion_inconsistent",
                    "verification_state requires a preceding explicit verification event",
                )
            if payload["convergence_state"] != "not_converged":
                _fail(
                    "completion_inconsistent",
                    "convergence requires a distinct evidence-bearing event not supported by v1",
                )
            terminal_statuses = {
                packet["status"] for packet in state["leaf_packets_by_id"].values()
            }
            if state["cancellation_reason"] == "cancellation_probe_failed":
                state["outcome"] = "blocked"
            elif state["cancellation_requested"]:
                state["outcome"] = "cancelled"
            else:
                state["outcome"] = next(
                    (
                        outcome
                        for outcome in (
                            "failed",
                            "blocked",
                            "cancelled",
                            "unverified",
                        )
                        if outcome in terminal_statuses
                    ),
                    "completed",
                )
            state["status"] = payload["status"]
            state["verification_state"] = payload["verification_state"]
            state["convergence_state"] = payload["convergence_state"]
        state["event_count"] += 1
        state["last_event_id"] = event["event_id"]
        by_key[event["idempotency_key"]] = canonical
        by_id[event["event_id"]] = canonical
        last_time = event_time
    return state


def _open_existing(
    anchor: LedgerAnchor, ledger_path: Path, private_root: Path
) -> int | None:
    try:
        return open_ledger_file(
            ledger_path,
            os.O_RDONLY,
            private_root=private_root,
            anchor=anchor,
        )
    except FileNotFoundError:
        return None


def read_execution_events(
    ledger_path: Path, *, private_root: Path
) -> list[dict[str, Any]]:
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        if anchor is None:
            _fail("lock_anchor_missing", "ledger lock did not provide an anchor")
        descriptor = _open_existing(anchor, ledger_path, private_root)
        if descriptor is None:
            return []
        try:
            return _decode_ledger(read_ledger_descriptor_bytes(descriptor))
        finally:
            os.close(descriptor)


def _read_events_from_anchor(
    ledger_path: Path,
    private_root: Path,
    anchor: LedgerAnchor,
) -> list[dict[str, Any]]:
    descriptor = _open_existing(anchor, ledger_path, private_root)
    if descriptor is None:
        return []
    try:
        return _decode_ledger(read_ledger_descriptor_bytes(descriptor))
    finally:
        os.close(descriptor)


def read_execution_events_synchronized_read_only(
    ledger_path: Path,
    *,
    private_root: Path,
    blocking: bool = True,
) -> list[dict[str, Any]]:
    """Read without mutation while synchronizing with cooperative ledger writers."""
    try:
        return _read_execution_events_synchronized_read_only(
            ledger_path, private_root=private_root, blocking=blocking
        )
    except BlockingIOError as exc:
        raise WorkflowExecutionLedgerError(
            "ledger_busy", "ledger is busy with an active execution or writer"
        ) from exc


def _read_execution_events_synchronized_read_only(
    ledger_path: Path,
    *,
    private_root: Path,
    blocking: bool,
) -> list[dict[str, Any]]:
    with exclusive_existing_ledger_lock_read_only(
        ledger_path,
        private_root=private_root,
        blocking=blocking,
    ) as anchor:
        if anchor is not None:
            return _read_events_from_anchor(ledger_path, private_root, anchor)

    # A legacy ledger may predate its transaction lock. Read it first, then
    # re-check the lock: if a writer created the lock during our read, acquire
    # it and discard the potentially concurrent bytes before decoding.
    try:
        raw = read_existing_private_file_bytes(ledger_path, private_root=private_root)
    except FileNotFoundError:
        return []
    with exclusive_existing_ledger_lock_read_only(
        ledger_path,
        private_root=private_root,
        blocking=blocking,
    ) as anchor:
        if anchor is not None:
            return _read_events_from_anchor(ledger_path, private_root, anchor)
    return _decode_ledger(raw)


def read_execution_events_read_only(
    ledger_path: Path, *, private_root: Path
) -> list[dict[str, Any]]:
    """Read an existing ledger without creating locks, paths, or changing modes."""
    try:
        raw = read_existing_private_file_bytes(ledger_path, private_root=private_root)
    except FileNotFoundError:
        return []
    return _decode_ledger(raw)


def append_execution_event(
    ledger_path: Path,
    *,
    private_root: Path,
    run_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    occurred_at: str,
) -> dict[str, Any]:
    """Atomically replay, validate, append, fsync, and return ledger projection."""
    normalized_payload = _validate_payload(event_type, payload)
    with exclusive_ledger_lock(ledger_path, private_root=private_root) as anchor:
        if anchor is None:
            _fail("lock_anchor_missing", "ledger lock did not provide an anchor")
        try:
            os.stat(
                anchor.name, dir_fd=anchor.directory_descriptor, follow_symlinks=False
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
            projection = project_execution_events(events)
            for event in events:
                if event["idempotency_key"] != idempotency_key:
                    continue
                if (
                    event["run_id"] == run_id
                    and event["event_type"] == event_type
                    and event["payload"] == normalized_payload
                ):
                    try:
                        os.fsync(descriptor)
                        os.fsync(anchor.directory_descriptor)
                    except OSError as exc:
                        _fail(
                            "durability_unknown",
                            f"duplicate ledger event fsync failed: {exc}",
                        )
                    return projection
                _fail("idempotency_conflict", "idempotency key maps to another event")
            body: dict[str, Any] = {
                "schema_version": "workflow_execution_event_v1",
                "run_id": run_id,
                "sequence": len(events) + 1,
                "previous_event_id": events[-1]["event_id"] if events else None,
                "idempotency_key": idempotency_key,
                "occurred_at": occurred_at,
                "event_type": event_type,
                "payload": normalized_payload,
            }
            event = {**body, "event_id": derive_workflow_execution_event_id_v1(body)}
            _normalized_event(event)
            next_projection = project_execution_events([*events, event])
            record = _compact_json(event) + b"\n"
            record_offset = os.lseek(descriptor, 0, os.SEEK_END)
            try:
                _write_all(descriptor, record)
            except _LedgerWriteFailure as exc:
                try:
                    os.ftruncate(descriptor, record_offset)
                    os.fsync(descriptor)
                    if created:
                        os.unlink(anchor.name, dir_fd=anchor.directory_descriptor)
                        os.fsync(anchor.directory_descriptor)
                except OSError as rollback_exc:
                    _fail(
                        "durability_unknown",
                        "ledger append failed after "
                        f"{exc.bytes_written} bytes and rollback durability is unknown: "
                        f"{rollback_exc}",
                    )
                _fail(
                    "write_failed",
                    f"ledger append failed after {exc.bytes_written} bytes: {exc.cause}",
                )
            try:
                os.fsync(descriptor)
                if created:
                    os.fsync(anchor.directory_descriptor)
            except OSError as exc:
                _fail("durability_unknown", f"ledger fsync failed: {exc}")
            return next_projection
        finally:
            os.close(descriptor)
