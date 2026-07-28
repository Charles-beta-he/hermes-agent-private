from __future__ import annotations

import copy
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from workflow_kernel.workflow_execution_event_ledger import (
    WorkflowExecutionEventLedgerError,
    WorkflowExecutionLedgerSink,
    append_workflow_execution_event,
    create_workflow_execution_event_v1,
    derive_execution_approval_digest_v1,
    derive_execution_ids_v1,
    read_workflow_execution_events,
    replay_workflow_execution_events_v1,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _identity(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "session_owner": "session-owner",
        "lease_id": "lease-1",
        "lease_expires_at": "2026-07-29T00:00:00Z",
        "fencing_token": 7,
        "workflow_id": "workflow-1",
        "definition_digest": DIGEST_A,
        "plan_digest": DIGEST_B,
        "binding_digest": DIGEST_C,
        "execution_mode": "apply",
        "policy_digest": DIGEST_A,
        "approval_digest": derive_execution_approval_digest_v1(
            run_id="run-1",
            workflow_id="workflow-1",
            plan_digest=DIGEST_B,
            binding_digest=DIGEST_C,
            policy_digest=DIGEST_A,
        ),
    }
    value.update(overrides)
    return value


def _event(
    sequence: int,
    kind: str,
    *,
    previous_event_id: str | None = None,
    identity: dict[str, Any] | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
    attempt_id: str | None = None,
    effect_id: str | None = None,
    side_effect: str = "none",
    evidence_digest: str | None = None,
    reason_code: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return create_workflow_execution_event_v1(
        {
            "schema_version": "workflow_execution_event_v1",
            "run_id": "run-1",
            "sequence": sequence,
            "previous_event_id": previous_event_id,
            "idempotency_key": idempotency_key or f"run-1:{sequence}:{kind}",
            "occurred_at": f"2026-07-28T00:00:{sequence:02d}Z",
            "identity": identity or _identity(),
            "kind": kind,
            "task_id": task_id,
            "step_id": step_id,
            "attempt_id": attempt_id,
            "effect_id": effect_id,
            "side_effect": side_effect,
            "evidence_digest": evidence_digest,
            "reason_code": reason_code,
        }
    )


def _chain() -> list[dict[str, Any]]:
    ids = derive_execution_ids_v1(
        run_id="run-1",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="apply",
        attempt=1,
        side_effect="local_write",
    )
    events: list[dict[str, Any]] = []
    previous: str | None = None
    specifications = [
        ("run_admitted", {}),
        ("task_started", {**ids, "task_id": "apply", "side_effect": "local_write"}),
        ("effect_intent", {**ids, "task_id": "apply", "side_effect": "local_write"}),
        (
            "effect_observed",
            {
                **ids,
                "task_id": "apply",
                "side_effect": "local_write",
                "evidence_digest": DIGEST_A,
            },
        ),
        (
            "task_succeeded",
            {
                **ids,
                "task_id": "apply",
                "side_effect": "local_write",
                "evidence_digest": DIGEST_A,
            },
        ),
        ("run_succeeded", {"evidence_digest": DIGEST_A}),
    ]
    for sequence, (kind, extras) in enumerate(specifications, 1):
        event = _event(sequence, kind, previous_event_id=previous, **extras)
        events.append(event)
        previous = event["event_id"]
    return events


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "private"
    return root, root / "events" / "workflow-execution-events.jsonl"


def test_execution_ids_are_deterministic_scoped_and_effect_optional() -> None:
    first = derive_execution_ids_v1(
        run_id="run-1",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="apply",
        attempt=1,
        side_effect="local_write",
    )
    assert first == derive_execution_ids_v1(
        run_id="run-1",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="apply",
        attempt=1,
        side_effect="local_write",
    )
    assert first["step_id"].startswith("wst_")
    assert first["attempt_id"].startswith("wat_")
    assert first["effect_id"].startswith("wef_")
    assert first != derive_execution_ids_v1(
        run_id="run-2",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="apply",
        attempt=1,
        side_effect="local_write",
    )
    read_only = derive_execution_ids_v1(
        run_id="run-1",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="inspect",
        attempt=1,
        side_effect="local_read",
    )
    assert read_only["effect_id"] is None


def test_event_creator_rejects_impossible_calendar_timestamp() -> None:
    valid = _chain()[0]
    body = {key: value for key, value in valid.items() if key != "event_id"}
    body["occurred_at"] = "2026-02-31T00:00:00Z"

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        create_workflow_execution_event_v1(body)

    assert raised.value.code == "invalid_event"


def test_append_readback_replay_success_and_private_permissions(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    events = _chain()

    for event in events:
        result = append_workflow_execution_event(
            ledger, event, private_root=root
        )

    assert result["status"] == "appended"
    assert result["snapshot"]["status"] == "succeeded"
    assert result["snapshot"]["terminal"] is True
    assert result["snapshot"]["uncertain_effect_ids"] == []
    assert read_workflow_execution_events(ledger, private_root=root) == events
    assert replay_workflow_execution_events_v1(events) == result["snapshot"]
    assert ledger.stat().st_mode & 0o777 == 0o600


def test_effect_intent_without_observation_replays_as_uncertain() -> None:
    events = _chain()[:3]

    snapshot = replay_workflow_execution_events_v1(events)

    assert snapshot["status"] == "running"
    assert snapshot["uncertain_effect_ids"] == [events[-1]["effect_id"]]


def test_exact_retry_is_idempotent_but_conflict_does_not_change_bytes(
    tmp_path: Path,
) -> None:
    root, ledger = _paths(tmp_path)
    event = _chain()[0]
    append_workflow_execution_event(ledger, event, private_root=root)
    before = ledger.read_bytes()

    duplicate = append_workflow_execution_event(
        ledger, copy.deepcopy(event), private_root=root
    )
    assert duplicate["status"] == "duplicate"
    assert ledger.read_bytes() == before

    conflict = _event(
        2,
        "run_failed",
        previous_event_id=event["event_id"],
        reason_code="failed",
        idempotency_key=event["idempotency_key"],
    )
    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        append_workflow_execution_event(ledger, conflict, private_root=root)
    assert raised.value.code == "idempotency_conflict"
    assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda event: event["identity"].__setitem__("session_owner", "other"), "session_owner_mismatch"),
        (lambda event: event["identity"].__setitem__("lease_id", "lease-2"), "lease_id_mismatch"),
        (lambda event: event["identity"].__setitem__("fencing_token", 8), "fencing_token_mismatch"),
        (lambda event: event["identity"].__setitem__("plan_digest", DIGEST_A), "plan_digest_mismatch"),
        (lambda event: event.__setitem__("run_id", "run-2"), "mixed_run"),
        (lambda event: event.__setitem__("sequence", 9), "sequence_mismatch"),
        (lambda event: event.__setitem__("previous_event_id", None), "broken_link"),
    ],
)
def test_replay_rejects_identity_or_chain_drift(mutation: Any, code: str) -> None:
    events = _chain()[:2]
    mutation(events[1])
    identity = events[1]["identity"]
    identity["approval_digest"] = derive_execution_approval_digest_v1(
        run_id=events[1]["run_id"],
        workflow_id=identity["workflow_id"],
        plan_digest=identity["plan_digest"],
        binding_digest=identity["binding_digest"],
        policy_digest=identity["policy_digest"],
    )
    events[1] = create_workflow_execution_event_v1(
        {key: value for key, value in events[1].items() if key != "event_id"}
    )

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        replay_workflow_execution_events_v1(events)

    assert raised.value.code == code


def test_replay_rejects_event_after_terminal() -> None:
    events = _chain()
    events.append(
        _event(
            7,
            "run_failed",
            previous_event_id=events[-1]["event_id"],
            reason_code="late",
        )
    )

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        replay_workflow_execution_events_v1(events)

    assert raised.value.code == "run_terminal"


def test_kind_shape_is_fail_closed() -> None:
    ids = derive_execution_ids_v1(
        run_id="run-1",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="apply",
        attempt=1,
        side_effect="local_write",
    )
    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        _event(
            1,
            "effect_intent",
            task_id="apply",
            step_id=ids["step_id"],
            attempt_id=ids["attempt_id"],
            effect_id=None,
            side_effect="local_write",
        )
    assert raised.value.code == "event_shape_invalid"


def test_first_semantic_rejection_does_not_create_ledger(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    invalid_first = _event(1, "run_failed", reason_code="not-admitted")

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        append_workflow_execution_event(ledger, invalid_first, private_root=root)

    assert raised.value.code == "run_not_admitted"
    assert not ledger.exists()


def test_first_success_recovers_empty_inode_with_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ledger = _paths(tmp_path)
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"")
    real_fsync = os.fsync
    fsync_kinds: list[str] = []

    def tracking_fsync(descriptor: int) -> None:
        fsync_kinds.append(
            "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        )
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    result = append_workflow_execution_event(ledger, _chain()[0], private_root=root)

    assert result["status"] == "appended"
    assert "file" in fsync_kinds
    assert "directory" in fsync_kinds


def test_failed_effect_observation_fsync_rolls_back_to_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ledger = _paths(tmp_path)
    events = _chain()
    for event in events[:3]:
        append_workflow_execution_event(ledger, event, private_root=root)

    real_fsync = os.fsync
    failed = False

    def fail_once(descriptor: int) -> None:
        nonlocal failed
        if not failed and not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed = True
            raise OSError("injected effect observation fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_once)
    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        append_workflow_execution_event(ledger, events[3], private_root=root)

    assert raised.value.code == "durability_unknown"
    persisted = read_workflow_execution_events(ledger, private_root=root)
    replay = replay_workflow_execution_events_v1(persisted)
    assert [event["kind"] for event in persisted] == [
        "run_admitted",
        "task_started",
        "effect_intent",
    ]
    assert replay["effects"][events[3]["effect_id"]]["status"] == "uncertain"
    assert replay["uncertain_effect_ids"] == [events[3]["effect_id"]]


def test_partial_writes_are_completed_before_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ledger = _paths(tmp_path)
    event = _chain()[0]
    real_write = os.write
    calls = 0

    def partial_once(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, data[: max(1, len(data) // 2)])
        return real_write(descriptor, data)

    monkeypatch.setattr(os, "write", partial_once)
    result = append_workflow_execution_event(ledger, event, private_root=root)

    assert calls == 2
    assert result["status"] == "appended"
    assert read_workflow_execution_events(ledger, private_root=root) == [event]


def test_incomplete_effect_observation_write_restores_prior_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ledger = _paths(tmp_path)
    events = _chain()
    for event in events[:3]:
        append_workflow_execution_event(ledger, event, private_root=root)
    prior_bytes = ledger.read_bytes()
    real_write = os.write
    calls = 0

    def partial_then_zero(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, data[: max(1, len(data) // 2)])
        return 0

    monkeypatch.setattr(os, "write", partial_then_zero)
    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        append_workflow_execution_event(ledger, events[3], private_root=root)

    assert raised.value.code == "short_write"
    assert ledger.read_bytes() == prior_bytes
    replay = replay_workflow_execution_events_v1(
        read_workflow_execution_events(ledger, private_root=root)
    )
    assert replay["effects"][events[3]["effect_id"]]["status"] == "uncertain"
    assert replay["uncertain_effect_ids"] == [events[3]["effect_id"]]


def test_torn_tail_and_unsafe_target_fail_closed(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b'{"partial":')
    before = ledger.read_bytes()

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        append_workflow_execution_event(ledger, _chain()[0], private_root=root)
    assert raised.value.code == "torn_tail"
    assert ledger.read_bytes() == before

    ledger.unlink()
    target = ledger.parent / "target"
    target.write_bytes(b"")
    ledger.symlink_to(target)
    with pytest.raises(OSError):
        append_workflow_execution_event(ledger, _chain()[0], private_root=root)


def _observation(
    *,
    identity: dict[str, Any] | None = None,
    kind: str = "run_admitted",
    task_id: str | None = None,
    ids: dict[str, str | None] | None = None,
    side_effect: str = "none",
    evidence_digest: str | None = None,
    reason_code: str | None = None,
) -> dict[str, Any]:
    selected_ids = ids or {"step_id": None, "attempt_id": None, "effect_id": None}
    return {
        "run_id": "run-1",
        "occurred_at": "2026-07-28T00:00:01Z",
        "identity": identity or _identity(),
        "kind": kind,
        "task_id": task_id,
        **selected_ids,
        "side_effect": side_effect,
        "evidence_digest": evidence_digest,
        "reason_code": reason_code,
    }


def test_concurrent_sinks_deduplicate_the_same_observation(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = WorkflowExecutionLedgerSink(ledger, private_root=root)
    second = WorkflowExecutionLedgerSink(ledger, private_root=root)
    observation = _observation()

    first(observation)
    second(observation)

    events = read_workflow_execution_events(ledger, private_root=root)
    assert len(events) == 1
    assert events[0]["kind"] == "run_admitted"


def test_stale_fencing_writer_cannot_append_after_newer_identity(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    newer = WorkflowExecutionLedgerSink(ledger, private_root=root)
    stale = WorkflowExecutionLedgerSink(ledger, private_root=root)

    newer(_observation(identity=_identity(fencing_token=8)))
    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        stale(_observation(identity=_identity(fencing_token=7)))

    assert raised.value.code == "idempotency_conflict"
    events = read_workflow_execution_events(ledger, private_root=root)
    assert len(events) == 1
    assert events[0]["identity"]["fencing_token"] == 8


def test_sink_restart_requires_explicit_resume_and_preserves_chain(tmp_path: Path) -> None:
    root, ledger = _paths(tmp_path)
    first = WorkflowExecutionLedgerSink(ledger, private_root=root)
    first(_observation())

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        WorkflowExecutionLedgerSink(ledger, private_root=root)
    assert raised.value.code == "execution_ledger_not_empty"

    resumed = WorkflowExecutionLedgerSink(ledger, private_root=root, require_empty=False)
    ids = derive_execution_ids_v1(
        run_id="run-1",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="apply",
        attempt=1,
        side_effect="local_read",
    )
    resumed(
        _observation(
            kind="task_started",
            task_id="apply",
            ids=ids,
            side_effect="local_read",
        )
    )

    events = read_workflow_execution_events(ledger, private_root=root)
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[1]["previous_event_id"] == events[0]["event_id"]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {
                "execution_mode": "dry_run",
                "policy_digest": DIGEST_A,
                "approval_digest": None,
            },
            "execution_mode_mismatch",
        ),
        (
            {
                "policy_digest": DIGEST_C,
                "approval_digest": derive_execution_approval_digest_v1(
                    run_id="run-1",
                    workflow_id="workflow-1",
                    plan_digest=DIGEST_B,
                    binding_digest=DIGEST_C,
                    policy_digest=DIGEST_C,
                ),
            },
            "policy_digest_mismatch",
        ),
        ({"fencing_token": 8}, "fencing_token_mismatch"),
    ],
)
def test_execution_identity_cannot_drift_within_run(
    overrides: dict[str, Any], code: str
) -> None:
    events = _chain()
    drifted = copy.deepcopy(events[1])
    drifted["identity"].update(overrides)
    drifted.pop("event_id")
    events[1] = create_workflow_execution_event_v1(drifted)

    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        replay_workflow_execution_events_v1(events[:2])
    assert raised.value.code == code


@pytest.mark.parametrize(
    ("identity", "code"),
    [
        (_identity(execution_mode="dry_run"), "execution_mode_identity_invalid"),
        (_identity(policy_digest=DIGEST_A, approval_digest=None), "apply_identity_incomplete"),
        (
            _identity(policy_digest=DIGEST_A, approval_digest=DIGEST_B),
            "apply_approval_digest_mismatch",
        ),
    ],
)
def test_execution_identity_mode_policy_semantics_fail_closed(
    identity: dict[str, Any], code: str
) -> None:
    with pytest.raises(WorkflowExecutionEventLedgerError) as raised:
        _event(1, "run_admitted", identity=identity)
    assert raised.value.code == code
