from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

import workflow_kernel.taskpool_cas as taskpool_cas
from workflow_kernel.taskpool_cas import TaskpoolCASError, transition_task_note

NOW = "2026-08-03T00:00:00Z"
LEASE = "2099-08-03T01:00:00Z"


def _note(status: str = "pending", *, extra: str = "", body: str = "# Task\n\nKeep me.\n") -> bytes:
    return (
        "---\n"
        "type: task\n"
        f"status: {status}\n"
        "priority: P1\n"
        "evidence:\n"
        "  - proof:legacy\n"
        f"{extra}"
        "---\n"
        f"{body}"
    ).encode()


def _setup(tmp_path: Path, *, status: str = "pending", extra: str = "") -> tuple[Path, Path]:
    root = tmp_path / "tasks"
    root.mkdir(mode=0o700)
    task = root / "alpha.md"
    task.write_bytes(_note(status, extra=extra))
    return root, task


def _transition(root: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tasks_root": root,
        "task_id": "alpha",
        "expected_status": "pending",
        "expected_revision": 0,
        "expected_fence": None,
        "to_status": "claimed",
        "actor": "worker-a",
        "run_id": "run-a",
        "lease_expires_at": LEASE,
        "reason": "",
        "evidence": (),
        "idempotency_key": "claim-alpha",
    }
    values.update(overrides)
    return transition_task_note(**cast(Any, values))


def _error_code(root: Path, **overrides: object) -> str:
    try:
        _transition(root, **overrides)
    except TaskpoolCASError as exc:
        return exc.code
    return "success"


def _claim_worker(root: str, start, results, key: str, actor: str) -> None:
    start.wait()
    results.put(_error_code(Path(root), idempotency_key=key, actor=actor, run_id=f"run-{actor}"))


def test_claim_bootstraps_revision_and_fence_and_preserves_unknown_markdown(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    before = task.read_text()

    receipt = _transition(root)
    after = task.read_text()

    assert receipt["schema_version"] == "taskpool_transition_receipt_v1"
    assert receipt["from_status"] == "pending"
    assert receipt["to_status"] == "claimed"
    assert receipt["previous_revision"] == 0
    assert receipt["new_revision"] == 1
    assert receipt["previous_fence"] == 0
    assert receipt["new_fence"] == 1
    assert receipt["idempotent_replay"] is False
    assert "priority: P1\nevidence:\n  - proof:legacy\n" in after
    assert "# Task\n\nKeep me.\n" in after
    assert "status: claimed\n" in after
    assert "taskpool_revision: 1\n" in after
    assert "taskpool_fence: 1\n" in after
    assert "taskpool_claimed_by: worker-a\n" in after
    normalized = task.read_bytes().replace(
        str(receipt["canonical_post_write_sha256"]).encode(),
        ("sha256:" + "0" * 64).encode(),
        1,
    )
    assert receipt["canonical_post_write_sha256"] == "sha256:" + hashlib.sha256(normalized).hexdigest()
    assert before != after


def test_exact_retry_replays_even_after_later_transition(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    first = _transition(root)
    second = _transition(
        root,
        expected_status="claimed",
        expected_revision=1,
        expected_fence=1,
        to_status="running",
        lease_expires_at=None,
        idempotency_key="run-alpha",
    )
    before_retry = task.read_bytes()

    replay = _transition(root)

    assert replay["request_digest"] == first["request_digest"]
    assert replay["new_revision"] == 1
    assert replay["idempotent_replay"] is True
    assert second["new_revision"] == 2
    assert task.read_bytes() == before_retry


def test_same_idempotency_key_with_different_request_conflicts(tmp_path: Path) -> None:
    root, _ = _setup(tmp_path)
    _transition(root)
    assert _error_code(root, actor="worker-b") == "idempotency_conflict"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"expected_revision": 1}, "stale_revision"),
        ({"expected_status": "retry"}, "status_mismatch"),
        ({"to_status": "done"}, "unsupported_transition"),
        ({"lease_expires_at": NOW}, "invalid_lease"),
        ({"task_id": "../alpha"}, "invalid_task_id"),
        ({"idempotency_key": "bad key"}, "invalid_idempotency_key"),
    ],
)
def test_invalid_claim_inputs_fail_before_mutation(tmp_path: Path, overrides: dict, code: str) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()
    assert _error_code(root, **overrides) == code
    assert task.read_bytes() == before


def test_claim_bound_transition_requires_exact_fence_owner_run_and_live_lease(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    _transition(root)
    canonical = task.read_bytes()

    cases = [
        ({"expected_fence": None}, "expected_fence_required"),
        ({"expected_fence": 0}, "stale_fence"),
        ({"actor": "worker-b"}, "claim_owner_mismatch"),
        ({"run_id": "run-b"}, "claim_run_mismatch"),
    ]
    for index, (override, code) in enumerate(cases):
        params = {
            "expected_status": "claimed",
            "expected_revision": 1,
            "expected_fence": 1,
            "to_status": "running",
            "lease_expires_at": None,
            "idempotency_key": f"attempt-{index}",
        }
        params.update(override)
        assert _error_code(root, **params) == code
        assert task.read_bytes() == canonical


def test_caller_cannot_backdate_clock_and_internal_clock_rejects_expired_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task = _setup(tmp_path)
    with pytest.raises(TypeError):
        _transition(root, now=NOW)
    before = task.read_bytes()
    _transition(root)
    canonical = task.read_bytes()
    assert before != canonical
    after_lease = datetime(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(taskpool_cas, "_utc_now", lambda: ("2100-01-01T00:00:00Z", after_lease))
    assert (
        _error_code(
            root,
            expected_status="claimed",
            expected_revision=1,
            expected_fence=1,
            to_status="running",
            lease_expires_at=None,
            idempotency_key="expired-attempt",
        )
        == "lease_expired"
    )
    assert task.read_bytes() == canonical


def test_running_to_review_requires_structured_evidence(tmp_path: Path) -> None:
    root, _ = _setup(tmp_path)
    _transition(root)
    _transition(
        root,
        expected_status="claimed",
        expected_revision=1,
        expected_fence=1,
        to_status="running",
        lease_expires_at=None,
        idempotency_key="run-alpha",
    )
    base = {
        "expected_status": "running",
        "expected_revision": 2,
        "expected_fence": 2,
        "to_status": "review",
        "lease_expires_at": None,
        "idempotency_key": "review-alpha",
    }
    assert _error_code(root, **base) == "evidence_required"
    assert _error_code(root, **{**base, "evidence": ("bare proof",)}) == "invalid_evidence"
    receipt = _transition(root, **{**base, "evidence": ("command:pytest PASS",)})
    assert receipt["to_status"] == "review"
    text = (root / "alpha.md").read_text()
    assert "taskpool_claimed_by: \n" in text
    assert "taskpool_run_id: \n" in text
    assert "taskpool_claim_expires_at: \n" in text


def test_retry_and_blocked_require_reason(tmp_path: Path) -> None:
    root, _ = _setup(tmp_path)
    _transition(root)
    base = {
        "expected_status": "claimed",
        "expected_revision": 1,
        "expected_fence": 1,
        "lease_expires_at": None,
    }
    assert _error_code(root, **base, to_status="retry", idempotency_key="retry") == "reason_required"
    receipt = _transition(root, **base, to_status="blocked", reason="dependency", idempotency_key="blocked")
    assert receipt["to_status"] == "blocked"


@pytest.mark.parametrize(
    "extra",
    [
        "taskpool_revision: 1\n",
        "taskpool_fence: 1\n",
        "taskpool_revision: nope\ntaskpool_fence: 1\n",
        "taskpool_revision: -1\ntaskpool_fence: 1\n",
        f"taskpool_revision: {'9' * 5000}\ntaskpool_fence: 0\n",
        "taskpool_revision: 0\ntaskpool_fence: 0\ntaskpool_revision: 0\n",
        "taskpool_claimed_by: ghost\n",
        (
            "taskpool_revision: 0\n"
            "taskpool_fence: 0\n"
            "taskpool_claimed_by: ghost\n"
            "taskpool_run_id: ghost-run\n"
            "taskpool_claim_expires_at: 2099-01-01T00:00:00Z\n"
        ),
    ],
)
def test_incomplete_malformed_or_duplicate_metadata_fails_closed(tmp_path: Path, extra: str) -> None:
    root, task = _setup(tmp_path, extra=extra)
    before = task.read_bytes()
    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


@pytest.mark.parametrize(
    ("ambiguous_line", "code"),
    [
        ("status : done", "invalid_task_note"),
        ('"status": done', "invalid_task_note"),
        ('"\\u0073tatus": done', "invalid_task_note"),
        ("!!str status: done", "invalid_task_note"),
        ("!owned status: done", "invalid_task_note"),
        ("&status_key status: done", "invalid_task_note"),
        ("&a !!str status: done", "invalid_task_note"),
        ("!!str &a status: done", "invalid_task_note"),
        ("? status\n: done", "invalid_task_note"),
        ("<<: *task_defaults", "invalid_task_note"),
        ("<< : *task_defaults", "invalid_task_note"),
        ("<<    : *task_defaults", "invalid_task_note"),
        ("  << : *task_defaults", "invalid_task_note"),
        ("taskpool_revision : 99", "invalid_task_metadata"),
        ("'taskpool_revision': 99", "invalid_task_metadata"),
    ],
)
def test_yaml_equivalent_owned_key_spellings_fail_closed(
    tmp_path: Path, ambiguous_line: str, code: str
) -> None:
    root = tmp_path / "tasks"
    root.mkdir(mode=0o700)
    task = root / "alpha.md"
    task.write_bytes(_note(extra=f"{ambiguous_line}\n"))
    before = task.read_bytes()

    assert _error_code(root) == code
    assert task.read_bytes() == before


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("hidden_key", ["status: done", "taskpool_revision: 99"])
def test_yaml_line_separator_cannot_hide_owned_key(
    tmp_path: Path, separator: str, hidden_key: str
) -> None:
    root = tmp_path / "tasks"
    root.mkdir(mode=0o700)
    task = root / "alpha.md"
    task.write_bytes(_note(extra=f"unknown: x{separator}{hidden_key}\n"))
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_note"
    assert task.read_bytes() == before


def test_huge_public_counter_returns_stable_domain_error_before_io(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, expected_revision=10**5000) == "invalid_expected_revision"
    assert task.read_bytes() == before


@pytest.mark.parametrize("field", ["expected_status", "to_status"])
def test_unhashable_public_status_returns_stable_domain_error(tmp_path: Path, field: str) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, **{field: ["pending"]}) == "invalid_status"
    assert task.read_bytes() == before


def test_hostile_string_subclass_status_returns_stable_domain_error(tmp_path: Path) -> None:
    class ExplodingStatus(str):
        def __hash__(self) -> int:
            raise RuntimeError("untrusted hash")

    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, expected_status=ExplodingStatus("pending")) == "invalid_status"
    assert task.read_bytes() == before


def test_hostile_evidence_sequence_returns_stable_domain_error(tmp_path: Path) -> None:
    class ExplodingEvidence(list[str]):
        def __len__(self) -> int:
            raise RuntimeError("untrusted length")

    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, evidence=ExplodingEvidence(["proof:x"])) == "invalid_evidence"
    assert task.read_bytes() == before


def test_lying_evidence_sequence_cannot_bypass_item_bound(tmp_path: Path) -> None:
    class LyingEvidence(list[str]):
        def __len__(self) -> int:
            return 1

        def __iter__(self):
            return iter(f"proof:{index}" for index in range(65))

    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, evidence=LyingEvidence(["proof:x"])) == "invalid_evidence"
    assert task.read_bytes() == before


@pytest.mark.parametrize("invalid_root", [None, [], 7])
def test_invalid_tasks_root_shape_returns_stable_domain_error(
    tmp_path: Path, invalid_root: object
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, tasks_root=invalid_root) == "invalid_tasks_root"
    assert task.read_bytes() == before


@pytest.mark.parametrize("bad_component", ["bad\0root", "bad\ud800root", "bad\udc80root"])
def test_invalid_tasks_root_scalar_content_returns_stable_domain_error(
    tmp_path: Path, bad_component: str
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, tasks_root=tmp_path / bad_component) == "invalid_tasks_root"
    assert task.read_bytes() == before


def test_tasks_root_pathlike_exception_returns_stable_domain_error(tmp_path: Path) -> None:
    class ExplodingPath:
        def __fspath__(self) -> str:
            raise RuntimeError("untrusted path conversion")

    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, tasks_root=ExplodingPath()) == "invalid_tasks_root"
    assert task.read_bytes() == before


def test_deeply_nested_idempotency_json_returns_stable_domain_error(tmp_path: Path) -> None:
    nested = '{"a":' * 20_000 + "0" + "}" * 20_000
    root, task = _setup(
        tmp_path,
        extra=(
            "taskpool_revision: 0\n"
            "taskpool_fence: 0\n"
            f"taskpool_idempotency_v1: {nested}\n"
        ),
    )
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


def test_malformed_frontmatter_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    root.mkdir()
    task = root / "alpha.md"
    task.write_text("---\ntype: task\nstatus: pending\n")
    before = task.read_bytes()
    assert _error_code(root) == "invalid_task_note"
    assert task.read_bytes() == before


def test_legacy_claim_bound_note_without_fence_metadata_fails_closed(tmp_path: Path) -> None:
    root, task = _setup(tmp_path, status="claimed")
    before = task.read_bytes()
    assert _error_code(root, expected_status="claimed", to_status="running", lease_expires_at=None) == "invalid_task_metadata"
    assert task.read_bytes() == before


def test_symlink_and_hardlink_task_leaves_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    root.mkdir()
    victim = tmp_path / "victim.md"
    victim.write_bytes(_note())
    symlink = root / "alpha.md"
    symlink.symlink_to(victim)
    assert _error_code(root) == "unsafe_task_path"
    symlink.unlink()
    os.link(victim, symlink)
    assert _error_code(root) == "unsafe_task_path"
    assert victim.read_bytes() == _note()


def test_symlink_root_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "alpha.md").write_bytes(_note())
    linked = tmp_path / "tasks"
    linked.symlink_to(real, target_is_directory=True)
    assert _error_code(linked) == "unsafe_task_root"
    assert (real / "alpha.md").read_bytes() == _note()


def test_darwin_system_alias_is_not_silently_resolved(tmp_path: Path) -> None:
    if sys.platform != "darwin" or tmp_path.parts[:3] != ("/", "private", "var"):
        pytest.skip("Darwin /var -> /private/var alias is unavailable")
    root, task = _setup(tmp_path)
    alias_root = Path("/").joinpath(*tmp_path.parts[2:], root.name)
    before = task.read_bytes()

    assert alias_root.is_dir()
    assert _error_code(alias_root) == "unsafe_task_root"
    assert task.read_bytes() == before


def test_root_reverification_rejects_intermediate_symlink_alias(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    root = real_parent / "tasks"
    root.mkdir()
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    alias_root = alias_parent / "tasks"

    descriptor = taskpool_cas._open_existing_directory(root)
    try:
        pinned = os.fstat(descriptor)
        with pytest.raises(TaskpoolCASError) as raised:
            taskpool_cas._verify_root(alias_root, descriptor, pinned)
    finally:
        os.close(descriptor)

    assert raised.value.code == "root_replaced"


def test_open_task_closes_descriptor_when_initial_fstat_fails(
    tmp_path: Path, monkeypatch
) -> None:
    root, _ = _setup(tmp_path)
    root_descriptor = taskpool_cas._open_existing_directory(root)
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    opened_task_descriptor: int | None = None
    closed: list[int] = []

    def capture_open(*args, **kwargs) -> int:
        nonlocal opened_task_descriptor
        opened_task_descriptor = real_open(*args, **kwargs)
        return opened_task_descriptor

    def fail_task_fstat(descriptor: int):
        if descriptor == opened_task_descriptor:
            raise OSError("injected task fstat failure")
        return real_fstat(descriptor)

    def capture_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(taskpool_cas.os, "open", capture_open)
    monkeypatch.setattr(taskpool_cas.os, "fstat", fail_task_fstat)
    monkeypatch.setattr(taskpool_cas.os, "close", capture_close)
    try:
        with pytest.raises(OSError, match="injected task fstat failure"):
            taskpool_cas._open_task(root_descriptor, "alpha.md")
    finally:
        real_close(root_descriptor)

    assert opened_task_descriptor is not None
    assert opened_task_descriptor in closed


def test_initial_root_descriptor_fstat_failure_is_stable_domain_error(
    tmp_path: Path, monkeypatch
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    real_fstat = os.fstat

    monkeypatch.setattr(taskpool_cas, "_open_existing_directory", lambda _root: descriptor)

    def fail_root_fstat(current: int):
        if current == descriptor:
            raise OSError("injected root descriptor fstat failure")
        return real_fstat(current)

    monkeypatch.setattr(taskpool_cas.os, "fstat", fail_root_fstat)

    assert _error_code(root) == "unsafe_task_root"
    assert task.read_bytes() == before


def test_second_task_descriptor_fstat_failure_is_stable_domain_error(
    tmp_path: Path, monkeypatch
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()
    real_open_task = taskpool_cas._open_task
    real_fstat = os.fstat
    task_descriptor: int | None = None
    task_fstat_count = 0

    def capture_open_task(root_descriptor: int, name: str) -> int:
        nonlocal task_descriptor
        task_descriptor = real_open_task(root_descriptor, name)
        return task_descriptor

    def fail_second_task_fstat(current: int):
        nonlocal task_fstat_count
        if current == task_descriptor:
            task_fstat_count += 1
            if task_fstat_count == 1:
                raise OSError("injected second task descriptor fstat failure")
        return real_fstat(current)

    monkeypatch.setattr(taskpool_cas, "_open_task", capture_open_task)
    monkeypatch.setattr(taskpool_cas.os, "fstat", fail_second_task_fstat)

    assert _error_code(root) == "unsafe_task_path"
    assert task.read_bytes() == before


def test_pre_replace_fsync_failure_leaves_canonical_note_unchanged(tmp_path: Path, monkeypatch) -> None:
    import workflow_kernel.taskpool_cas as module

    root, task = _setup(tmp_path)
    before = task.read_bytes()

    def fail(_descriptor: int) -> None:
        raise OSError("injected file fsync failure")

    monkeypatch.setattr(module, "_fsync_file", fail)
    assert _error_code(root) == "write_failed"
    assert task.read_bytes() == before
    assert sorted(path.name for path in root.iterdir()) == ["alpha.md"]


def test_post_replace_directory_fsync_failure_is_durability_unknown_and_retryable(tmp_path: Path, monkeypatch) -> None:
    import workflow_kernel.taskpool_cas as module

    root, task = _setup(tmp_path)
    real = module._fsync_directory
    failed = False

    def fail_once(descriptor: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected directory fsync failure")
        real(descriptor)

    monkeypatch.setattr(module, "_fsync_directory", fail_once)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)
    assert raised.value.code == "durability_unknown"
    assert "status: claimed\n" in task.read_text()

    monkeypatch.setattr(module, "_fsync_directory", real)
    replay = _transition(root)
    assert replay["idempotent_replay"] is True


def test_task_replacement_before_commit_is_detected_without_overwriting_replacement(tmp_path: Path, monkeypatch) -> None:
    import workflow_kernel.taskpool_cas as module

    root, task = _setup(tmp_path)
    original = task.read_bytes()
    detached = root / "detached.md"
    attacker = _note("retry", body="# attacker\n")

    def replace(stage: str) -> None:
        if stage == "before_replace":
            task.rename(detached)
            task.write_bytes(attacker)

    monkeypatch.setattr(module, "_TEST_HOOK", replace)
    assert _error_code(root) == "task_replaced"
    assert task.read_bytes() == attacker
    assert detached.read_bytes() == original
    assert all(".tmp" not in path.name for path in root.iterdir())


def test_root_replacement_before_commit_is_detected_without_mutating_either_task(tmp_path: Path, monkeypatch) -> None:
    import workflow_kernel.taskpool_cas as module

    root, task = _setup(tmp_path)
    original = task.read_bytes()
    moved = tmp_path / "moved"
    attacker = _note("retry", body="# attacker\n")

    def replace(stage: str) -> None:
        if stage == "before_replace":
            root.rename(moved)
            root.mkdir()
            (root / "alpha.md").write_bytes(attacker)

    monkeypatch.setattr(module, "_TEST_HOOK", replace)
    assert _error_code(root) == "root_replaced"
    assert (moved / "alpha.md").read_bytes() == original
    assert (root / "alpha.md").read_bytes() == attacker
    assert all(".tmp" not in path.name for path in moved.iterdir())


def test_task_replacement_after_commit_reports_unknown_without_overwriting_attacker(tmp_path: Path, monkeypatch) -> None:
    root, task = _setup(tmp_path)
    detached = root / "committed-detached.md"
    attacker = _note("retry", body="# attacker\n")

    def replace(stage: str) -> None:
        if stage == "after_replace":
            task.rename(detached)
            task.write_bytes(attacker)

    monkeypatch.setattr(taskpool_cas, "_TEST_HOOK", replace)
    assert _error_code(root) == "durability_unknown"
    assert task.read_bytes() == attacker
    assert b"status: claimed\n" in detached.read_bytes()


def test_root_replacement_after_commit_reports_unknown_without_mutating_replacement(tmp_path: Path, monkeypatch) -> None:
    root, _ = _setup(tmp_path)
    moved = tmp_path / "committed-moved"
    attacker = _note("retry", body="# attacker\n")

    def replace(stage: str) -> None:
        if stage == "after_replace":
            root.rename(moved)
            root.mkdir()
            (root / "alpha.md").write_bytes(attacker)

    monkeypatch.setattr(taskpool_cas, "_TEST_HOOK", replace)
    assert _error_code(root) == "durability_unknown"
    assert (root / "alpha.md").read_bytes() == attacker
    assert b"status: claimed\n" in (moved / "alpha.md").read_bytes()


def test_spawned_claim_contenders_have_exactly_one_winner(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_claim_worker, args=(str(root), start, results, f"claim-{index}", f"worker-{index}"))
        for index in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert outcomes.count("success") == 1
    assert outcomes.count("stale_revision") == 7
    text = task.read_text()
    assert text.count("taskpool_revision: 1\n") == 1
    assert text.count("taskpool_fence: 1\n") == 1
    metadata_line = next(line for line in text.splitlines() if line.startswith("taskpool_idempotency_v1: "))
    assert len(json.loads(metadata_line.split(": ", 1)[1])) == 1


def test_idempotency_map_capacity_fails_without_eviction(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    for index in range(32):
        revision = index * 2
        transition_task_note(
            tasks_root=root,
            task_id="alpha",
            expected_status="pending" if index == 0 else "retry",
            expected_revision=revision,
            expected_fence=None,
            to_status="claimed",
            actor="worker-a",
            run_id="run-a",
            lease_expires_at=LEASE,
            reason="",
            evidence=(),
            idempotency_key=f"capacity-claim-{index}",
        )
        transition_task_note(
            tasks_root=root,
            task_id="alpha",
            expected_status="claimed",
            expected_revision=revision + 1,
            expected_fence=revision + 1,
            to_status="retry",
            actor="worker-a",
            run_id="run-a",
            lease_expires_at=None,
            reason="bounded capacity exercise",
            evidence=(),
            idempotency_key=f"capacity-retry-{index}",
        )
    before = task.read_bytes()
    assert _error_code(
        root,
        expected_status="retry",
        expected_revision=64,
        expected_fence=None,
        idempotency_key="capacity-overflow",
    ) == "idempotency_capacity_exhausted"
    assert task.read_bytes() == before


def _forged_replay_note(
    *,
    receipt_updates: dict[str, object] | None = None,
    receipt_extra: dict[str, object] | None = None,
) -> bytes:
    request: dict[str, object] = {
        "task_id": "alpha",
        "expected_status": "pending",
        "expected_revision": 0,
        "expected_fence": None,
        "to_status": "claimed",
        "actor": "worker-a",
        "run_id": "run-a",
        "lease_expires_at": LEASE,
        "reason": "",
        "evidence": [],
        "idempotency_key": "claim-alpha",
    }
    digest = taskpool_cas._request_digest(request)
    receipt: dict[str, object] = {
        "schema_version": "taskpool_transition_receipt_v1",
        "task_id": "alpha",
        "idempotency_key": "claim-alpha",
        "request_digest": digest,
        "from_status": "pending",
        "to_status": "claimed",
        "previous_revision": 0,
        "new_revision": 1,
        "previous_fence": 0,
        "new_fence": 1,
        "actor": "worker-a",
        "run_id": "run-a",
        "occurred_at": NOW,
        "lease_expires_at": LEASE,
        "canonical_post_write_sha256": "sha256:" + "1" * 64,
        "idempotent_replay": False,
    }
    receipt.update(receipt_updates or {})
    receipt.update(receipt_extra or {})
    entries = {"claim-alpha": {"request_digest": digest, "receipt": receipt}}
    return _note(
        extra=(
            "taskpool_revision: 0\n"
            "taskpool_fence: 0\n"
            f"taskpool_idempotency_v1: {json.dumps(entries, sort_keys=True, separators=(',', ':'))}\n"
        )
    )


@pytest.mark.parametrize(
    "note",
    [
        _forged_replay_note(receipt_updates={"from_status": "done", "to_status": "archived"}),
        _forged_replay_note(receipt_updates={"actor": "attacker"}),
        _forged_replay_note(receipt_updates={"occurred_at": "not-a-time"}),
        _forged_replay_note(receipt_extra={"attacker_field": "accepted"}),
    ],
)
def test_forged_stored_replay_receipts_fail_closed(tmp_path: Path, note: bytes) -> None:
    root = tmp_path / "tasks"
    root.mkdir(mode=0o700)
    task = root / "alpha.md"
    task.write_bytes(note)
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


def test_exact_replay_revalidates_request_owned_receipt_fields(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    _transition(root)
    lines = task.read_text().splitlines(keepends=True)
    metadata_index = next(
        index for index, line in enumerate(lines) if line.startswith("taskpool_idempotency_v1: ")
    )
    owner_index = lines.index("taskpool_claimed_by: worker-a\n")
    entries = json.loads(lines[metadata_index].split(": ", 1)[1])
    receipt = entries["claim-alpha"]["receipt"]
    receipt["actor"] = "attacker"
    receipt["canonical_post_write_sha256"] = "sha256:" + "0" * 64
    lines[owner_index] = "taskpool_claimed_by: attacker\n"
    lines[metadata_index] = (
        "taskpool_idempotency_v1: "
        + json.dumps(entries, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    normalized = "".join(lines).encode()
    receipt["canonical_post_write_sha256"] = "sha256:" + hashlib.sha256(normalized).hexdigest()
    lines[metadata_index] = (
        "taskpool_idempotency_v1: "
        + json.dumps(entries, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    task.write_text("".join(lines))
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


def test_exact_replay_rejects_request_fence_not_bound_to_receipt(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    _transition(root)
    lines = task.read_text().splitlines(keepends=True)
    metadata_index = next(
        index for index, line in enumerate(lines) if line.startswith("taskpool_idempotency_v1: ")
    )
    entries = json.loads(lines[metadata_index].split(": ", 1)[1])
    request: dict[str, object] = {
        "task_id": "alpha",
        "expected_status": "pending",
        "expected_revision": 0,
        "expected_fence": 999,
        "to_status": "claimed",
        "actor": "worker-a",
        "run_id": "run-a",
        "lease_expires_at": LEASE,
        "reason": "",
        "evidence": [],
        "idempotency_key": "claim-alpha",
    }
    forged_digest = taskpool_cas._request_digest(request)
    entry = entries["claim-alpha"]
    receipt = entry["receipt"]
    entry["request_digest"] = forged_digest
    receipt["request_digest"] = forged_digest
    receipt["canonical_post_write_sha256"] = "sha256:" + "0" * 64
    lines[metadata_index] = (
        "taskpool_idempotency_v1: "
        + json.dumps(entries, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    normalized = "".join(lines).encode()
    receipt["canonical_post_write_sha256"] = "sha256:" + hashlib.sha256(normalized).hexdigest()
    lines[metadata_index] = (
        "taskpool_idempotency_v1: "
        + json.dumps(entries, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    task.write_text("".join(lines))
    before = task.read_bytes()

    assert _error_code(root, expected_fence=999) == "invalid_task_metadata"
    assert task.read_bytes() == before


@pytest.mark.parametrize(
    "request_updates",
    [
        {
            "expected_status": "claimed",
            "expected_revision": 1,
            "expected_fence": 1,
            "to_status": "blocked",
            "lease_expires_at": None,
            "reason": "",
        },
        {
            "expected_status": "running",
            "expected_revision": 1,
            "expected_fence": 1,
            "to_status": "review",
            "lease_expires_at": None,
            "evidence": [],
        },
        {
            "expected_status": "pending",
            "expected_revision": 0,
            "expected_fence": None,
            "to_status": "review",
            "lease_expires_at": LEASE,
            "evidence": ["proof:ok"],
        },
    ],
)
def test_exact_replay_revalidates_request_lifecycle_policy(request_updates: dict[str, object]) -> None:
    request: dict[str, object] = {
        "task_id": "alpha",
        "expected_status": "pending",
        "expected_revision": 0,
        "expected_fence": None,
        "to_status": "claimed",
        "actor": "worker-a",
        "run_id": "run-a",
        "lease_expires_at": LEASE,
        "reason": "valid reason",
        "evidence": ["proof:ok"],
        "idempotency_key": "replay-policy",
    }
    request.update(request_updates)
    receipt: dict[str, object] = {
        "task_id": request["task_id"],
        "idempotency_key": request["idempotency_key"],
        "request_digest": taskpool_cas._request_digest(request),
        "from_status": request["expected_status"],
        "to_status": request["to_status"],
        "previous_revision": request["expected_revision"],
        "previous_fence": request["expected_revision"],
        "actor": request["actor"],
        "run_id": request["run_id"],
        "lease_expires_at": request["lease_expires_at"] if request["to_status"] == "claimed" else None,
    }

    with pytest.raises(TaskpoolCASError) as raised:
        taskpool_cas._validate_replay_receipt(receipt, request)

    assert raised.value.code == "invalid_task_metadata"


@pytest.mark.parametrize(
    ("entry_key", "receipt_updates"),
    [
        ("run-alpha", {"actor": "worker-b", "run_id": "run-b"}),
        ("run-alpha", {"lease_expires_at": "2098-01-01T00:00:00Z"}),
        ("run-alpha", {"occurred_at": "2000-01-01T00:00:00Z"}),
        ("retry-alpha", {"occurred_at": "2100-01-01T00:00:00Z"}),
    ],
)
def test_forged_history_claim_and_time_continuity_fails_closed(
    tmp_path: Path, entry_key: str, receipt_updates: dict[str, object]
) -> None:
    root, task = _setup(tmp_path)
    _transition(root)
    _transition(
        root,
        expected_status="claimed",
        expected_revision=1,
        expected_fence=1,
        to_status="running",
        lease_expires_at=None,
        idempotency_key="run-alpha",
    )
    _transition(
        root,
        expected_status="running",
        expected_revision=2,
        expected_fence=2,
        to_status="retry",
        lease_expires_at=None,
        reason="retry after failure",
        idempotency_key="retry-alpha",
    )
    lines = task.read_text().splitlines(keepends=True)
    metadata_index = next(
        index for index, line in enumerate(lines) if line.startswith("taskpool_idempotency_v1: ")
    )
    entries = json.loads(lines[metadata_index].split(": ", 1)[1])
    entries[entry_key]["receipt"].update(receipt_updates)
    latest = entries["retry-alpha"]["receipt"]
    latest["canonical_post_write_sha256"] = taskpool_cas.ZERO_DIGEST
    lines[metadata_index] = (
        "taskpool_idempotency_v1: "
        + json.dumps(entries, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    latest["canonical_post_write_sha256"] = "sha256:" + hashlib.sha256(
        "".join(lines).encode()
    ).hexdigest()
    lines[metadata_index] = (
        "taskpool_idempotency_v1: "
        + json.dumps(entries, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    task.write_text("".join(lines))
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


def test_forged_revision_one_claim_bound_origin_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    root.mkdir(mode=0o700)
    task = root / "alpha.md"
    request: dict[str, object] = {
        "task_id": "alpha",
        "expected_status": "claimed",
        "expected_revision": 0,
        "expected_fence": 0,
        "to_status": "running",
        "actor": "attacker",
        "run_id": "forged-run",
        "lease_expires_at": None,
        "reason": "",
        "evidence": [],
        "idempotency_key": "forged-origin",
    }
    request_digest = taskpool_cas._request_digest(request)
    receipt: dict[str, object] = {
        "schema_version": "taskpool_transition_receipt_v1",
        "task_id": "alpha",
        "idempotency_key": "forged-origin",
        "request_digest": request_digest,
        "from_status": "claimed",
        "to_status": "running",
        "previous_revision": 0,
        "new_revision": 1,
        "previous_fence": 0,
        "new_fence": 1,
        "actor": "attacker",
        "run_id": "forged-run",
        "occurred_at": NOW,
        "lease_expires_at": LEASE,
        "canonical_post_write_sha256": taskpool_cas.ZERO_DIGEST,
        "idempotent_replay": False,
    }
    entries = {"forged-origin": {"request_digest": request_digest, "receipt": receipt}}

    def render() -> bytes:
        return _note(
            "running",
            extra=(
                "taskpool_revision: 1\n"
                "taskpool_fence: 1\n"
                "taskpool_claimed_by: attacker\n"
                "taskpool_run_id: forged-run\n"
                f"taskpool_claim_expires_at: {LEASE}\n"
                "taskpool_idempotency_v1: "
                + json.dumps(entries, sort_keys=True, separators=(",", ":"))
                + "\n"
            ),
        )

    receipt["canonical_post_write_sha256"] = "sha256:" + hashlib.sha256(render()).hexdigest()
    task.write_bytes(render())
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


@pytest.mark.parametrize(
    "lease",
    [
        "2099-08-03 01:00:00Z",
        "2099-08-03\t01:00:00Z",
        "2099-08-03\x1b01:00:00Z",
        "2099-08-03\u202801:00:00Z",
        "2099-08-03T01:00:00+00:00",
    ],
)
def test_noncanonical_or_control_wrapped_lease_fails_before_mutation(
    tmp_path: Path, lease: str
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, lease_expires_at=lease) == "invalid_time"
    assert task.read_bytes() == before


@pytest.mark.parametrize(
    "raw_map",
    [
        '{"duplicate":{},"duplicate":{}}',
        '{ }',
    ],
)
def test_non_strict_or_noncanonical_idempotency_json_fails_closed(tmp_path: Path, raw_map: str) -> None:
    root, task = _setup(
        tmp_path,
        extra=(
            "taskpool_revision: 0\n"
            "taskpool_fence: 0\n"
            f"taskpool_idempotency_v1: {raw_map}\n"
        ),
    )
    before = task.read_bytes()

    assert _error_code(root) == "invalid_task_metadata"
    assert task.read_bytes() == before


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"evidence": None}, "invalid_evidence"),
        ({"evidence": ("proof:\ud800",)}, "invalid_evidence"),
        ({"reason": "forged\u2028audit"}, "invalid_reason"),
        ({"reason": " reason "}, "invalid_reason"),
        ({"reason": " " * 100_000}, "invalid_reason"),
        ({"evidence": ("proof:x\u2028",)}, "invalid_evidence"),
        ({"evidence": ("\tproof:x\t",)}, "invalid_evidence"),
        ({"evidence": (" " * 100_000 + "proof:x",)}, "invalid_evidence"),
        ({"evidence": ("proof:\x1b[31mforged",)}, "invalid_evidence"),
        ({"reason": "x" * 4097}, "invalid_reason"),
        ({"evidence": tuple(f"proof:{index}" for index in range(65))}, "invalid_evidence"),
    ],
)
def test_public_text_collections_are_bounded_and_unicode_safe(
    tmp_path: Path, overrides: dict, code: str
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    assert _error_code(root, **overrides) == code
    assert task.read_bytes() == before


def test_any_post_replace_validation_failure_is_durability_unknown(tmp_path: Path, monkeypatch) -> None:
    root, task = _setup(tmp_path)
    real_parse = taskpool_cas._parse_note
    calls = 0

    def fail_readback(data: bytes):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TaskpoolCASError("invalid_task_note", "injected post-replace parse failure")
        return real_parse(data)

    monkeypatch.setattr(taskpool_cas, "_parse_note", fail_readback)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)

    assert raised.value.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_any_post_replace_non_domain_failure_is_durability_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    root, task = _setup(tmp_path)

    def fail_after_replace(label: str) -> None:
        if label == "after_replace":
            raise RuntimeError("injected non-domain failure")

    monkeypatch.setattr(taskpool_cas, "_TEST_HOOK", fail_after_replace)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)

    assert raised.value.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_any_post_replace_base_exception_is_durability_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    class InjectedBaseException(BaseException):
        pass

    root, task = _setup(tmp_path)

    def fail_after_replace(label: str) -> None:
        if label == "after_replace":
            raise InjectedBaseException("injected post-replace process-control failure")

    monkeypatch.setattr(taskpool_cas, "_TEST_HOOK", fail_after_replace)
    try:
        _transition(root)
    except BaseException as exc:
        raised = exc
    else:
        pytest.fail("post-replace failure unexpectedly returned success")

    assert isinstance(raised, TaskpoolCASError)
    assert raised.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_replace_then_raise_is_commit_uncertain(tmp_path: Path, monkeypatch) -> None:
    root, task = _setup(tmp_path)
    real_replace = os.replace

    def replace_then_raise(*args, **kwargs) -> None:
        real_replace(*args, **kwargs)
        raise OSError("injected ambiguous replace outcome")

    monkeypatch.setattr(os, "replace", replace_then_raise)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)

    assert raised.value.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_post_attempt_cleanup_base_exception_cannot_override_durability_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    class InjectedBaseException(BaseException):
        pass

    root, task = _setup(tmp_path)
    real_open = os.open
    real_fstat = os.fstat
    real_replace = os.replace
    temporary_descriptor: int | None = None
    replacement_attempted = False

    def capture_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal temporary_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".alpha.md.") and path.endswith(".tmp"):
            temporary_descriptor = descriptor
        return descriptor

    def replace_then_raise(*args, **kwargs) -> None:
        nonlocal replacement_attempted
        real_replace(*args, **kwargs)
        replacement_attempted = True
        raise OSError("injected ambiguous replace outcome")

    def interrupt_cleanup(descriptor: int):
        if replacement_attempted and descriptor == temporary_descriptor:
            raise InjectedBaseException("injected cleanup interruption")
        return real_fstat(descriptor)

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(os, "replace", replace_then_raise)
    monkeypatch.setattr(os, "fstat", interrupt_cleanup)
    try:
        _transition(root)
    except BaseException as exc:
        raised = exc
    else:
        pytest.fail("ambiguous replacement unexpectedly returned success")

    assert isinstance(raised, TaskpoolCASError)
    assert raised.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_post_attempt_close_base_exception_cannot_override_durability_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    class InjectedBaseException(BaseException):
        pass

    root, task = _setup(tmp_path)
    real_open = os.open
    real_close = os.close
    real_replace = os.replace
    temporary_descriptor: int | None = None
    replacement_attempted = False

    def capture_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal temporary_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".alpha.md.") and path.endswith(".tmp"):
            temporary_descriptor = descriptor
        return descriptor

    def replace_then_raise(*args, **kwargs) -> None:
        nonlocal replacement_attempted
        real_replace(*args, **kwargs)
        replacement_attempted = True
        raise OSError("injected ambiguous replace outcome")

    def interrupt_temp_close(descriptor: int) -> None:
        if replacement_attempted and descriptor == temporary_descriptor:
            raise InjectedBaseException("injected close interruption")
        real_close(descriptor)

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(os, "replace", replace_then_raise)
    monkeypatch.setattr(os, "close", interrupt_temp_close)
    try:
        _transition(root)
    except BaseException as exc:
        raised = exc
    else:
        pytest.fail("ambiguous replacement unexpectedly returned success")

    assert isinstance(raised, TaskpoolCASError)
    assert raised.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_post_replace_mode_change_is_durability_unknown(tmp_path: Path, monkeypatch) -> None:
    root, task = _setup(tmp_path)

    def mutate_mode(label: str) -> None:
        if label == "after_replace":
            task.chmod(0o600)

    monkeypatch.setattr(taskpool_cas, "_TEST_HOOK", mutate_mode)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)

    assert raised.value.code == "durability_unknown"
    assert b"status: claimed\n" in task.read_bytes()


def test_post_replace_group_change_is_durability_unknown(tmp_path: Path, monkeypatch) -> None:
    root, task = _setup(tmp_path)
    alternate_groups = [group for group in os.getgroups() if group != root.stat().st_gid]
    if not alternate_groups:
        pytest.skip("effective user has no alternate group for ownership-preservation probe")
    canonical_gid = alternate_groups[0]
    task.chmod(0o640)
    os.chown(task, -1, canonical_gid)

    def mutate_group(label: str) -> None:
        if label == "after_replace":
            os.chown(task, -1, root.stat().st_gid)

    monkeypatch.setattr(taskpool_cas, "_TEST_HOOK", mutate_group)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)

    assert raised.value.code == "durability_unknown"


def test_unpreservable_mode_fails_before_replace_without_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    root, task = _setup(tmp_path)
    task.chmod(0o640)
    before = task.read_bytes()
    monkeypatch.setattr(os, "fchmod", lambda _descriptor, _mode: None)
    previous_umask = os.umask(0o077)
    try:
        assert _error_code(root) == "write_failed"
    finally:
        os.umask(previous_umask)

    assert task.read_bytes() == before
    assert task.stat().st_mode & 0o7777 == 0o640


def test_precommit_task_read_failure_is_stable_domain_error(
    tmp_path: Path, monkeypatch
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()

    def fail_read(_descriptor: int, _size: int) -> bytes:
        raise OSError(5, "injected EIO")

    monkeypatch.setattr(os, "read", fail_read)
    with pytest.raises(TaskpoolCASError) as raised:
        _transition(root)

    assert raised.value.code == "read_failed"
    assert task.read_bytes() == before


def test_initial_temporary_identity_failure_leaves_no_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()
    real_open = os.open
    real_fstat = os.fstat
    temporary_descriptor: int | None = None
    failed = False

    def capture_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal temporary_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".alpha.md.") and path.endswith(".tmp"):
            temporary_descriptor = descriptor
        return descriptor

    def fail_first_temp_fstat(descriptor: int):
        nonlocal failed
        if descriptor == temporary_descriptor and not failed:
            failed = True
            raise OSError(5, "injected temporary identity failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(os, "fstat", fail_first_temp_fstat)

    assert _error_code(root) == "write_failed"
    assert task.read_bytes() == before
    assert sorted(path.name for path in root.iterdir()) == ["alpha.md"]


@pytest.mark.parametrize("mode", [0o640, 0o2640, 0o4640, 0o6640])
def test_transition_preserves_exact_task_mode_despite_process_umask(
    tmp_path: Path, mode: int
) -> None:
    root, task = _setup(tmp_path)
    os.chown(task, -1, os.getegid())
    task.chmod(mode)
    assert task.stat().st_mode & 0o7777 == mode
    previous_umask = os.umask(0o077)
    try:
        _transition(root)
    finally:
        os.umask(previous_umask)

    assert task.stat().st_mode & 0o7777 == mode


def test_transition_preserves_task_group_without_setgid(tmp_path: Path) -> None:
    root, task = _setup(tmp_path)
    alternate_groups = [group for group in os.getgroups() if group != root.stat().st_gid]
    if not alternate_groups:
        pytest.skip("effective user has no alternate group for ownership-preservation probe")
    expected_gid = alternate_groups[0]
    os.chown(task, -1, expected_gid)
    task.chmod(0o640)

    _transition(root)

    assert task.stat().st_gid == expected_gid
    assert task.stat().st_mode & 0o7777 == 0o640


def test_temporary_candidate_is_created_private_before_content_write(
    tmp_path: Path, monkeypatch
) -> None:
    root, _task = _setup(tmp_path)
    requested_modes: list[int] = []
    real_open = os.open

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        if isinstance(path, str) and path.startswith(".alpha.md.") and path.endswith(".tmp"):
            requested_modes.append(mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", record_open)
    _transition(root)

    assert requested_modes == [0o600]


@pytest.mark.parametrize("mode", [0o770, 0o777])
def test_group_or_world_writable_task_root_is_rejected(tmp_path: Path, mode: int) -> None:
    root, task = _setup(tmp_path)
    root.chmod(mode)
    before = task.read_bytes()

    assert _error_code(root) == "unsafe_task_root"
    assert task.read_bytes() == before


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_group_or_world_writable_task_leaf_is_rejected(tmp_path: Path, mode: int) -> None:
    root, task = _setup(tmp_path)
    task.chmod(mode)
    before = task.read_bytes()

    assert _error_code(root) == "unsafe_task_path"
    assert task.read_bytes() == before


def test_task_leaf_not_owned_by_effective_user_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root, task = _setup(tmp_path)
    before = task.read_bytes()
    real_fstat = os.fstat

    def foreign_file_owner(descriptor: int) -> os.stat_result:
        info = real_fstat(descriptor)
        if stat.S_ISREG(info.st_mode):
            fields = list(info)
            fields[4] = info.st_uid + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "fstat", foreign_file_owner)

    assert _error_code(root) == "unsafe_task_path"
    assert task.read_bytes() == before
