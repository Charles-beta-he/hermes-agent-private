"""Unified append-only run envelope ledger for Hermes control-plane surfaces.

RunEnvelopeV1 is the minimal shared fact shape used by otherwise independent
surfaces (kanban.db, local-brain markdown taskpool, dispatch tick JSON state).
Those legacy stores remain projections / compatibility layers; this module writes
an append-only JSONL fact stream plus a reducer checkpoint that can reconstruct
current run lifecycle state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import os
import time

try:  # POSIX interprocess lock; unavailable platforms fall back to best effort.
    import fcntl
except Exception:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

SCHEMA = "hermes_run_envelope_v1"
CHECKPOINT_SCHEMA = "hermes_run_reducer_checkpoint_v1"


def _ledger_dir() -> Path:
    override = os.environ.get("HERMES_RUN_LEDGER_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    return home / "state" / "run_ledger"


def build_projection_ref(projection: str, ref: str) -> dict[str, str]:
    """Return a normalized reference to a compatibility projection."""
    return {"projection": projection, "ref": str(ref)}


@dataclass(frozen=True)
class RunEnvelopeV1:
    run_id: str
    surface: str
    lifecycle_state: str
    trigger: dict[str, Any]
    proof_refs: list[str] = field(default_factory=list)
    projection_refs: list[dict[str, str]] = field(default_factory=list)
    handoff_refs: list[str] = field(default_factory=list)
    task_id: str | None = None
    session_id: str | None = None
    prompt_version: str | None = None
    tool_batch: list[dict[str, Any]] = field(default_factory=list)
    reducer_checkpoint: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    occurred_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        checkpoint = self.reducer_checkpoint or {
            "schema": CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "lifecycle_state": self.lifecycle_state,
        }
        return {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "surface": self.surface,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "trigger": self.trigger,
            "prompt_version": self.prompt_version,
            "tool_batch": self.tool_batch,
            "lifecycle_state": self.lifecycle_state,
            "proof_refs": self.proof_refs,
            "handoff_refs": self.handoff_refs,
            "projection_refs": self.projection_refs,
            "reducer_checkpoint": checkpoint,
            "metadata": self.metadata,
            "occurred_at": self.occurred_at,
        }


def _empty_checkpoint() -> dict[str, Any]:
    return {"schema": CHECKPOINT_SCHEMA, "last_event_seq": 0, "runs": {}}


def _reduce_payload_into_checkpoint(checkpoint: dict[str, Any], payload: dict[str, Any]) -> None:
    """Fold one serialized envelope payload into the reducer checkpoint."""
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return
    seq = int(payload.get("event_seq") or 0)
    runs = checkpoint.setdefault("runs", {})
    runs[run_id] = {
        "run_id": run_id,
        "surface": payload.get("surface"),
        "task_id": payload.get("task_id"),
        "session_id": payload.get("session_id"),
        "lifecycle_state": payload.get("lifecycle_state"),
        "proof_refs": list(payload.get("proof_refs") or []),
        "handoff_refs": list(payload.get("handoff_refs") or []),
        "projection_refs": list(payload.get("projection_refs") or []),
        "last_event_seq": seq,
        "updated_at": payload.get("occurred_at"),
    }
    checkpoint["last_event_seq"] = max(int(checkpoint.get("last_event_seq") or 0), seq)


def _rebuild_checkpoint_from_ledger(ledger_path: Path) -> dict[str, Any]:
    """Reconstruct reducer state from append-only JSONL when checkpoint is absent/corrupt."""
    checkpoint = _empty_checkpoint()
    if not ledger_path.exists():
        return checkpoint
    with ledger_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                _reduce_payload_into_checkpoint(checkpoint, payload)
    return checkpoint


def _load_checkpoint(path: Path, ledger_path: Path) -> dict[str, Any]:
    """Load reducer state with the append-only ledger as the authority.

    A syntactically valid checkpoint can still be stale if a process crashed
    after appending JSONL but before replacing the checkpoint. Rebuilding from
    JSONL under the append lock prevents duplicate event_seq allocation.
    """
    rebuilt = _rebuild_checkpoint_from_ledger(ledger_path)
    if rebuilt["last_event_seq"] > 0 or ledger_path.exists():
        return rebuilt
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("schema", CHECKPOINT_SCHEMA)
            data.setdefault("runs", {})
            data.setdefault("last_event_seq", 0)
            return data
    except Exception:
        pass
    return rebuilt


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def append_run_event(envelope: RunEnvelopeV1) -> dict[str, Any]:
    """Append one run fact and update the reducer checkpoint.

    The ledger append and checkpoint reduction are protected by a lightweight
    interprocess lock where available. If the checkpoint is absent/corrupt, it is
    rebuilt from the append-only JSONL ledger before the next event sequence is
    allocated, keeping the JSON checkpoint a disposable projection.
    """
    root = _ledger_dir()
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "run_events.jsonl"
    checkpoint_path = root / "run_reducer_checkpoint.json"
    lock_path = root / "run_ledger.lock"

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            checkpoint = _load_checkpoint(checkpoint_path, ledger_path)
            seq = int(checkpoint.get("last_event_seq") or 0) + 1
            payload = envelope.to_dict()
            payload["event_seq"] = seq

            with ledger_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

            _reduce_payload_into_checkpoint(checkpoint, payload)
            _atomic_write_json(checkpoint_path, checkpoint)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    return {
        "status": "OK",
        "schema": SCHEMA,
        "run_id": envelope.run_id,
        "event_seq": seq,
        "ledger_path": str(ledger_path),
        "checkpoint_path": str(checkpoint_path),
    }


def append_run_event_best_effort(envelope: RunEnvelopeV1) -> dict[str, Any]:
    try:
        return append_run_event(envelope)
    except Exception as exc:  # pragma: no cover - callers intentionally non-fatal
        return {"status": "ERROR", "schema": SCHEMA, "run_id": envelope.run_id, "error": str(exc)}
