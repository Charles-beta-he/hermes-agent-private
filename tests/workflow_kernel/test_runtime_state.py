from __future__ import annotations

from pathlib import Path

from hermes_cli import control_tower
from workflow_kernel.runtime_state import (
    limited_unattended_ledger_path,
    prefer_runtime_artifact,
    seed_jsonl_once,
    unattended_dry_run_dir,
    unattended_dry_run_ledger_path,
)


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


def test_seed_jsonl_once_allows_missing_legacy_without_creating_empty_ledger(tmp_path: Path) -> None:
    destination = tmp_path / "state" / "receipts.jsonl"
    result = seed_jsonl_once(tmp_path / "missing.jsonl", destination)
    assert result["status"] == "legacy_missing"
    assert not destination.exists()


def test_prefer_runtime_artifact_falls_back_to_frozen_snapshot(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.json"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    assert prefer_runtime_artifact(runtime, snapshot) == snapshot

    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('{"live":true}')
    assert prefer_runtime_artifact(runtime, snapshot) == runtime


def test_control_tower_phase55_path_prefers_profile_runtime_report(tmp_path: Path, monkeypatch) -> None:
    snapshot_root = tmp_path / "repo"
    snapshot = snapshot_root / "docs/private/workflow-kernel-unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    runtime = tmp_path / "profile/state/workflow-kernel/unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"snapshot":true}')
    monkeypatch.setattr(control_tower, "get_hermes_home", lambda: tmp_path / "profile")

    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == snapshot
    runtime.parent.mkdir(parents=True)
    runtime.write_text('{"live":true}')
    assert control_tower._workflow_kernel_phase55_path(snapshot_root) == runtime
