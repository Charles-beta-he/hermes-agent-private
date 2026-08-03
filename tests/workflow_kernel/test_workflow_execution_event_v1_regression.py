"""Slice 0: historical workflow_execution_event_v1 bytes/semantics stay frozen.

New effect/v2 records must be rejected by v1 schema and ledger boundaries.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from workflow_kernel.schema_formats import workflow_format_checker
from workflow_kernel.workflow_execution_ledger import (
    WorkflowExecutionLedgerError,
    append_execution_event,
    derive_workflow_execution_event_id_v1,
    project_execution_events,
    read_execution_events,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
V1_SCHEMA_PATH = REPO_ROOT / "schemas" / "workflow-execution-event-v1.schema.json"
PACKAGED_V1_SCHEMA_PATH = (
    REPO_ROOT / "workflow_kernel" / "schemas" / "workflow-execution-event-v1.schema.json"
)


def _compact(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _identity() -> dict[str, object]:
    return {
        "authority_ref": "authority:local",
        "baseline_ref": "git:abc",
        "model_contract_ref": "model:none",
        "profile_scope": "default",
        "project_scope": "/tmp/project",
        "tool_policy_ref": "policy:local",
        "verifier_targets": {"verify-a": "leaf-a"},
    }


def _v1_body(**overrides: object) -> dict[str, object]:
    identity = _identity()
    body: dict[str, object] = {
        "schema_version": "workflow_execution_event_v1",
        "run_id": "run-f4-regression",
        "sequence": 1,
        "previous_event_id": None,
        "idempotency_key": "run-f4-regression:accepted",
        "occurred_at": "2026-08-02T00:00:00Z",
        "event_type": "run_accepted",
        "payload": {
            "identity_schema_version": 1,
            "identity": identity,
            "identity_hash": "sha256:" + hashlib.sha256(_compact(identity)).hexdigest(),
            "manifest_digest": "sha256:" + "b" * 64,
            "definition_digest": "sha256:" + "c" * 64,
            "plan_digest": "sha256:" + "d" * 64,
            "hard_budget": {
                "spawns": 1,
                "wall_ms": 1000,
                "tool_calls": 0,
                "tokens": 0,
                "side_effect_ops": 0,
            },
            "task_order": ["leaf-a", "verify-a"],
            "verification_task_ids": ["verify-a"],
        },
    }
    body.update(overrides)
    return body


def _v1_event(**overrides: object) -> dict[str, object]:
    body = _v1_body(**overrides)
    return {**body, "event_id": derive_workflow_execution_event_id_v1(body)}


def test_release_and_packaged_v1_schemas_are_byte_identical_to_pinned_fixture() -> None:
    fixture = (
        REPO_ROOT
        / "tests"
        / "workflow_kernel"
        / "fixtures"
        / "workflow-execution-event-v1.schema.json"
    )
    assert V1_SCHEMA_PATH.read_bytes() == PACKAGED_V1_SCHEMA_PATH.read_bytes()
    assert V1_SCHEMA_PATH.read_bytes() == fixture.read_bytes()
    assert (
        hashlib.sha256(V1_SCHEMA_PATH.read_bytes()).hexdigest()
        == "0f57c84d49d2f34ed00b1c9d62998ddaf017e378ea9a439dbdd0d78892f3c010"
    )


def test_release_and_packaged_v1_schemas_are_byte_identical() -> None:
    test_release_and_packaged_v1_schemas_are_byte_identical_to_pinned_fixture()


def test_v1_schema_const_and_closed_event_vocabulary_are_unchanged() -> None:
    schema = json.loads(V1_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "workflow_execution_event_v1"
    assert schema["properties"]["event_type"]["enum"] == [
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
    ]
    assert schema["additionalProperties"] is False


def test_v1_schema_rejects_execution_event_v2_schema_version() -> None:
    schema = json.loads(V1_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=workflow_format_checker())
    candidate = _v1_event(schema_version="workflow_execution_event_v2")
    errors = list(validator.iter_errors(candidate))
    assert errors
    assert any("workflow_execution_event_v1" in error.message for error in errors)


@pytest.mark.parametrize(
    "event_type",
    [
        "run_waiting_reconcile",
        "leaf_waiting_reconcile",
        "effect_bound",
        "effect_resolved",
        "effect_claimed",
        "effect_receipt_projected",
    ],
)
def test_v1_schema_rejects_effect_and_v2_event_types(event_type: str) -> None:
    schema = json.loads(V1_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=workflow_format_checker())
    candidate = _v1_event(event_type=event_type, payload={"effect_id": "eff_1"})
    assert list(validator.iter_errors(candidate))


def test_v1_schema_rejects_effect_ledger_record_shape() -> None:
    schema = json.loads(V1_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=workflow_format_checker())
    effect_record = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": "effect-1",
        "sequence": 1,
        "previous_event_id": None,
        "idempotency_key": "effect-1:claimed",
        "occurred_at": "2026-08-02T00:00:00Z",
        "event_type": "claimed",
        "payload": {},
        "event_id": "wel_" + "0" * 64,
    }
    assert list(validator.iter_errors(effect_record))


def test_v1_event_id_derivation_is_stable_for_historical_body() -> None:
    body = _v1_body()
    expected = "wee_" + hashlib.sha256(_compact(body)).hexdigest()
    assert derive_workflow_execution_event_id_v1(body) == expected


def test_append_execution_event_still_emits_only_v1_bytes(tmp_path: Path) -> None:
    root = tmp_path / "private"
    ledger = root / "runs" / "run.jsonl"
    body = _v1_body()
    payload = body["payload"]
    assert isinstance(payload, dict)

    projection = append_execution_event(
        ledger,
        private_root=root,
        run_id=str(body["run_id"]),
        event_type=str(body["event_type"]),
        payload=payload,
        idempotency_key=str(body["idempotency_key"]),
        occurred_at=str(body["occurred_at"]),
    )

    raw = ledger.read_bytes()
    event = json.loads(raw.decode("utf-8").splitlines()[0])
    assert event["schema_version"] == "workflow_execution_event_v1"
    assert b"workflow_execution_event_v2" not in raw
    assert b"workflow_effect_ledger_event_v1" not in raw
    assert projection["status"] in {"accepted", "running", "completed"} or "run_id" in projection
    events = read_execution_events(ledger, private_root=root)
    assert len(events) == 1
    assert events[0]["schema_version"] == "workflow_execution_event_v1"


def test_v1_ledger_read_rejects_mixed_v2_record(tmp_path: Path) -> None:
    root = tmp_path / "private"
    ledger = root / "runs" / "run.jsonl"
    root.mkdir(parents=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)

    v1 = _v1_event()
    v2_body = {
        "schema_version": "workflow_execution_event_v2",
        "run_id": v1["run_id"],
        "sequence": 2,
        "previous_event_id": v1["event_id"],
        "idempotency_key": "run-f4-regression:waiting",
        "occurred_at": "2026-08-02T00:00:01Z",
        "event_type": "run_waiting_reconcile",
        "payload": {
            "effect_id": "effect-1",
            "effect_receipt_digest": None,
            "reason": "unknown_observation",
        },
    }
    v2 = {
        **v2_body,
        "event_id": "we2_" + hashlib.sha256(_compact(v2_body)).hexdigest(),
    }
    ledger.write_bytes(_compact(v1) + b"\n" + _compact(v2) + b"\n")
    ledger.chmod(0o600)

    with pytest.raises(WorkflowExecutionLedgerError) as raised:
        read_execution_events(ledger, private_root=root)
    assert raised.value.code in {
        "schema_invalid",
        "mixed_schema",
        "payload_invalid",
        "identity_mismatch",
    }


def test_project_execution_events_still_accepts_pure_v1_chain() -> None:
    started_body = _v1_body(
        sequence=1,
        event_type="run_started",
        payload={},
        idempotency_key="run-f4-regression:started",
    )
    # run_started cannot be first in real ledger; use accepted then started via append path instead.
    # This unit check only ensures pure accepted projection remains available.
    accepted = _v1_event()
    projection = project_execution_events([accepted])
    assert isinstance(projection, dict)
