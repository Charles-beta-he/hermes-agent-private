from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import workflow_kernel.workflow_run_event_ledger as ledger_module
from workflow_kernel.workflow_run_event_ledger import (
    WorkflowRunEventLedgerError,
    append_workflow_run_event,
    derive_workflow_run_event_id_v1,
    read_workflow_run_events,
    recover_torn_workflow_run_event_tail,
)


FIXTURES = Path(__file__).resolve().parents[1] / "contracts/fixtures/workflow-run-event-v1/cases.json"


def _first_event() -> dict[str, object]:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return copy.deepcopy(next(case["value"] for case in cases if case["name"] == "valid first workflow event"))


def _second_event(first: dict[str, object]) -> dict[str, object]:
    body = {
        "schema_version": "workflow_run_event_v1",
        "run_id": first["run_id"],
        "sequence": 2,
        "previous_event_id": first["event_id"],
        "idempotency_key": "run-1:2",
        "occurred_at": "2026-07-20T00:00:01.0001Z",
        "provenance": {
            "producer": "workflow_runtime_claim_executor",
            "artifact_path": "docs/private/runtime/start.json",
            "artifact_digest": "sha256:" + "b" * 64,
        },
        "transition": {"from": "claimed", "to": "running"},
    }
    return {**body, "event_id": derive_workflow_run_event_id_v1(body)}


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "private"
    return root, root / "events" / "workflow-run-events.jsonl"


def _rehash(event: dict[str, object]) -> None:
    body = {key: value for key, value in event.items() if key != "event_id"}
    event["event_id"] = derive_workflow_run_event_id_v1(body)


def test_shared_schema_fixture_cases_are_total_at_ledger_boundary(tmp_path: Path) -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for index, case in enumerate(cases):
        root = tmp_path / f"private-{index}"
        ledger = root / "events.jsonl"
        if case["valid"]:
            value = copy.deepcopy(case["value"])
            _rehash(value)
            append_workflow_run_event(ledger, value, private_root=root)
            assert read_workflow_run_events(ledger, private_root=root) == [value]
        else:
            with pytest.raises(WorkflowRunEventLedgerError):
                append_workflow_run_event(ledger, case["value"], private_root=root)
            assert not ledger.exists()


@pytest.mark.parametrize("candidate", [None, True, False, 0, 1.5, "event", [], [None], {}])
def test_ordinary_json_candidates_never_escape_untyped_exceptions(tmp_path: Path, candidate) -> None:
    root, ledger = _paths(tmp_path)

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, candidate, private_root=root)

    assert raised.value.code == "schema_invalid"
    assert not ledger.exists()


def test_append_readback_is_compact_utf8_jsonl_with_private_permissions(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    event = _first_event()

    result = append_workflow_run_event(ledger, event, private_root=root)

    assert result == {"status": "appended", "event_id": event["event_id"], "count": 1}
    assert read_workflow_run_events(ledger, private_root=root) == [event]
    raw = ledger.read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert b"\n " not in raw and b": " not in raw
    assert os.stat(ledger).st_mode & 0o777 == 0o600
    assert os.stat(ledger.parent).st_mode & 0o777 == 0o700


def test_numeric_identity_matches_javascript_for_exponent_and_negative_zero() -> None:
    one = _first_event()
    one_body = {key: value for key, value in one.items() if key != "event_id"}
    exponent_body = copy.deepcopy(one_body)
    exponent_body["sequence"] = 1e0
    assert derive_workflow_run_event_id_v1(exponent_body) == derive_workflow_run_event_id_v1(one_body)

    zero_body = copy.deepcopy(one_body)
    zero_body["sequence"] = 0
    negative_zero_body = copy.deepcopy(one_body)
    negative_zero_body["sequence"] = -0.0
    assert derive_workflow_run_event_id_v1(negative_zero_body) == derive_workflow_run_event_id_v1(zero_body)


def test_precise_time_accepts_minute_precision_and_offset_equivalent_instants(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    first["occurred_at"] = "2026-07-20T00:00Z"
    _rehash(first)
    append_workflow_run_event(ledger, first, private_root=root)

    second = _second_event(first)
    second["occurred_at"] = "2026-07-20T01:00:00+01:00"
    _rehash(second)
    result = append_workflow_run_event(ledger, second, private_root=root)

    assert result["status"] == "appended"
    assert read_workflow_run_events(ledger, private_root=root) == [first, second]


def test_precise_time_accepts_schema_valid_year_zero(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    first["occurred_at"] = "0000-01-01T00:00Z"
    _rehash(first)
    append_workflow_run_event(ledger, first, private_root=root)

    second = _second_event(first)
    second["occurred_at"] = "0000-01-01T00:00:00.1Z"
    _rehash(second)

    assert append_workflow_run_event(ledger, second, private_root=root)["status"] == "appended"
    assert read_workflow_run_events(ledger, private_root=root) == [first, second]


def test_exact_retry_is_idempotent_and_sequence_float_canonicalizes_like_javascript(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    event = _first_event()
    event["sequence"] = 1.0

    first = append_workflow_run_event(ledger, event, private_root=root)
    before = ledger.read_bytes()
    retry = append_workflow_run_event(ledger, _first_event(), private_root=root)

    assert first["status"] == "appended"
    assert retry == {"status": "duplicate", "event_id": event["event_id"], "count": 1}
    assert ledger.read_bytes() == before
    assert json.loads(before)["sequence"] == 1


def test_linked_event_appends_and_replays(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    second = _second_event(first)

    append_workflow_run_event(ledger, first, private_root=root)
    result = append_workflow_run_event(ledger, second, private_root=root)

    assert result == {"status": "appended", "event_id": second["event_id"], "count": 2}
    assert read_workflow_run_events(ledger, private_root=root) == [first, second]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda event: event.__setitem__("event_id", "wre_" + "0" * 64), "identity_mismatch"),
        (lambda event: event.__setitem__("sequence", 2), "identity_mismatch"),
        (lambda event: event.__setitem__("extra", True), "schema_invalid"),
    ],
)
def test_invalid_event_fails_before_creating_ledger(tmp_path: Path, mutate, code: str) -> None:
    root, ledger = _paths(tmp_path)
    event = _first_event()
    mutate(event)

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, event, private_root=root)

    assert raised.value.code == code
    assert not ledger.exists()


def test_idempotency_conflict_fails_without_changing_bytes(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    before = ledger.read_bytes()
    conflicting = _second_event(first)
    conflicting["idempotency_key"] = first["idempotency_key"]
    body = {key: value for key, value in conflicting.items() if key != "event_id"}
    conflicting["event_id"] = derive_workflow_run_event_id_v1(body)

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, conflicting, private_root=root)

    assert raised.value.code == "idempotency_conflict"
    assert ledger.read_bytes() == before


def test_first_event_must_start_from_proposed(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    event = _first_event()
    event["transition"] = {"from": "claimed", "to": "running"}
    _rehash(event)

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, event, private_root=root)

    assert raised.value.code == "discontinuous_transition"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda event: event.__setitem__("run_id", "run-2"), "mixed_run"),
        (lambda event: event.__setitem__("sequence", 3), "sequence_mismatch"),
        (lambda event: event.__setitem__("previous_event_id", "wre_" + "0" * 64), "broken_link"),
        (lambda event: event.__setitem__("occurred_at", "2026-07-20T00:00:00.9999Z"), "time_reversal"),
        (lambda event: event.__setitem__("occurred_at", "not-a-time"), "schema_invalid"),
        (
            lambda event: event.__setitem__("transition", {"from": "running", "to": "review"}),
            "discontinuous_transition",
        ),
    ],
)
def test_linked_chain_invariants_fail_before_append(tmp_path: Path, mutation, code: str) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    before = ledger.read_bytes()
    event = _second_event(first)
    mutation(event)
    _rehash(event)

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, event, private_root=root)

    assert raised.value.code == code
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("raw", [b'{"broken":true}\n', b'{"broken":'])
def test_existing_corruption_fails_closed_without_repair_or_append(tmp_path: Path, raw: bytes) -> None:
    root, ledger = _paths(tmp_path)
    ledger.parent.mkdir(parents=True, mode=0o700)
    ledger.write_bytes(raw)
    event = _first_event()

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, event, private_root=root)

    assert raised.value.code in {"schema_invalid", "torn_tail"}
    assert ledger.read_bytes() == raw


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b'\xef\xbb\xbf{}\n', "invalid_json"),
        (b'\xff\n', "invalid_utf8"),
        (b'{"sequence":1,"sequence":1}\n', "duplicate_key"),
        (b'{"sequence":NaN}\n', "invalid_json"),
    ],
)
def test_noncanonical_json_inputs_fail_closed(tmp_path: Path, raw: bytes, code: str) -> None:
    root, ledger = _paths(tmp_path)
    ledger.parent.mkdir(parents=True, mode=0o700)
    ledger.write_bytes(raw)

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        read_workflow_run_events(ledger, private_root=root)

    assert raised.value.code == code
    assert ledger.read_bytes() == raw


def test_symlink_and_hardlink_ledger_targets_are_rejected(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    ledger.parent.mkdir(parents=True, mode=0o700)
    target = tmp_path / "target.jsonl"
    target.write_bytes(b"")
    ledger.symlink_to(target)
    with pytest.raises(OSError):
        append_workflow_run_event(ledger, _first_event(), private_root=root)
    ledger.unlink()
    os.link(target, ledger)
    with pytest.raises(OSError):
        append_workflow_run_event(ledger, _first_event(), private_root=root)


def test_parent_swap_during_append_stays_on_anchored_directory(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    parent = ledger.parent
    parked = root / "events-parked"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open = ledger_module.open_ledger_file
    swapped = False

    def swap_then_open(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            parent.rename(parked)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(*args, **kwargs)

    monkeypatch.setattr(ledger_module, "open_ledger_file", swap_then_open)
    event = _first_event()
    result = append_workflow_run_event(ledger, event, private_root=root)

    assert result["status"] == "appended"
    assert json.loads((parked / ledger.name).read_bytes()) == event
    assert not (outside / ledger.name).exists()
    assert not (outside / f".{ledger.name}.lock").exists()


def test_final_entry_swap_is_rejected_without_touching_outside_target(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    before = ledger.read_bytes()
    parked = ledger.with_name("parked.jsonl")
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"outside\n")
    real_open = ledger_module.open_ledger_file
    swapped = False

    def swap_then_open(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            ledger.rename(parked)
            ledger.symlink_to(outside)
            swapped = True
        return real_open(*args, **kwargs)

    monkeypatch.setattr(ledger_module, "open_ledger_file", swap_then_open)
    with pytest.raises(OSError):
        append_workflow_run_event(ledger, _second_event(first), private_root=root)

    assert parked.read_bytes() == before
    assert outside.read_bytes() == b"outside\n"


def test_torn_tail_recovery_is_explicit_hash_bound_and_preserves_valid_prefix(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    valid = ledger.read_bytes()
    ledger.write_bytes(valid + b'{"partial":')
    corrupted = ledger.read_bytes()
    digest = hashlib.sha256(corrupted).hexdigest()

    with pytest.raises(WorkflowRunEventLedgerError) as stale:
        recover_torn_workflow_run_event_tail(ledger, expected_sha256="0" * 64, private_root=root)
    assert stale.value.code == "recovery_cas_mismatch"
    assert ledger.read_bytes() == corrupted

    result = recover_torn_workflow_run_event_tail(ledger, expected_sha256=digest, private_root=root)

    assert result == {"status": "recovered", "removed_bytes": len(b'{"partial":'), "count": 1}
    assert ledger.read_bytes() == valid
    assert read_workflow_run_events(ledger, private_root=root) == [first]


def test_recovery_refuses_complete_unterminated_or_middle_corruption(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    complete_without_newline = ledger.read_bytes().removesuffix(b"\n")
    ledger.write_bytes(complete_without_newline)
    digest = hashlib.sha256(complete_without_newline).hexdigest()
    with pytest.raises(WorkflowRunEventLedgerError) as complete:
        recover_torn_workflow_run_event_tail(ledger, expected_sha256=digest, private_root=root)
    assert complete.value.code == "tail_not_confirmed_torn"

    middle = b'{"broken":true}\n{"partial":'
    ledger.write_bytes(middle)
    digest = hashlib.sha256(middle).hexdigest()
    with pytest.raises(WorkflowRunEventLedgerError) as corrupt:
        recover_torn_workflow_run_event_tail(ledger, expected_sha256=digest, private_root=root)
    assert corrupt.value.code == "schema_invalid"
    assert ledger.read_bytes() == middle


def test_recovery_refuses_invalid_utf8_tail_that_is_not_a_truncated_codepoint(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    raw = ledger.read_bytes() + b"\xff"
    ledger.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        recover_torn_workflow_run_event_tail(ledger, expected_sha256=digest, private_root=root)

    assert raised.value.code == "invalid_utf8"
    assert ledger.read_bytes() == raw


def test_recovery_accepts_only_a_truncated_final_utf8_codepoint(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    raw = ledger.read_bytes() + b"\xe2\x82"
    ledger.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    result = recover_torn_workflow_run_event_tail(ledger, expected_sha256=digest, private_root=root)

    assert result["status"] == "recovered"
    assert read_workflow_run_events(ledger, private_root=root) == [first]


def test_short_write_fails_closed_and_leaves_only_a_recoverable_tail(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    second = _second_event(first)
    append_workflow_run_event(ledger, first, private_root=root)
    real_write = ledger_module._write_record_once

    def short_write(descriptor: int, record: bytes) -> int:
        return real_write(descriptor, record[:7])

    monkeypatch.setattr(ledger_module, "_write_record_once", short_write)
    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, second, private_root=root)
    assert raised.value.code == "short_write"
    with pytest.raises(WorkflowRunEventLedgerError) as torn:
        read_workflow_run_events(ledger, private_root=root)
    assert torn.value.code == "torn_tail"

    monkeypatch.undo()
    digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
    recovery = recover_torn_workflow_run_event_tail(
        ledger,
        expected_sha256=digest,
        private_root=root,
    )
    assert recovery["status"] == "recovered"
    assert read_workflow_run_events(ledger, private_root=root) == [first]


def test_fsync_failure_reports_unknown_durability_and_readback_resolves_state(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    second = _second_event(first)
    append_workflow_run_event(ledger, first, private_root=root)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(ledger_module, "_fsync_descriptor", fail_fsync)
    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, second, private_root=root)
    assert raised.value.code == "durability_unknown"
    monkeypatch.undo()
    assert read_workflow_run_events(ledger, private_root=root) == [first, second]


def test_duplicate_retry_resolves_unknown_file_and_directory_durability(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    initial_calls = 0

    def fail_initial_directory_fsync(_descriptor: int) -> None:
        nonlocal initial_calls
        initial_calls += 1
        if initial_calls == 2:
            raise OSError("injected parent directory fsync failure")

    monkeypatch.setattr(ledger_module, "_fsync_descriptor", fail_initial_directory_fsync)
    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, first, private_root=root)
    assert raised.value.code == "durability_unknown"
    assert initial_calls == 2

    retry_calls = 0

    def record_retry_fsync(_descriptor: int) -> None:
        nonlocal retry_calls
        retry_calls += 1

    monkeypatch.setattr(ledger_module, "_fsync_descriptor", record_retry_fsync)
    assert append_workflow_run_event(ledger, first, private_root=root) == {
        "status": "duplicate",
        "event_id": first["event_id"],
        "count": 1,
    }
    assert retry_calls == 2


def test_duplicate_retry_fsync_failure_remains_durability_unknown(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected duplicate retry fsync failure")

    monkeypatch.setattr(ledger_module, "_fsync_descriptor", fail_fsync)
    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        append_workflow_run_event(ledger, first, private_root=root)
    assert raised.value.code == "durability_unknown"


def test_recovery_fsync_failure_reports_unknown_and_readback_resolves_state(tmp_path: Path, monkeypatch) -> None:
    root, ledger = _paths(tmp_path)
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    ledger.write_bytes(ledger.read_bytes() + b'{"partial":')
    digest = hashlib.sha256(ledger.read_bytes()).hexdigest()

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected recovery fsync failure")

    monkeypatch.setattr(ledger_module, "_fsync_descriptor", fail_fsync)
    with pytest.raises(WorkflowRunEventLedgerError) as raised:
        recover_torn_workflow_run_event_tail(
            ledger,
            expected_sha256=digest,
            private_root=root,
        )
    assert raised.value.code == "durability_unknown"
    monkeypatch.undo()
    assert read_workflow_run_events(ledger, private_root=root) == [first]
