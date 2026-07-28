"""Durable append-only evidence ledger for admitted workflow executions.

This ledger records authority-neutral observations around the local graph runtime.
It does not grant execution authority and it deliberately distinguishes an
observed effect outcome from an exactly-once or committed external effect.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, NoReturn, cast

import jsonschema

from workflow_kernel.runtime_state import (
    PRIVATE_FILE_MODE,
    LedgerAnchor,
    exclusive_ledger_lock,
    open_ledger_file,
    read_ledger_descriptor_bytes,
)

_SCHEMA_NAME = "workflow-execution-event-v1.schema.json"
_WRITE_SIDE_EFFECTS = {"local_write", "external_write"}
_TASK_KINDS = {
    "task_started",
    "task_succeeded",
    "task_failed",
    "task_skipped",
    "task_blocked",
    "effect_intent",
    "effect_observed",
    "effect_planned",
}
_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})$"
)


class WorkflowExecutionEventLedgerError(ValueError):
    """Stable fail-closed event-contract or storage error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowExecutionEventLedgerError(code, message)


@lru_cache(maxsize=1)
def _validator() -> jsonschema.Draft202012Validator:
    text = resources.files("workflow_kernel.schemas").joinpath(_SCHEMA_NAME).read_text(
        encoding="utf-8"
    )
    schema = json.loads(text)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _canonical_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_owner": identity["session_owner"],
        "lease_id": identity["lease_id"],
        "lease_expires_at": identity["lease_expires_at"],
        "fencing_token": identity["fencing_token"],
        "workflow_id": identity["workflow_id"],
        "definition_digest": identity["definition_digest"],
        "plan_digest": identity["plan_digest"],
        "binding_digest": identity["binding_digest"],
        "execution_mode": identity["execution_mode"],
        "policy_digest": identity["policy_digest"],
        "approval_digest": identity["approval_digest"],
    }


def _canonical_body(body: Mapping[str, Any]) -> dict[str, Any]:
    sequence = body["sequence"]
    if isinstance(sequence, float) and sequence.is_integer():
        sequence = int(sequence)
    identity = body["identity"]
    fencing_token = identity["fencing_token"]
    if isinstance(fencing_token, float) and fencing_token.is_integer():
        fencing_token = int(fencing_token)
    canonical_identity = _canonical_identity(identity)
    canonical_identity["fencing_token"] = fencing_token
    return {
        "schema_version": body["schema_version"],
        "run_id": body["run_id"],
        "sequence": sequence,
        "previous_event_id": body["previous_event_id"],
        "idempotency_key": body["idempotency_key"],
        "occurred_at": body["occurred_at"],
        "identity": canonical_identity,
        "kind": body["kind"],
        "task_id": body["task_id"],
        "step_id": body["step_id"],
        "attempt_id": body["attempt_id"],
        "effect_id": body["effect_id"],
        "side_effect": body["side_effect"],
        "evidence_digest": body["evidence_digest"],
        "reason_code": body["reason_code"],
    }


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def derive_execution_approval_digest_v1(
    *,
    run_id: str,
    workflow_id: str,
    plan_digest: str,
    binding_digest: str,
    policy_digest: str,
) -> str:
    """Bind apply evidence to one run identity without granting authority."""

    values = (run_id, workflow_id, plan_digest, binding_digest, policy_digest)
    if not all(type(value) is str and bool(value) for value in values):
        raise ValueError("apply approval inputs must be non-empty strings")
    if any(
        re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
        for value in (plan_digest, binding_digest, policy_digest)
    ):
        raise ValueError("apply approval digests must be canonical sha256 values")
    body = {
        "schema_version": "workflow_runtime_apply_approval_v1",
        "run_id": run_id,
        "workflow_id": workflow_id,
        "plan_digest": plan_digest,
        "binding_digest": binding_digest,
        "policy_digest": policy_digest,
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_identity_semantics(run_id: str, identity: Mapping[str, Any]) -> None:
    mode = identity["execution_mode"]
    policy_digest = identity["policy_digest"]
    approval_digest = identity["approval_digest"]
    if mode == "dry_run":
        if policy_digest is None or approval_digest is not None:
            _fail(
                "execution_mode_identity_invalid",
                "dry-run identity requires policy evidence and forbids approval evidence",
            )
        return
    if policy_digest is None or approval_digest is None:
        _fail(
            "apply_identity_incomplete",
            "apply identity requires policy-bound approval evidence",
        )
    expected = derive_execution_approval_digest_v1(
        run_id=run_id,
        workflow_id=identity["workflow_id"],
        plan_digest=identity["plan_digest"],
        binding_digest=identity["binding_digest"],
        policy_digest=policy_digest,
    )
    if approval_digest != expected:
        _fail(
            "apply_approval_digest_mismatch",
            "approval evidence is not bound to execution identity",
        )


def derive_workflow_execution_event_id_v1(body: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_compact_json(_canonical_body(body))).hexdigest()
    return f"wee_{digest}"


def _canonical_event_bytes(event: Mapping[str, Any]) -> bytes:
    return _compact_json({**_canonical_body(event), "event_id": event["event_id"]})


def _hash_identity(prefix: str, fields: list[object]) -> str:
    digest = hashlib.sha256(_compact_json(fields)).hexdigest()
    return f"{prefix}_{digest}"


def derive_execution_ids_v1(
    *,
    run_id: str,
    plan_digest: str,
    binding_digest: str,
    task_id: str,
    attempt: int,
    side_effect: str,
) -> dict[str, str | None]:
    """Derive stable task/attempt/effect identities from admitted bytes."""

    candidate = {
        "schema_version": "workflow_execution_identity_input_v1",
        "run_id": run_id,
        "plan_digest": plan_digest,
        "binding_digest": binding_digest,
        "task_id": task_id,
        "attempt": attempt,
        "side_effect": side_effect,
    }
    if (
        re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", run_id) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", plan_digest) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", binding_digest) is None
        or re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", task_id) is None
        or type(attempt) is not int
        or attempt < 1
        or side_effect
        not in {"none", "local_read", "local_write", "external_read", "external_write"}
    ):
        _fail("identity_input_invalid", "execution identity input is invalid")
    step_id = _hash_identity(
        "wst", [candidate["run_id"], candidate["plan_digest"], candidate["task_id"]]
    )
    attempt_id = _hash_identity("wat", [step_id, candidate["attempt"]])
    effect_id = (
        _hash_identity("wef", [attempt_id, candidate["binding_digest"], side_effect])
        if side_effect in _WRITE_SIDE_EFFECTS
        else None
    )
    return {"step_id": step_id, "attempt_id": attempt_id, "effect_id": effect_id}


def _validate_kind_shape(event: Mapping[str, Any]) -> None:
    kind = event["kind"]
    task_id = event["task_id"]
    step_id = event["step_id"]
    attempt_id = event["attempt_id"]
    effect_id = event["effect_id"]
    side_effect = event["side_effect"]
    evidence = event["evidence_digest"]
    reason = event["reason_code"]

    task_shape = task_id is not None and step_id is not None
    attempt_shape = task_shape and attempt_id is not None
    effect_shape = attempt_shape and effect_id is not None and side_effect in _WRITE_SIDE_EFFECTS
    no_task = all(value is None for value in (task_id, step_id, attempt_id, effect_id))

    valid = False
    if kind == "run_admitted":
        valid = no_task and side_effect == "none" and evidence is None and reason is None
    elif kind in {
        "run_succeeded",
        "run_dry_run_completed",
        "run_failed",
        "stale_observed",
    }:
        valid = no_task and side_effect == "none"
        if kind in {"run_succeeded", "run_dry_run_completed"}:
            valid = valid and evidence is not None and reason is None
        else:
            valid = valid and reason is not None
    elif kind in {"task_skipped", "task_blocked"}:
        valid = task_shape and attempt_id is None and effect_id is None and evidence is None
        valid = valid and (kind != "task_blocked" or reason is not None)
    elif kind in {"effect_intent", "effect_observed", "effect_planned"}:
        valid = effect_shape and reason is None
        valid = valid and (kind != "effect_intent" or evidence is None)
        valid = valid and (kind not in {"effect_observed", "effect_planned"} or evidence is not None)
    elif kind in {"task_started", "task_succeeded", "task_failed"}:
        expected_effect = side_effect in _WRITE_SIDE_EFFECTS
        valid = attempt_shape and ((effect_id is not None) == expected_effect)
        if kind == "task_started":
            valid = valid and evidence is None and reason is None
        elif kind == "task_succeeded":
            valid = valid and evidence is not None and reason is None
        else:
            valid = valid and evidence is None and reason is not None
    if not valid:
        _fail("event_shape_invalid", f"event kind {kind!r} has an invalid field shape")


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
    if derive_workflow_execution_event_id_v1(event) != event["event_id"]:
        _fail("identity_mismatch", "event_id does not match canonical event body")
    _validate_identity_semantics(event["run_id"], event["identity"])
    _instant(event["occurred_at"])
    _validate_kind_shape(event)
    return event


def create_workflow_execution_event_v1(body: Mapping[str, Any]) -> dict[str, Any]:
    """Create and validate one content-addressed execution event."""

    canonical = _canonical_body(body)
    event = {**canonical, "event_id": derive_workflow_execution_event_id_v1(canonical)}
    return _normalized_event(event)


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
        except WorkflowExecutionEventLedgerError:
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


def _compare_instants(left: tuple[int, str], right: tuple[int, str]) -> int:
    if left[0] != right[0]:
        return (left[0] > right[0]) - (left[0] < right[0])
    width = max(len(left[1]), len(right[1]))
    left_fraction = left[1].ljust(width, "0")
    right_fraction = right[1].ljust(width, "0")
    return (left_fraction > right_fraction) - (left_fraction < right_fraction)


def compare_rfc3339_instants_v1(left: str, right: str) -> int:
    """Compare strict RFC3339 instants without truncating fractional precision."""

    return _compare_instants(_instant(left), _instant(right))


def _identity_mismatch_code(field: str) -> str:
    return {
        "session_owner": "session_owner_mismatch",
        "lease_id": "lease_id_mismatch",
        "lease_expires_at": "lease_expiry_mismatch",
        "fencing_token": "fencing_token_mismatch",
        "workflow_id": "workflow_id_mismatch",
        "definition_digest": "definition_digest_mismatch",
        "plan_digest": "plan_digest_mismatch",
        "binding_digest": "binding_digest_mismatch",
        "execution_mode": "execution_mode_mismatch",
        "policy_digest": "policy_digest_mismatch",
        "approval_digest": "approval_digest_mismatch",
    }[field]


def replay_workflow_execution_events_v1(events: object) -> dict[str, Any]:
    """Replay execution evidence into a deterministic authority-neutral snapshot."""

    if not isinstance(events, list):
        _fail("invalid_events", "events must be a list")
    run_id: str | None = None
    identity: dict[str, Any] | None = None
    last_event_id: str | None = None
    last_time: tuple[int, str] | None = None
    applied = 0
    duplicates = 0
    status = "empty"
    terminal = False
    tasks: dict[str, dict[str, Any]] = {}
    effects: dict[str, dict[str, Any]] = {}
    by_key: dict[str, bytes] = {}
    by_id: dict[str, bytes] = {}

    for candidate in events:
        event = _normalized_event(candidate)
        canonical = _canonical_event_bytes(event)
        existing_key = by_key.get(event["idempotency_key"])
        if existing_key is not None:
            if existing_key == canonical:
                duplicates += 1
                continue
            _fail("idempotency_conflict", "idempotency key maps to a different event")
        existing_id = by_id.get(event["event_id"])
        if existing_id is not None:
            if existing_id == canonical:
                duplicates += 1
                continue
            _fail("event_id_conflict", "event id maps to a different event")
        if terminal:
            _fail("run_terminal", "terminal execution cannot accept another event")
        if event["sequence"] != applied + 1:
            _fail("sequence_mismatch", "event sequence is not contiguous")
        if event["previous_event_id"] != last_event_id:
            _fail("broken_link", "previous_event_id does not match ledger tip")
        occurred_at = _instant(event["occurred_at"])
        if last_time is not None and _compare_instants(occurred_at, last_time) < 0:
            _fail("time_reversal", "event time precedes ledger tip")

        if run_id is None:
            if event["kind"] != "run_admitted":
                _fail("run_not_admitted", "first event must admit the run")
            run_id = event["run_id"]
            identity = event["identity"]
            status = "admitted"
        else:
            if event["run_id"] != run_id:
                _fail("mixed_run", "one ledger cannot contain multiple run ids")
            assert identity is not None
            for field, expected in identity.items():
                if event["identity"][field] != expected:
                    _fail(_identity_mismatch_code(field), f"execution identity field {field} changed")

            kind = event["kind"]
            task_id = event["task_id"]
            if kind == "run_admitted":
                _fail("duplicate_admission", "run can be admitted only once")
            if kind in _TASK_KINDS:
                assert task_id is not None
                expected_ids = derive_execution_ids_v1(
                    run_id=run_id,
                    plan_digest=identity["plan_digest"],
                    binding_digest=identity["binding_digest"],
                    task_id=task_id,
                    attempt=1,
                    side_effect=event["side_effect"],
                )
                if event["step_id"] != expected_ids["step_id"]:
                    _fail("step_id_mismatch", "step id is not derived from admitted identity")
                if event["attempt_id"] is not None and event["attempt_id"] != expected_ids["attempt_id"]:
                    _fail("attempt_id_mismatch", "attempt id is not derived from admitted identity")
                if (
                    kind not in {"task_skipped", "task_blocked"}
                    and event["effect_id"] != expected_ids["effect_id"]
                ):
                    _fail("effect_id_mismatch", "effect id is not derived from admitted identity")

            if kind == "effect_planned":
                if identity["execution_mode"] != "dry_run":
                    _fail("effect_plan_mode_invalid", "effect planning requires dry-run mode")
                if task_id in tasks:
                    _fail("task_already_observed", "task already has execution evidence")
                effect_id = event["effect_id"]
                assert effect_id is not None
                if effect_id in effects:
                    _fail("effect_already_observed", "effect already has evidence")
                tasks[task_id] = {
                    "status": "planned",
                    "step_id": event["step_id"],
                    "attempt_id": event["attempt_id"],
                    "effect_id": effect_id,
                    "side_effect": event["side_effect"],
                }
                effects[effect_id] = {
                    "status": "planned",
                    "task_id": task_id,
                    "attempt_id": event["attempt_id"],
                    "evidence_digest": event["evidence_digest"],
                }
                status = "running"
            elif kind == "task_started":
                if identity["execution_mode"] == "dry_run" and event["side_effect"] in _WRITE_SIDE_EFFECTS:
                    _fail("dry_run_effect_started", "dry-run cannot start a write effect")
                if task_id in tasks:
                    _fail("task_already_observed", "task already has execution evidence")
                tasks[task_id] = {
                    "status": "running",
                    "step_id": event["step_id"],
                    "attempt_id": event["attempt_id"],
                    "effect_id": event["effect_id"],
                    "side_effect": event["side_effect"],
                }
                status = "running"
            elif kind in {"task_skipped", "task_blocked"}:
                if task_id in tasks:
                    _fail("task_already_observed", "task already has execution evidence")
                tasks[task_id] = {
                    "status": "skipped" if kind == "task_skipped" else "blocked",
                    "step_id": event["step_id"],
                    "attempt_id": None,
                    "effect_id": None,
                    "side_effect": event["side_effect"],
                }
                status = "running"
            elif kind == "effect_intent":
                if identity["execution_mode"] != "apply":
                    _fail("effect_intent_mode_invalid", "effect intent requires apply mode")
                task = tasks.get(task_id)
                if task is None or task["status"] != "running":
                    _fail("task_not_running", "effect intent requires a running task")
                effect_id = event["effect_id"]
                assert effect_id is not None
                if effect_id in effects:
                    _fail("effect_already_observed", "effect already has evidence")
                effects[effect_id] = {
                    "status": "uncertain",
                    "task_id": task_id,
                    "attempt_id": event["attempt_id"],
                    "evidence_digest": None,
                }
            elif kind == "effect_observed":
                effect_id = event["effect_id"]
                assert effect_id is not None
                effect = effects.get(effect_id)
                if effect is None or effect["status"] != "uncertain":
                    _fail("effect_intent_missing", "effect outcome requires one open intent")
                effect["status"] = "observed"
                effect["evidence_digest"] = event["evidence_digest"]
            elif kind in {"task_succeeded", "task_failed"}:
                task = tasks.get(task_id)
                if task is None or task["status"] != "running":
                    _fail("task_not_running", "task outcome requires a running task")
                if kind == "task_succeeded" and task["effect_id"] is not None:
                    effect = effects.get(task["effect_id"])
                    if effect is None or effect["status"] != "observed":
                        _fail("effect_outcome_missing", "effectful success requires observed effect evidence")
                task["status"] = "succeeded" if kind == "task_succeeded" else "failed"
                status = "running"
            elif kind == "run_succeeded":
                if identity["execution_mode"] != "apply":
                    _fail("run_success_mode_invalid", "run success requires apply mode")
                if not tasks or any(
                    task["status"] in {"running", "failed", "blocked"}
                    for task in tasks.values()
                ):
                    _fail("run_success_invalid", "run cannot succeed with incomplete or failed tasks")
                if any(effect["status"] == "uncertain" for effect in effects.values()):
                    _fail("effect_uncertain", "run cannot succeed with uncertain effects")
                status = "succeeded"
                terminal = True
            elif kind == "run_dry_run_completed":
                if identity["execution_mode"] != "dry_run":
                    _fail("dry_run_mode_invalid", "dry-run completion requires dry-run mode")
                if not tasks or not any(
                    task["status"] == "planned" for task in tasks.values()
                ):
                    _fail("dry_run_plan_missing", "dry-run must contain a planned effect")
                if any(
                    task["status"] in {"running", "failed", "blocked"}
                    for task in tasks.values()
                ):
                    _fail("dry_run_incomplete", "dry-run has incomplete or failed tasks")
                if any(effect["status"] != "planned" for effect in effects.values()):
                    _fail("dry_run_effect_invalid", "dry-run contains applied effect evidence")
                status = "dry_run"
                terminal = True
            elif kind == "run_failed":
                status = "failed"
                terminal = True
            elif kind == "stale_observed":
                if _compare_instants(occurred_at, _instant(identity["lease_expires_at"])) < 0:
                    _fail("lease_not_expired", "stale observation precedes lease expiry")
                status = "stale"
                terminal = True

        applied += 1
        last_event_id = event["event_id"]
        last_time = occurred_at
        by_key[event["idempotency_key"]] = canonical
        by_id[event["event_id"]] = canonical

    return {
        "schema_version": "workflow_execution_snapshot_v1",
        "run_id": run_id,
        "identity": identity,
        "sequence": applied,
        "last_event_id": last_event_id,
        "status": status,
        "terminal": terminal,
        "tasks": {key: tasks[key] for key in sorted(tasks)},
        "effects": {key: effects[key] for key in sorted(effects)},
        "uncertain_effect_ids": sorted(
            key for key, value in effects.items() if value["status"] == "uncertain"
        ),
        "duplicates": duplicates,
        "authority_granted": False,
        "executable": False,
    }


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


def read_workflow_execution_events(
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


def _rollback_failed_append(
    descriptor: int,
    append_offset: int,
    *,
    code: str,
    message: str,
) -> NoReturn:
    try:
        os.ftruncate(descriptor, append_offset)
        os.fsync(descriptor)
    except OSError as rollback_exc:
        _fail(
            "durability_unknown",
            f"{message}; rollback durability unknown: {rollback_exc}",
        )
    _fail(code, f"{message}; rollback confirmed")


def append_workflow_execution_event(
    ledger_path: Path,
    candidate: object,
    *,
    private_root: Path,
) -> dict[str, Any]:
    """Validate, replay, append once, fsync, and return projected readback."""

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
        if created:
            # Complete all first-event semantic checks before O_CREAT. A rejected
            # admission must not leave an empty inode whose later first success
            # could skip publication durability for the directory entry.
            replay_workflow_execution_events_v1([event])
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
            snapshot = replay_workflow_execution_events_v1(events)
            by_key = {
                item["idempotency_key"]: _canonical_event_bytes(item) for item in events
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
                _fail("event_id_conflict", "event id maps to a different event")
            next_snapshot = replay_workflow_execution_events_v1([*events, event])
            record = canonical + b"\n"
            append_offset = os.lseek(descriptor, 0, os.SEEK_END)
            written = 0
            while written < len(record):
                try:
                    chunk_size = os.write(descriptor, record[written:])
                except OSError as exc:
                    _rollback_failed_append(
                        descriptor,
                        append_offset,
                        code="write_failed",
                        message=f"ledger write failed after {written} bytes: {exc}",
                    )
                if chunk_size <= 0:
                    _rollback_failed_append(
                        descriptor,
                        append_offset,
                        code="short_write",
                        message=f"ledger write stopped after {written} of {len(record)} bytes",
                    )
                written += chunk_size
            try:
                os.fsync(descriptor)
                # An empty pre-existing file can be an orphan from an interrupted
                # first append. Treat its first successful record as publication
                # and durably sync the parent directory again.
                if created or not events:
                    os.fsync(anchor.directory_descriptor)
            except OSError as exc:
                _rollback_failed_append(
                    descriptor,
                    append_offset,
                    code="durability_unknown",
                    message=f"ledger fsync failed: {exc}",
                )
            return {
                "status": "appended",
                "event_id": event["event_id"],
                "count": next_snapshot["sequence"],
                "snapshot": next_snapshot,
            }
        finally:
            os.close(descriptor)


class WorkflowExecutionLedgerSink:
    """Synchronous fail-closed adapter from runtime observations to JSONL evidence."""

    _OBSERVATION_KEYS = {
        "run_id",
        "occurred_at",
        "identity",
        "kind",
        "task_id",
        "step_id",
        "attempt_id",
        "effect_id",
        "side_effect",
        "evidence_digest",
        "reason_code",
    }

    def __init__(
        self,
        ledger_path: Path,
        *,
        private_root: Path,
        require_empty: bool = True,
    ) -> None:
        self._ledger_path = Path(ledger_path)
        self._private_root = Path(private_root)
        self._lock = Lock()
        events = read_workflow_execution_events(
            self._ledger_path, private_root=self._private_root
        )
        if require_empty and events:
            _fail("execution_ledger_not_empty", "durable execution requires an empty ledger")
        snapshot = replay_workflow_execution_events_v1(events)
        self._sequence = cast(int, snapshot["sequence"])
        self._previous_event_id = cast(str | None, snapshot["last_event_id"])

    def __call__(self, observation: Mapping[str, Any]) -> None:
        if not isinstance(observation, Mapping):
            _fail("invalid_observation", "runtime observation must be a mapping")
        if set(observation) != self._OBSERVATION_KEYS:
            _fail("invalid_observation", "runtime observation keys are not canonical")
        identity_value = observation["identity"]
        if not isinstance(identity_value, Mapping):
            _fail("invalid_observation", "runtime observation identity must be a mapping")
        identity = dict(identity_value)
        with self._lock:
            sequence = self._sequence + 1
            fingerprint = hashlib.sha256(
                _compact_json(
                    {
                        "run_id": observation["run_id"],
                        "sequence": sequence,
                        "kind": observation["kind"],
                        "task_id": observation["task_id"],
                    }
                )
            ).hexdigest()
            event = create_workflow_execution_event_v1(
                {
                    "schema_version": "workflow_execution_event_v1",
                    "run_id": observation["run_id"],
                    "sequence": sequence,
                    "previous_event_id": self._previous_event_id,
                    "idempotency_key": f"execution-event-{fingerprint}",
                    "occurred_at": observation["occurred_at"],
                    "identity": identity,
                    "kind": observation["kind"],
                    "step_id": observation["step_id"],
                    "task_id": observation["task_id"],
                    "attempt_id": observation["attempt_id"],
                    "effect_id": observation["effect_id"],
                    "side_effect": observation["side_effect"],
                    "evidence_digest": observation["evidence_digest"],
                    "reason_code": observation["reason_code"],
                }
            )
            result = append_workflow_execution_event(
                self._ledger_path,
                event,
                private_root=self._private_root,
            )
            if result["count"] != sequence:
                _fail("append_readback_mismatch", "durable event readback count changed")
            self._sequence = sequence
            self._previous_event_id = cast(str, result["event_id"])


__all__ = [
    "WorkflowExecutionEventLedgerError",
    "WorkflowExecutionLedgerSink",
    "append_workflow_execution_event",
    "compare_rfc3339_instants_v1",
    "create_workflow_execution_event_v1",
    "derive_execution_approval_digest_v1",
    "derive_execution_ids_v1",
    "derive_workflow_execution_event_id_v1",
    "read_workflow_execution_events",
    "replay_workflow_execution_events_v1",
]
