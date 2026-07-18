from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from hermes_cli import control_tower
from workflow_kernel import runtime_state
from workflow_kernel.runtime_state import (
    ensure_private_directory,
    ensure_private_file,
    ensure_runtime_artifact_parent,
    exclusive_ledger_lock,
    limited_unattended_ledger_path,
    prefer_runtime_artifact,
    seed_jsonl_once,
    unattended_dry_run_dir,
    unattended_dry_run_ledger_path,
)


def _concurrent_seed_worker(source: str, destination: str, start, results) -> None:
    start.wait()
    results.put(seed_jsonl_once(Path(source), Path(destination))["status"])


def test_runtime_paths_are_profile_scoped_and_outside_repository(tmp_path: Path) -> None:
    assert limited_unattended_ledger_path(tmp_path) == tmp_path / "state/workflow-kernel/limited-unattended/receipts.jsonl"
    assert unattended_dry_run_dir(tmp_path) == tmp_path / "state/workflow-kernel/unattended-dry-run-scheduler"
    assert unattended_dry_run_ledger_path(tmp_path) == tmp_path / "state/workflow-kernel/unattended-dry-run-scheduler/receipts.jsonl"


def test_seed_jsonl_once_preserves_bytes_and_never_overwrites_runtime_history(tmp_path: Path) -> None:
    source = tmp_path / "legacy.jsonl"
    destination = tmp_path / "state" / "receipts.jsonl"
    source.write_bytes(b'{"id":1}\n{"id":2}\n')

    first = seed_jsonl_once(source, destination)
    assert first["status"] == "seeded"
    assert destination.read_bytes() == source.read_bytes()

    destination.write_bytes(destination.read_bytes() + b'{"id":3}\n')
    second = seed_jsonl_once(source, destination)
    assert second["status"] == "already_exists"
    assert destination.read_bytes() == b'{"id":1}\n{"id":2}\n{"id":3}\n'


def test_seed_jsonl_once_is_process_safe_and_never_overwrites_winner(tmp_path: Path) -> None:
    first_source = tmp_path / "legacy-a.jsonl"
    second_source = tmp_path / "legacy-b.jsonl"
    destination = tmp_path / "state/receipts.jsonl"
    first_source.write_bytes(b'{"source":"a"}\n')
    second_source.write_bytes(b'{"source":"b"}\n')
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_concurrent_seed_worker, args=(str(source), str(destination), start, results))
        for source in (first_source, second_source)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(results.get(timeout=2) for _ in processes) == ["already_exists", "seeded"]
    assert destination.read_bytes() in {first_source.read_bytes(), second_source.read_bytes()}


def test_seed_jsonl_once_allows_missing_legacy_without_creating_empty_ledger(tmp_path: Path) -> None:
    destination = tmp_path / "state" / "receipts.jsonl"
    result = seed_jsonl_once(tmp_path / "missing.jsonl", destination)
    assert result["status"] == "legacy_missing"
    assert not destination.exists()


def test_explicit_non_runtime_paths_keep_caller_managed_permissions(tmp_path: Path) -> None:
    explicit_parent = tmp_path / "explicit"
    explicit_parent.mkdir(mode=0o755)
    explicit_parent.chmod(0o755)
    source = tmp_path / "legacy.jsonl"
    source.write_bytes(b'{"id":1}\n')
    destination = explicit_parent / "receipts.jsonl"
    destination.write_bytes(b'{"existing":true}\n')
    destination.chmod(0o644)

    seed_jsonl_once(source, destination)
    with exclusive_ledger_lock(destination):
        pass

    assert explicit_parent.stat().st_mode & 0o777 == 0o755
    assert destination.stat().st_mode & 0o777 == 0o644


def test_same_named_explicit_tree_is_not_treated_as_private_runtime(tmp_path: Path) -> None:
    caller_root = tmp_path / "caller/workflow-kernel"
    caller_root.mkdir(parents=True, mode=0o755)
    caller_root.chmod(0o755)
    artifact = caller_root / "receipts.jsonl"

    assert ensure_runtime_artifact_parent(artifact) is False
    assert caller_root.stat().st_mode & 0o777 == 0o755


def test_runtime_artifacts_are_private_even_when_they_already_exist(tmp_path: Path) -> None:
    private_root = tmp_path / "state/workflow-kernel"
    directory = private_root / "limited-unattended"
    directory.mkdir(parents=True, mode=0o755)
    artifact = directory / "receipts.jsonl"
    artifact.write_text('{}\n', encoding="utf-8")
    directory.chmod(0o755)
    artifact.chmod(0o644)

    assert ensure_runtime_artifact_parent(artifact, private_root=private_root) is True
    ensure_private_file(artifact)

    assert directory.stat().st_mode & 0o777 == 0o700
    assert artifact.stat().st_mode & 0o777 == 0o600


def test_exclusive_ledger_lock_uses_private_lock_file(tmp_path: Path) -> None:
    private_root = tmp_path / "state/workflow-kernel"
    ledger = private_root / "runtime/receipts.jsonl"

    with exclusive_ledger_lock(ledger, private_root=private_root):
        lock_path = ledger.with_name(f".{ledger.name}.lock")
        assert lock_path.exists()
        assert lock_path.stat().st_mode & 0o777 == 0o600

    assert lock_path.exists()


def test_exclusive_ledger_lock_rejects_symlink_without_chmodding_target(tmp_path: Path) -> None:
    ledger = tmp_path / "explicit/receipts.jsonl"
    ledger.parent.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("do not touch", encoding="utf-8")
    target.chmod(0o644)
    lock_path = ledger.with_name(f".{ledger.name}.lock")
    os.symlink(target, lock_path)

    with pytest.raises(OSError):
        with exclusive_ledger_lock(ledger):
            pass

    assert target.stat().st_mode & 0o777 == 0o644


def test_ensure_private_file_rejects_symlink_without_chmodding_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("do not touch", encoding="utf-8")
    target.chmod(0o644)
    link = tmp_path / "link.txt"
    os.symlink(target, link)

    with pytest.raises(OSError):
        ensure_private_file(link)

    assert target.stat().st_mode & 0o777 == 0o644


def test_private_runtime_rejects_symlinked_ancestor_without_creating_outside_tree(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    (profile / "state").symlink_to(outside, target_is_directory=True)
    private_root = profile / "state/workflow-kernel"

    with pytest.raises(OSError, match="unsafe directory ancestor"):
        ensure_runtime_artifact_parent(
            private_root / "limited-unattended/receipts.jsonl",
            private_root=private_root,
        )

    assert not (outside / "workflow-kernel").exists()


def test_private_runtime_rejects_parent_component_escape_without_chmod(tmp_path: Path) -> None:
    private_root = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    outside.chmod(0o755)

    with pytest.raises(ValueError, match="parent path components"):
        ensure_runtime_artifact_parent(
            private_root / "../outside/receipts.jsonl",
            private_root=private_root,
        )

    assert outside.stat().st_mode & 0o777 == 0o755


def test_directory_chain_closes_next_descriptor_when_validation_fails(tmp_path: Path, monkeypatch) -> None:
    original_open = os.open
    original_fstat = os.fstat
    original_close = os.close
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def failing_fstat(descriptor: int):
        if len(opened) >= 2 and descriptor == opened[-1]:
            raise OSError("simulated fstat failure")
        return original_fstat(descriptor)

    def tracked_close(descriptor: int):
        closed.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(runtime_state.os, "open", tracked_open)
    monkeypatch.setattr(runtime_state.os, "fstat", failing_fstat)
    monkeypatch.setattr(runtime_state.os, "close", tracked_close)

    with pytest.raises(OSError, match="unsafe directory ancestor"):
        ensure_private_directory(tmp_path / "private")

    assert opened[-1] in closed


def test_private_file_fails_closed_without_no_follow_support(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_text('{}\n', encoding="utf-8")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    with pytest.raises(OSError, match="no-follow semantics unsupported"):
        ensure_private_file(artifact)


def test_prefer_runtime_artifact_falls_back_to_frozen_snapshot(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.json"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    assert prefer_runtime_artifact(runtime, snapshot) == snapshot

    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('{"live":true}')
    assert prefer_runtime_artifact(runtime, snapshot) == runtime


def phase55_report(marker: str) -> str:
    return (
        '{"phase":55,"report_id":"workflow_kernel_unattended_dry_run_scheduler_20260613_phase55",'
        '"status":"UNATTENDED_DRY_RUN_SCHEDULER_READY","external_side_effects_executed":false,'
        '"cron_state_mutated":false,"unattended_dispatch_enabled":false,'
        f'"latest_receipt":{{"marker":"{marker}","schema":"workflow_kernel_unattended_dry_run_receipt_v1",'
        '"status":"UNATTENDED_DRY_RUN_RECEIPT_RECORDED","phase":55,"mode":"dry_run_preview",'
        '"blockers":[],"rate_limit_decision":{"decision":"dry_run_allowed"},'
        '"external_side_effects_executed":false,'
        '"unattended_dispatch_enabled":false,"would_send":false}}'
    )


def phase55_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    snapshot_root = tmp_path / "repo"
    snapshot = snapshot_root / "docs/private/workflow-kernel-unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    runtime = tmp_path / "profile/state/workflow-kernel/unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    return snapshot_root, snapshot, runtime


def test_control_tower_phase55_path_prefers_valid_profile_runtime_report(tmp_path: Path, monkeypatch) -> None:
    snapshot_root, snapshot, runtime = phase55_paths(tmp_path)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(phase55_report("snapshot"), encoding="utf-8")
    monkeypatch.setattr(control_tower, "get_hermes_home", lambda: tmp_path / "profile")

    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == snapshot
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "frozen_baseline"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(phase55_report("runtime"), encoding="utf-8")
    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == runtime
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "runtime"
    runtime.unlink()
    snapshot.unlink()
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "missing"


@pytest.mark.parametrize("invalid_runtime", ["{not-json", "[]", '{"phase":54}'])
def test_control_tower_phase55_falls_back_when_runtime_report_is_invalid(tmp_path: Path, monkeypatch, invalid_runtime: str) -> None:
    snapshot_root, snapshot, runtime = phase55_paths(tmp_path)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(phase55_report("snapshot"), encoding="utf-8")
    runtime.parent.mkdir(parents=True)
    runtime.write_text(invalid_runtime, encoding="utf-8")
    monkeypatch.setattr(control_tower, "get_hermes_home", lambda: tmp_path / "profile")

    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == snapshot
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "frozen_baseline"


def test_control_tower_phase55_falls_back_when_runtime_path_is_directory(tmp_path: Path, monkeypatch) -> None:
    snapshot_root, snapshot, runtime = phase55_paths(tmp_path)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(phase55_report("snapshot"), encoding="utf-8")
    runtime.mkdir(parents=True)
    monkeypatch.setattr(control_tower, "get_hermes_home", lambda: tmp_path / "profile")

    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == snapshot
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "frozen_baseline"


def test_control_tower_phase55_falls_back_when_runtime_report_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    snapshot_root, snapshot, runtime = phase55_paths(tmp_path)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(phase55_report("snapshot"), encoding="utf-8")
    runtime.parent.mkdir(parents=True)
    runtime.write_text(phase55_report("runtime"), encoding="utf-8")
    monkeypatch.setattr(control_tower, "get_hermes_home", lambda: tmp_path / "profile")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == runtime:
            raise PermissionError("simulated unreadable runtime report")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == snapshot
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "frozen_baseline"


@pytest.mark.parametrize(
    ("field", "value", "nested"),
    [
        ("status", "UNSAFE", False),
        ("external_side_effects_executed", True, False),
        ("cron_state_mutated", True, False),
        ("unattended_dispatch_enabled", True, False),
        ("schema", "unexpected", True),
        ("status", "UNSAFE", True),
        ("external_side_effects_executed", True, True),
        ("unattended_dispatch_enabled", True, True),
        ("would_send", True, True),
        ("phase", 999, True),
        ("mode", "execute", True),
        ("blockers", ["policy_gate_not_ready"], True),
        ("rate_limit_decision", {"decision": "blocked"}, True),
    ],
)
def test_control_tower_phase55_falls_back_when_runtime_safety_invariant_is_invalid(
    tmp_path: Path,
    monkeypatch,
    field: str,
    value: object,
    nested: bool,
) -> None:
    snapshot_root, snapshot, runtime = phase55_paths(tmp_path)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(phase55_report("snapshot"), encoding="utf-8")
    runtime.parent.mkdir(parents=True)
    import json

    runtime_data = json.loads(phase55_report("runtime"))
    target = runtime_data["latest_receipt"] if nested else runtime_data
    target[field] = value
    runtime.write_text(json.dumps(runtime_data), encoding="utf-8")
    monkeypatch.setattr(control_tower, "get_hermes_home", lambda: tmp_path / "profile")

    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == snapshot
    assert control_tower._workflow_kernel_phase55_source(snapshot_root) == "frozen_baseline"


def test_private_atomic_write_stays_anchored_when_parent_path_is_swapped(tmp_path, monkeypatch):
    private_root = tmp_path / "state/workflow-kernel"
    parent = private_root / "reports"
    parked = private_root / "reports-parked"
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact = parent / "phase55.json"
    original_replace = runtime_state.os.replace
    original_rename = runtime_state.os.rename
    swapped = False

    def swap_then_replace(source, destination, **kwargs):
        nonlocal swapped
        if not swapped:
            original_rename(parent, parked)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr(runtime_state.os, "replace", swap_then_replace)
    result = runtime_state.write_private_file_atomic(artifact, b'{"safe":true}\n', private_root=private_root)

    assert result["path"] == str(artifact)
    assert not (outside / artifact.name).exists()
    assert (parked / artifact.name).read_bytes() == b'{"safe":true}\n'


def test_private_ledger_transaction_stays_anchored_when_parent_path_is_swapped(tmp_path):
    private_root = tmp_path / "state/workflow-kernel"
    parent = private_root / "limited-unattended"
    parked = private_root / "limited-unattended-parked"
    outside = tmp_path / "outside"
    outside.mkdir()
    ledger = parent / "receipts.jsonl"
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text('{"seed":true}\n', encoding="utf-8")

    with runtime_state.exclusive_ledger_lock(ledger, private_root=private_root) as anchor:
        assert anchor is not None
        parent.rename(parked)
        parent.symlink_to(outside, target_is_directory=True)
        runtime_state.seed_jsonl_once(legacy, ledger, private_root=private_root, anchor=anchor)
        descriptor = runtime_state.open_ledger_file(
            ledger,
            os.O_APPEND | os.O_WRONLY,
            private_root=private_root,
            anchor=anchor,
        )
        try:
            os.write(descriptor, b'{"append":true}\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        anchored = runtime_state.read_ledger_bytes(ledger, private_root=private_root, anchor=anchor)

    assert anchored == b'{"seed":true}\n{"append":true}\n'
    assert (parked / ledger.name).read_bytes() == anchored
    assert not (outside / ledger.name).exists()
    assert not (outside / f".{ledger.name}.lock").exists()


@pytest.mark.parametrize("replace_fails", [False, True])
def test_private_atomic_write_cleanup_failure_closes_parent_and_preserves_primary_error(
    tmp_path,
    monkeypatch,
    replace_fails,
):
    private_root = tmp_path / "state/workflow-kernel"
    artifact = private_root / "reports/phase55.json"
    original_close = runtime_state.os.close
    original_replace = runtime_state.os.replace
    opened_parent: list[int] = []
    closed: list[int] = []
    original_open_chain = runtime_state._open_directory_chain

    def tracked_open_chain(path):
        descriptor = original_open_chain(path)
        opened_parent.append(descriptor)
        return descriptor

    def tracked_close(descriptor):
        closed.append(descriptor)
        return original_close(descriptor)

    def failing_unlink(*args, **kwargs):
        raise PermissionError("cleanup failed")

    def maybe_failing_replace(*args, **kwargs):
        if replace_fails:
            raise OSError("primary replace failure")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(runtime_state, "_open_directory_chain", tracked_open_chain)
    monkeypatch.setattr(runtime_state.os, "close", tracked_close)
    monkeypatch.setattr(runtime_state.os, "unlink", failing_unlink)
    monkeypatch.setattr(runtime_state.os, "replace", maybe_failing_replace)

    if replace_fails:
        with pytest.raises(OSError, match="primary replace failure"):
            runtime_state.write_private_file_atomic(artifact, b'{}\n', private_root=private_root)
    else:
        runtime_state.write_private_file_atomic(artifact, b'{}\n', private_root=private_root)
        assert artifact.read_bytes() == b'{}\n'

    assert opened_parent[-1] in closed
