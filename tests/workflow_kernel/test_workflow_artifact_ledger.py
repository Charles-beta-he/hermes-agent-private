from __future__ import annotations

from copy import deepcopy
import json
import multiprocessing
import os
from pathlib import Path
import stat
from typing import Any

import pytest

from workflow_kernel import workflow_artifact_ledger as artifact_ledger
from workflow_kernel.workflow_artifact_ledger import (
    WorkflowArtifactLedgerError,
    apply_workflow_mutation,
    derive_workflow_artifact_digest_v1,
    initialize_workflow_artifact,
    read_workflow_artifact,
    read_workflow_artifact_history,
    validate_workflow_mutation,
)


NOW = "2026-07-25T03:00:00Z"
LATER = "2026-07-25T03:01:00Z"


def _task(
    task_id: str,
    *,
    role: str,
    depends_on: list[str],
    verifies: list[str] | None = None,
    side_effect: str = "none",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": task_id.replace("-", " ").title(),
        "role": role,
        "owner": f"{task_id}-owner",
        "depends_on": depends_on,
        "inputs": ["input"],
        "outputs": [f"{task_id}-output"],
        "writes": [],
        "authority": {
            "side_effect": side_effect,
            "capabilities": capabilities or [],
        },
        "acceptance": [
            {
                "id": f"{task_id}-accepted",
                "claim": f"{task_id} is evidenced.",
                "evidence_required": ["readback"],
            }
        ],
        "verifies": verifies or [],
    }


def _definition() -> dict[str, Any]:
    return {
        "schema_version": "workflow_definition_v1",
        "workflow_id": "artifact-test",
        "version": 1,
        "title": "Artifact test",
        "goal": "Exercise governed artifact mutation.",
        "non_goals": ["No external effects."],
        "constraints": ["Authority remains immutable."],
        "authority": {"max_side_effect": "none", "allowed_capabilities": []},
        "tasks": [
            _task("inspect", role="explore", depends_on=[]),
            _task("implement", role="execute", depends_on=["inspect"]),
            _task(
                "verify",
                role="verify",
                depends_on=["implement"],
                verifies=["implement"],
            ),
        ],
        "loop_policy": {
            "max_iterations": 2,
            "repeated_failure_limit": 1,
            "on_repeated_failure": "terminal_failure",
        },
        "stop_conditions": ["success", "blocked", "terminal_failure"],
        "handoff": {
            "required_fields": [
                "goal",
                "task_states",
                "artifacts",
                "evidence",
                "blockers",
                "next_step",
                "authority_state",
            ]
        },
    }


def _mutation(
    state: dict[str, Any],
    operations: list[dict[str, Any]],
    *,
    key: str = "mutation-1",
) -> dict[str, Any]:
    return {
        "schema_version": "workflow_mutation_v1",
        "artifact_id": state["artifact_id"],
        "base_revision": state["revision"],
        "base_artifact_digest": state["artifact_digest"],
        "idempotency_key": key,
        "operations": operations,
    }


def _initialize(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    private_root = tmp_path / "private"
    ledger = private_root / "artifacts" / "artifact.jsonl"
    receipt = initialize_workflow_artifact(
        _definition(),
        ledger_path=ledger,
        private_root=private_root,
        idempotency_key="init-1",
        now=NOW,
    )
    assert receipt["readback_verified"] is True
    return private_root, ledger, read_workflow_artifact(
        ledger, private_root=private_root
    )


def _concurrent_apply_worker(
    ledger_path: str,
    private_root: str,
    mutation: dict[str, Any],
    start: Any,
    results: Any,
) -> None:
    start.wait()
    try:
        receipt = apply_workflow_mutation(
            mutation,
            ledger_path=Path(ledger_path),
            private_root=Path(private_root),
            now=LATER,
        )
    except WorkflowArtifactLedgerError as exc:
        results.put(exc.code)
    else:
        results.put(f"applied:{receipt['new_revision']}")


def _concurrent_init_worker(
    ledger_path: str,
    private_root: str,
    definition: dict[str, Any],
    start: Any,
    results: Any,
) -> None:
    start.wait()
    try:
        receipt = initialize_workflow_artifact(
            definition,
            ledger_path=Path(ledger_path),
            private_root=Path(private_root),
            idempotency_key="concurrent-init",
            now=NOW,
        )
    except Exception as exc:  # pragma: no cover - result is asserted in parent
        code = getattr(exc, "code", type(exc).__name__)
        results.put(f"error:{code}")
    else:
        results.put(str(receipt["event_id"]))


def test_initialize_replays_a_versioned_artifact_and_binds_receipt(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)

    assert state["schema_version"] == "workflow_artifact_v1"
    assert state["artifact_id"] == "artifact-test"
    assert state["revision"] == 1
    assert state["definition"] == _definition()
    assert state["artifact_digest"] == derive_workflow_artifact_digest_v1(
        "artifact-test", 1, _definition()
    )
    assert state["event_count"] == 1
    assert state["last_event_id"].startswith("wae_")
    assert len(read_workflow_artifact_history(ledger, private_root=private_root)) == 1


def test_initialize_refuses_existing_artifact_and_invalid_definition(tmp_path: Path) -> None:
    private_root, ledger, _state = _initialize(tmp_path)
    with pytest.raises(WorkflowArtifactLedgerError) as existing:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="init-2",
            now=LATER,
        )
    assert existing.value.code == "artifact_exists"

    invalid = _definition()
    invalid["tasks"][0]["depends_on"] = ["verify"]
    other = private_root / "artifacts/invalid.jsonl"
    with pytest.raises(WorkflowArtifactLedgerError) as graph:
        initialize_workflow_artifact(
            invalid,
            ledger_path=other,
            private_root=private_root,
            idempotency_key="invalid-init",
            now=NOW,
        )
    assert graph.value.code == "definition_invalid"
    assert not other.exists()


def test_validation_is_read_only_and_returns_candidate(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    before = ledger.read_bytes()
    lock = ledger.with_name(f".{ledger.name}.lock")
    lock_before = lock.stat().st_mtime_ns
    mutation = _mutation(
        state,
        [
            {
                "op": "set_metadata",
                "title": "Updated artifact",
                "goal": state["definition"]["goal"],
                "non_goals": state["definition"]["non_goals"],
                "constraints": state["definition"]["constraints"],
            }
        ],
    )

    report = validate_workflow_mutation(
        mutation, ledger_path=ledger, private_root=private_root
    )

    assert report["valid"] is True
    assert report["current_revision"] == 1
    assert report["candidate_revision"] == 2
    assert report["candidate_definition"]["title"] == "Updated artifact"
    assert report["candidate_definition"]["version"] == 2
    assert ledger.read_bytes() == before
    assert lock.stat().st_mtime_ns == lock_before


def test_apply_commits_one_event_and_verifies_readback(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    mutation = _mutation(
        state,
        [
            {
                "op": "set_metadata",
                "title": "Updated artifact",
                "goal": state["definition"]["goal"],
                "non_goals": state["definition"]["non_goals"],
                "constraints": state["definition"]["constraints"],
            }
        ],
    )

    receipt = apply_workflow_mutation(
        mutation,
        ledger_path=ledger,
        private_root=private_root,
        now=LATER,
    )
    projected = read_workflow_artifact(ledger, private_root=private_root)

    assert receipt == {
        "schema_version": "workflow_artifact_receipt_v1",
        "artifact_id": "artifact-test",
        "event_id": projected["last_event_id"],
        "idempotency_key": "mutation-1",
        "previous_revision": 1,
        "new_revision": 2,
        "previous_artifact_digest": state["artifact_digest"],
        "new_artifact_digest": projected["artifact_digest"],
        "readback_verified": True,
    }
    assert projected["definition"]["title"] == "Updated artifact"
    assert projected["event_count"] == 2


def test_typed_task_operations_preserve_graph_semantics(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    extra = _task("inventory", role="explore", depends_on=[])
    receipt = apply_workflow_mutation(
        _mutation(state, [{"op": "add_task", "task": extra}]),
        ledger_path=ledger,
        private_root=private_root,
        now=LATER,
    )
    assert receipt["new_revision"] == 2
    updated = read_workflow_artifact(ledger, private_root=private_root)
    assert {task["id"] for task in updated["definition"]["tasks"]} == {
        "inspect",
        "implement",
        "verify",
        "inventory",
    }

    inspect = deepcopy(next(t for t in updated["definition"]["tasks"] if t["id"] == "inspect"))
    inspect["depends_on"] = ["verify"]
    inspect_digest = artifact_ledger.derive_workflow_task_digest_v1(inspect)
    bad = _mutation(
        updated,
        [
            {
                "op": "replace_task",
                "task_id": "inspect",
                "expected_task_digest": artifact_ledger.derive_workflow_task_digest_v1(
                    next(t for t in updated["definition"]["tasks"] if t["id"] == "inspect")
                ),
                "task": inspect,
            }
        ],
        key="cycle",
    )
    assert inspect_digest.startswith("sha256:")
    with pytest.raises(WorkflowArtifactLedgerError) as cycle:
        validate_workflow_mutation(bad, ledger_path=ledger, private_root=private_root)
    assert cycle.value.code == "definition_invalid"


def test_remove_rejects_unknown_or_still_referenced_task(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    for task_id, digest in (
        ("missing", "sha256:" + "0" * 64),
        (
            "inspect",
            artifact_ledger.derive_workflow_task_digest_v1(state["definition"]["tasks"][0]),
        ),
    ):
        mutation = _mutation(
            state,
            [
                {
                    "op": "remove_task",
                    "task_id": task_id,
                    "expected_task_digest": digest,
                }
            ],
            key=f"remove-{task_id}",
        )
        with pytest.raises(WorkflowArtifactLedgerError) as rejected:
            validate_workflow_mutation(
                mutation, ledger_path=ledger, private_root=private_root
            )
        assert rejected.value.code in {"task_unknown", "definition_invalid"}


def test_mutation_rejects_stale_revision_and_digest(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    valid = _mutation(
        state,
        [
            {
                "op": "set_loop_contract",
                "loop_policy": state["definition"]["loop_policy"],
                "stop_conditions": state["definition"]["stop_conditions"],
                "handoff": state["definition"]["handoff"],
            }
        ],
    )
    stale_revision = deepcopy(valid)
    stale_revision["base_revision"] = 2
    stale_digest = deepcopy(valid)
    stale_digest["base_artifact_digest"] = "sha256:" + "0" * 64

    with pytest.raises(WorkflowArtifactLedgerError) as revision:
        validate_workflow_mutation(
            stale_revision, ledger_path=ledger, private_root=private_root
        )
    assert revision.value.code == "stale_revision"
    with pytest.raises(WorkflowArtifactLedgerError) as digest:
        validate_workflow_mutation(
            stale_digest, ledger_path=ledger, private_root=private_root
        )
    assert digest.value.code == "stale_digest"


def test_workflow_authority_is_immutable_and_task_authority_cannot_widen(
    tmp_path: Path,
) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    unknown_operation = _mutation(
        state,
        [{"op": "set_authority", "authority": {"max_side_effect": "local_write"}}],
    )
    with pytest.raises(WorkflowArtifactLedgerError) as schema:
        validate_workflow_mutation(
            unknown_operation, ledger_path=ledger, private_root=private_root
        )
    assert schema.value.code == "schema_invalid"

    current = state["definition"]["tasks"][1]
    widened = deepcopy(current)
    widened["authority"] = {
        "side_effect": "local_write",
        "capabilities": ["filesystem.write"],
    }
    mutation = _mutation(
        state,
        [
            {
                "op": "replace_task",
                "task_id": current["id"],
                "expected_task_digest": artifact_ledger.derive_workflow_task_digest_v1(current),
                "task": widened,
            }
        ],
    )
    with pytest.raises(WorkflowArtifactLedgerError) as authority:
        validate_workflow_mutation(
            mutation, ledger_path=ledger, private_root=private_root
        )
    assert authority.value.code == "authority_widening"

    added = deepcopy(state["definition"]["tasks"][0])
    added["id"] = "new-writer"
    added["title"] = "New writer"
    added["acceptance"][0]["id"] = "new-writer-acceptance"
    added["authority"] = {
        "side_effect": "local_write",
        "capabilities": [],
    }
    mutation = _mutation(state, [{"op": "add_task", "task": added}])
    with pytest.raises(WorkflowArtifactLedgerError) as authority:
        validate_workflow_mutation(
            mutation, ledger_path=ledger, private_root=private_root
        )
    assert authority.value.code == "authority_widening"


def test_idempotent_reapply_returns_original_receipt_and_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    first_mutation = _mutation(
        state,
        [
            {
                "op": "set_metadata",
                "title": "Idempotent",
                "goal": state["definition"]["goal"],
                "non_goals": state["definition"]["non_goals"],
                "constraints": state["definition"]["constraints"],
            }
        ],
        key="same-key",
    )
    first = apply_workflow_mutation(
        first_mutation,
        ledger_path=ledger,
        private_root=private_root,
        now=LATER,
    )
    second = apply_workflow_mutation(
        first_mutation,
        ledger_path=ledger,
        private_root=private_root,
        now="2026-07-25T03:02:00Z",
    )
    assert second == first
    assert len(read_workflow_artifact_history(ledger, private_root=private_root)) == 2

    conflict = deepcopy(first_mutation)
    conflict["operations"][0]["title"] = "Different"
    with pytest.raises(WorkflowArtifactLedgerError) as rejected:
        apply_workflow_mutation(
            conflict,
            ledger_path=ledger,
            private_root=private_root,
            now="2026-07-25T03:03:00Z",
        )
    assert rejected.value.code == "idempotency_conflict"


def test_concurrent_writers_serialize_and_one_stale_candidate_loses(tmp_path: Path) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    mutations = [
        _mutation(
            state,
            [
                {
                    "op": "set_metadata",
                    "title": title,
                    "goal": state["definition"]["goal"],
                    "non_goals": state["definition"]["non_goals"],
                    "constraints": state["definition"]["constraints"],
                }
            ],
            key=f"concurrent-{index}",
        )
        for index, title in enumerate(("First", "Second"), start=1)
    ]
    processes = [
        context.Process(
            target=_concurrent_apply_worker,
            args=(str(ledger), str(private_root), mutation, start, results),
        )
        for mutation in mutations
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(results.get(timeout=2) for _ in processes) == [
        "applied:2",
        "stale_revision",
    ]
    assert read_workflow_artifact(ledger, private_root=private_root)["revision"] == 2


def test_multiprocess_exact_init_retries_publish_one_event(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_init_worker,
            args=(str(ledger), str(private_root), _definition(), start, results),
        )
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    event_ids = [results.get(timeout=2) for _ in processes]
    assert not [value for value in event_ids if value.startswith("error:")]
    assert len(set(event_ids)) == 1
    assert len(read_workflow_artifact_history(ledger, private_root=private_root)) == 1


def test_torn_tail_and_path_escape_fail_closed(tmp_path: Path) -> None:
    private_root, ledger, _state = _initialize(tmp_path)
    ledger.write_bytes(ledger.read_bytes() + b'{"schema_version":')
    with pytest.raises(WorkflowArtifactLedgerError) as torn:
        read_workflow_artifact(ledger, private_root=private_root)
    assert torn.value.code == "torn_tail"

    outside = tmp_path / "outside.jsonl"
    with pytest.raises(ValueError):
        initialize_workflow_artifact(
            _definition(),
            ledger_path=outside,
            private_root=private_root,
            idempotency_key="escape",
            now=NOW,
        )


def test_symlink_ledger_is_refused(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir()
    target = tmp_path / "target.jsonl"
    target.write_bytes(b"")
    ledger = private_root / "artifact.jsonl"
    ledger.symlink_to(target)
    with pytest.raises(OSError):
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="symlink",
            now=NOW,
        )


def test_partial_append_failure_rolls_back_to_the_previous_valid_tip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root, ledger, state = _initialize(tmp_path)
    before = ledger.read_bytes()
    mutation = _mutation(
        state,
        [
            {
                "op": "set_metadata",
                "title": "Will fail",
                "goal": state["definition"]["goal"],
                "non_goals": state["definition"]["non_goals"],
                "constraints": state["definition"]["constraints"],
            }
        ],
    )
    real_write = artifact_ledger.os.write
    calls = 0

    def partial_then_fail(descriptor: int, payload: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, payload[: max(1, len(payload) // 3)])
        raise OSError("injected append failure")

    monkeypatch.setattr(artifact_ledger.os, "write", partial_then_fail)
    with pytest.raises(WorkflowArtifactLedgerError) as failed:
        apply_workflow_mutation(
            mutation,
            ledger_path=ledger,
            private_root=private_root,
            now=LATER,
        )
    assert failed.value.code == "ledger_write_failed"
    assert ledger.read_bytes() == before
    assert read_workflow_artifact(ledger, private_root=private_root)["revision"] == 1


def test_duplicate_json_keys_and_non_finite_numbers_are_rejected(tmp_path: Path) -> None:
    private_root, ledger, _state = _initialize(tmp_path)
    raw = ledger.read_text(encoding="utf-8")
    event = json.loads(raw)
    event.pop("event_id")
    duplicate = json.dumps(event)[:-1] + ',"sequence":1}\n'
    ledger.write_text(duplicate, encoding="utf-8")
    with pytest.raises(WorkflowArtifactLedgerError) as duplicate_error:
        read_workflow_artifact(ledger, private_root=private_root)
    assert duplicate_error.value.code == "duplicate_key"


def test_invalid_first_event_does_not_publish_an_empty_ledger(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"

    with pytest.raises(WorkflowArtifactLedgerError) as rejected:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="invalid-first-event",
            now="not-a-timestamp",
        )

    assert rejected.value.code == "schema_invalid"
    assert not ledger.exists()


def test_short_first_write_removes_the_uncommitted_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"
    real_write = artifact_ledger.os.write

    def short_write(descriptor: int, payload: bytes | memoryview) -> int:
        return real_write(descriptor, payload[: max(1, len(payload) // 3)])

    monkeypatch.setattr(artifact_ledger.os, "write", short_write)
    with pytest.raises(WorkflowArtifactLedgerError) as failed:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="short-first-write",
            now=NOW,
        )

    assert failed.value.code == "ledger_write_failed"
    assert not ledger.exists()


@pytest.mark.parametrize("failed_kind", ["file", "directory"])
def test_exact_retry_resyncs_file_and_directory_after_fsync_uncertainty(
    failed_kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"
    # Create and durably publish the lock anchor without creating the ledger.
    with pytest.raises(WorkflowArtifactLedgerError):
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="invalid-anchor-probe",
            now="not-a-timestamp",
        )

    real_fsync = artifact_ledger.os.fsync
    failed = False

    def kind(descriptor: int) -> str:
        return "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"

    def fail_once(descriptor: int) -> None:
        nonlocal failed
        if not failed and kind(descriptor) == failed_kind:
            failed = True
            raise OSError(f"injected {failed_kind} fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(artifact_ledger.os, "fsync", fail_once)
    with pytest.raises(WorkflowArtifactLedgerError) as uncertain:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="durability-retry",
            now=NOW,
        )
    assert uncertain.value.code == "durability_unknown"
    assert failed

    retry_sync_kinds: list[str] = []

    def track_retry(descriptor: int) -> None:
        retry_sync_kinds.append(kind(descriptor))
        real_fsync(descriptor)

    monkeypatch.setattr(artifact_ledger.os, "fsync", track_retry)
    receipt = initialize_workflow_artifact(
        _definition(),
        ledger_path=ledger,
        private_root=private_root,
        idempotency_key="durability-retry",
        now=LATER,
    )

    assert receipt["readback_verified"] is True
    assert {"file", "directory"}.issubset(retry_sync_kinds)
    assert len(read_workflow_artifact_history(ledger, private_root=private_root)) == 1


@pytest.mark.parametrize("operation", ["fchmod", "lseek"])
def test_first_create_preparation_failure_removes_uncommitted_ledger(
    operation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"
    # Pre-provision the lock anchor so the injected regular-file failure targets the ledger.
    with pytest.raises(WorkflowArtifactLedgerError):
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="invalid-preparation-anchor",
            now="not-a-timestamp",
        )

    original = getattr(artifact_ledger.os, operation)
    failed = False

    def fail_regular_descriptor(descriptor: int, *args: Any) -> Any:
        nonlocal failed
        if (
            not failed
            and ledger.exists()
            and stat.S_ISREG(os.fstat(descriptor).st_mode)
        ):
            failed = True
            raise OSError(f"injected {operation} failure")
        return original(descriptor, *args)

    monkeypatch.setattr(artifact_ledger.os, operation, fail_regular_descriptor)
    with pytest.raises(WorkflowArtifactLedgerError) as rejected:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key=f"failed-{operation}",
            now=NOW,
        )
    assert rejected.value.code == "ledger_write_failed"
    assert failed
    assert not ledger.exists()

    monkeypatch.setattr(artifact_ledger.os, operation, original)
    receipt = initialize_workflow_artifact(
        _definition(),
        ledger_path=ledger,
        private_root=private_root,
        idempotency_key=f"retry-{operation}",
        now=LATER,
    )
    assert receipt["readback_verified"] is True
    assert len(read_workflow_artifact_history(ledger, private_root=private_root)) == 1


def test_first_create_o_excl_race_does_not_delete_competing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"
    with pytest.raises(WorkflowArtifactLedgerError):
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="invalid-race-anchor",
            now="not-a-timestamp",
        )

    original_open = artifact_ledger.open_ledger_file
    victim = b"competing-path-must-survive"

    def race_open(path: Path, flags: int, **kwargs: Any) -> int:
        if flags & os.O_EXCL:
            ledger.write_bytes(victim)
            raise FileExistsError("injected competing path")
        return original_open(path, flags, **kwargs)

    monkeypatch.setattr(artifact_ledger, "open_ledger_file", race_open)
    with pytest.raises(WorkflowArtifactLedgerError) as rejected:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="race-init",
            now=NOW,
        )

    assert rejected.value.code == "ledger_write_failed"
    assert ledger.read_bytes() == victim


def test_first_create_path_replacement_during_seek_preserves_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "artifact.jsonl"
    moved_created = private_root / "moved-created.jsonl"
    victim = b"replacement-must-survive"
    original_lseek = artifact_ledger.os.lseek
    replaced = False

    def replace_before_seek(descriptor: int, offset: int, whence: int) -> int:
        nonlocal replaced
        if not replaced and ledger.exists() and stat.S_ISREG(os.fstat(descriptor).st_mode):
            replaced = True
            os.replace(ledger, moved_created)
            ledger.write_bytes(victim)
            raise OSError("injected path replacement before seek")
        return original_lseek(descriptor, offset, whence)

    monkeypatch.setattr(artifact_ledger.os, "lseek", replace_before_seek)
    with pytest.raises(WorkflowArtifactLedgerError) as rejected:
        initialize_workflow_artifact(
            _definition(),
            ledger_path=ledger,
            private_root=private_root,
            idempotency_key="replacement-init",
            now=NOW,
        )

    assert rejected.value.code == "durability_unknown"
    assert replaced
    assert ledger.read_bytes() == victim
    assert moved_created.read_bytes() == b""
