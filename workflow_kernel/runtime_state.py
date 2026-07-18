"""Profile-scoped mutable storage for Workflow Kernel runtime artifacts.

Repository ``docs/private`` files are frozen design/evidence snapshots. Recurring
ledgers and latest runtime reports live under ``HERMES_HOME/state`` so scheduled
runs do not mutate the source checkout.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from hermes_constants import get_hermes_home


def runtime_root(home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else get_hermes_home()
    return base / "state" / "workflow-kernel"


def limited_unattended_ledger_path(home: str | Path | None = None) -> Path:
    return runtime_root(home) / "limited-unattended" / "receipts.jsonl"


def unattended_dry_run_dir(home: str | Path | None = None) -> Path:
    return runtime_root(home) / "unattended-dry-run-scheduler"


def unattended_dry_run_ledger_path(home: str | Path | None = None) -> Path:
    return unattended_dry_run_dir(home) / "receipts.jsonl"


def unattended_dry_run_report_path(home: str | Path | None = None) -> Path:
    return unattended_dry_run_dir(home) / "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"


def seed_jsonl_once(legacy_path: Path, runtime_path: Path) -> dict[str, object]:
    """Atomically seed a runtime ledger without overwriting runtime history."""
    if runtime_path.exists():
        return {"status": "already_exists", "path": str(runtime_path)}
    if not legacy_path.exists():
        return {"status": "legacy_missing", "path": str(runtime_path)}

    payload = legacy_path.read_bytes()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = runtime_path.parent / f".{runtime_path.name}.seed-{os.getpid()}-{uuid.uuid4().hex}"
    temporary.write_bytes(payload)
    try:
        os.link(temporary, runtime_path)
        status = "seeded"
    except FileExistsError:
        status = "already_exists"
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": status,
        "path": str(runtime_path),
        "source": str(legacy_path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def prefer_runtime_artifact(runtime_path: Path, snapshot_path: Path) -> Path:
    return runtime_path if runtime_path.exists() else snapshot_path
