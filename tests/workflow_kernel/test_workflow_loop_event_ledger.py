from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_loop_event_ledger import (
    WorkflowLoopEventLedgerError,
    append_workflow_loop_event,
    derive_workflow_loop_event_id_v1,
    read_workflow_loop_events,
    recover_torn_workflow_loop_event_tail,
    replay_workflow_loop_events_v1,
)


DEFINITION_FIXTURE = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-definition-v1"
    / "cases.json"
)
LOOP_EVENT_FIXTURE = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-loop-event-v1"
    / "cases.json"
)


def _plan() -> dict[str, Any]:
    cases = json.loads(DEFINITION_FIXTURE.read_text(encoding="utf-8"))
    return compile_workflow_definition_v1(copy.deepcopy(cases[0]["value"]))


def _signal(sequence: int, kind: str, task_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "workflow_loop_signal_v1",
        "signal_id": f"signal-{sequence + 1}",
        "kind": kind,
        "expected_sequence": sequence,
        "task_id": task_id,
        "reason": f"Observed {kind}.",
    }


def _event(
    plan: dict[str, Any],
    sequence: int,
    kind: str,
    *,
    task_id: str | None = None,
    previous_event_id: str | None = None,
    occurred_at: str | None = None,
    expires_at: str = "2026-07-21T01:00:00Z",
    session_owner: str = "session-owner",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body = {
        "schema_version": "workflow_loop_event_v1",
        "run_id": "run-1",
        "session_owner": session_owner,
        "sequence": sequence,
        "previous_event_id": previous_event_id,
        "idempotency_key": idempotency_key or f"run-1:{sequence}",
        "occurred_at": occurred_at or f"2026-07-21T00:00:{sequence:02d}Z",
        "expires_at": expires_at,
        "definition_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "signal": _signal(sequence - 1, kind, task_id),
    }
    return {**body, "event_id": derive_workflow_loop_event_id_v1(body)}


def _full_chain(plan: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous: str | None = None
    sequence = 1
    for task_id in ("inspect", "plan", "implement", "verify"):
        for kind in ("task_started_observed", "task_passed_observed"):
            event = _event(
                plan,
                sequence,
                kind,
                task_id=task_id,
                previous_event_id=previous,
            )
            events.append(event)
            previous = event["event_id"]
            sequence += 1
    return events


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "private"
    return root, root / "events" / "workflow-loop-events.jsonl"


def _append_worker(
    ledger: str, root: str, plan: dict[str, Any], event: dict[str, Any]
) -> str:
    result = append_workflow_loop_event(
        Path(ledger), plan, event, private_root=Path(root)
    )
    return str(result["status"])


def _rehash_plan(plan: dict[str, Any]) -> None:
    body = {key: value for key, value in plan.items() if key != "plan_digest"}
    payload = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    plan["plan_digest"] = f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _rehash(event: dict[str, Any]) -> None:
    body = {key: value for key, value in event.items() if key != "event_id"}
    event["event_id"] = derive_workflow_loop_event_id_v1(body)


def test_shared_fixture_identity_matches_typescript() -> None:
    cases = json.loads(LOOP_EVENT_FIXTURE.read_text(encoding="utf-8"))
    event = copy.deepcopy(cases[0]["value"])
    body = {key: value for key, value in event.items() if key != "event_id"}

    assert derive_workflow_loop_event_id_v1(body) == event["event_id"]


def test_replay_full_chain_is_deterministic_and_authority_neutral() -> None:
    plan = _plan()
    events = _full_chain(plan)

    first = replay_workflow_loop_events_v1(plan, events)
    second = replay_workflow_loop_events_v1(plan, copy.deepcopy(events))

    assert first == second
    assert first["workflow_state"] == "passed"
    assert first["terminal"] is True
    assert first["sequence"] == 8
    assert first["authority_granted"] is False
    assert first["executable"] is False


def test_replay_deduplicates_exact_event() -> None:
    plan = _plan()
    events = _full_chain(plan)

    snapshot = replay_workflow_loop_events_v1(
        plan, [events[0], events[0], *events[1:]]
    )

    assert snapshot["sequence"] == 8
    assert snapshot["workflow_state"] == "passed"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda event: event.__setitem__("session_owner", "other-owner"), "session_owner_mismatch"),
        (lambda event: event.__setitem__("run_id", "run-2"), "mixed_run"),
        (lambda event: event.__setitem__("sequence", 3), "sequence_mismatch"),
        (lambda event: event.__setitem__("previous_event_id", None), "broken_link"),
        (lambda event: event.__setitem__("definition_digest", "sha256:" + "b" * 64), "definition_digest_mismatch"),
        (lambda event: event.__setitem__("plan_digest", "sha256:" + "b" * 64), "plan_digest_mismatch"),
    ],
)
def test_replay_rejects_chain_mismatch(mutation: Any, code: str) -> None:
    plan = _plan()
    events = _full_chain(plan)
    mutation(events[1])
    _rehash(events[1])

    with pytest.raises(WorkflowLoopEventLedgerError) as exc_info:
        replay_workflow_loop_events_v1(plan, events)

    assert exc_info.value.code == code


def test_replay_rejects_same_definition_cross_plan_injection() -> None:
    original_plan = _plan()
    events = _full_chain(original_plan)
    changed_plan = copy.deepcopy(original_plan)
    changed_plan["loop_policy"]["max_iterations"] += 1
    _rehash_plan(changed_plan)

    assert changed_plan["definition_digest"] == original_plan["definition_digest"]
    assert changed_plan["plan_digest"] != original_plan["plan_digest"]

    with pytest.raises(WorkflowLoopEventLedgerError) as exc_info:
        replay_workflow_loop_events_v1(changed_plan, events)

    assert exc_info.value.code == "plan_digest_mismatch"


def test_non_stale_signal_at_or_after_expiration_is_rejected() -> None:
    plan = _plan()
    event = _event(
        plan,
        1,
        "task_started_observed",
        task_id="inspect",
        occurred_at="2026-07-21T01:00:00Z",
    )

    with pytest.raises(WorkflowLoopEventLedgerError) as exc_info:
        replay_workflow_loop_events_v1(plan, [event])

    assert exc_info.value.code == "session_expired"


def test_stale_signal_at_expiration_closes_session() -> None:
    plan = _plan()
    event = _event(
        plan,
        1,
        "stale_observed",
        occurred_at="2026-07-21T01:00:00Z",
    )

    snapshot = replay_workflow_loop_events_v1(plan, [event])

    assert snapshot["workflow_state"] == "stale"
    assert snapshot["terminal"] is True


def test_stale_signal_before_expiration_is_rejected() -> None:
    plan = _plan()
    event = _event(plan, 1, "stale_observed")

    with pytest.raises(WorkflowLoopEventLedgerError) as exc_info:
        replay_workflow_loop_events_v1(plan, [event])

    assert exc_info.value.code == "session_not_expired"


def test_append_readback_and_replay_full_chain(tmp_path: Path) -> None:
    plan = _plan()
    root, ledger = _paths(tmp_path)
    events = _full_chain(plan)

    for event in events:
        result = append_workflow_loop_event(
            ledger, plan, event, private_root=root
        )

    assert result["status"] == "appended"
    assert result["count"] == 8
    assert result["snapshot"]["workflow_state"] == "passed"
    assert read_workflow_loop_events(ledger, private_root=root) == events
    assert ledger.read_bytes().count(b"\n") == 8
    assert ledger.stat().st_mode & 0o777 == 0o600


def test_exact_retry_is_idempotent_and_does_not_change_bytes(tmp_path: Path) -> None:
    plan = _plan()
    root, ledger = _paths(tmp_path)
    event = _full_chain(plan)[0]
    append_workflow_loop_event(ledger, plan, event, private_root=root)
    before = ledger.read_bytes()

    result = append_workflow_loop_event(ledger, plan, copy.deepcopy(event), private_root=root)

    assert result["status"] == "duplicate"
    assert result["count"] == 1
    assert ledger.read_bytes() == before


def test_idempotency_conflict_fails_without_changing_bytes(tmp_path: Path) -> None:
    plan = _plan()
    root, ledger = _paths(tmp_path)
    first, second = _full_chain(plan)[:2]
    append_workflow_loop_event(ledger, plan, first, private_root=root)
    before = ledger.read_bytes()
    second["idempotency_key"] = first["idempotency_key"]
    _rehash(second)

    with pytest.raises(WorkflowLoopEventLedgerError) as exc_info:
        append_workflow_loop_event(ledger, plan, second, private_root=root)

    assert exc_info.value.code == "idempotency_conflict"
    assert ledger.read_bytes() == before


def test_torn_tail_fails_closed_without_append(tmp_path: Path) -> None:
    plan = _plan()
    root, ledger = _paths(tmp_path)
    ledger.parent.mkdir(parents=True, mode=0o700)
    ledger.write_bytes(b'{"incomplete":')
    before = ledger.read_bytes()

    with pytest.raises(WorkflowLoopEventLedgerError) as exc_info:
        append_workflow_loop_event(
            ledger, plan, _full_chain(plan)[0], private_root=root
        )

    assert exc_info.value.code == "torn_tail"
    assert ledger.read_bytes() == before


def test_torn_tail_recovery_is_hash_bound_and_preserves_valid_prefix(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root, ledger = _paths(tmp_path)
    first = _full_chain(plan)[0]
    append_workflow_loop_event(ledger, plan, first, private_root=root)
    with ledger.open("ab") as handle:
        handle.write(b'{"partial":')
    raw = ledger.read_bytes()
    expected = hashlib.sha256(raw).hexdigest()

    with pytest.raises(WorkflowLoopEventLedgerError) as mismatch:
        recover_torn_workflow_loop_event_tail(
            ledger,
            plan,
            expected_sha256="0" * 64,
            private_root=root,
        )
    assert mismatch.value.code == "recovery_cas_mismatch"
    assert ledger.read_bytes() == raw

    result = recover_torn_workflow_loop_event_tail(
        ledger,
        plan,
        expected_sha256=expected,
        private_root=root,
    )

    assert result == {
        "status": "recovered",
        "removed_bytes": len(b'{"partial":'),
        "count": 1,
    }
    assert read_workflow_loop_events(ledger, private_root=root) == [first]


def test_concurrent_exact_retries_append_once(tmp_path: Path) -> None:
    plan = _plan()
    root, ledger = _paths(tmp_path)
    event = _full_chain(plan)[0]

    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                _append_worker,
                [str(ledger)] * 8,
                [str(root)] * 8,
                [plan] * 8,
                [event] * 8,
            )
        )

    assert results.count("appended") == 1
    assert results.count("duplicate") == 7
    assert read_workflow_loop_events(ledger, private_root=root) == [event]
