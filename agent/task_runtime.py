"""Durable task-centric multi-session control plane.

The store is deliberately provider- and entrypoint-neutral.  CLI, Telegram,
delegated workers, and closure sessions exchange immutable packets through one
SQLite SSOT; natural-language conversation summaries are never authoritative.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import sqlite3
import time
import uuid
from typing import Any, Iterator, Mapping, Sequence


SCHEMA = "hermes_task_runtime_v1"
CONTEXT_PACKET_SCHEMA = "hermes_context_packet_v1"
WORKER_RESULT_SCHEMA = "hermes_worker_result_v1"
TERMINAL_SLICE_STATES = {"completed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _task_id() -> str:
    return f"task_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"


def _result_id(packet_id: str, worker_session_id: str, result: Mapping[str, Any]) -> str:
    return "result_" + _digest(
        {"packet_id": packet_id, "worker_session_id": worker_session_id, "result": dict(result)}
    ).split(":", 1)[1][:24]


class RevisionConflict(RuntimeError):
    pass


class TaskRuntimeStore:
    """SQLite SSOT for tasks, bindings, slices, packets, results, and closure."""

    def __init__(self, path: str | Path, *, owner_profile: str | None = None):
        self.path = Path(path).expanduser()
        self.owner_profile = str(owner_profile or os.environ.get("HERMES_PROFILE") or "default").strip()
        if not self.owner_profile:
            raise ValueError("owner_profile_required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    schema_name TEXT NOT NULL,
                    owner_profile TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_sources_json TEXT NOT NULL,
                    source_events_json TEXT NOT NULL,
                    spec_bundle_json TEXT NOT NULL,
                    implementation_graph_json TEXT NOT NULL,
                    evidence_graph_json TEXT NOT NULL,
                    closure_json TEXT NOT NULL,
                    spec_revision INTEGER NOT NULL,
                    plan_revision INTEGER NOT NULL,
                    state_revision INTEGER NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entrypoint_bindings (
                    entrypoint TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (entrypoint, session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bindings_task ON entrypoint_bindings(task_id);
                CREATE TABLE IF NOT EXISTS source_events (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_events_task ON source_events(task_id, recorded_at, event_id);
                CREATE TRIGGER IF NOT EXISTS source_events_no_update
                BEFORE UPDATE ON source_events
                BEGIN
                    SELECT RAISE(ABORT, 'source_events_append_only');
                END;
                CREATE TRIGGER IF NOT EXISTS source_events_no_delete
                BEFORE DELETE ON source_events
                BEGIN
                    SELECT RAISE(ABORT, 'source_events_append_only');
                END;
                CREATE TABLE IF NOT EXISTS task_state_events (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    state_revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, state_revision),
                    UNIQUE (task_id, event_digest)
                );
                CREATE INDEX IF NOT EXISTS idx_task_state_events_task
                    ON task_state_events(task_id, state_revision);
                CREATE TRIGGER IF NOT EXISTS task_state_events_no_update
                BEFORE UPDATE ON task_state_events
                BEGIN
                    SELECT RAISE(ABORT, 'task_state_events_append_only');
                END;
                CREATE TRIGGER IF NOT EXISTS task_state_events_no_delete
                BEFORE DELETE ON task_state_events
                BEGIN
                    SELECT RAISE(ABORT, 'task_state_events_append_only');
                END;
                CREATE TABLE IF NOT EXISTS task_slices (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    slice_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    definition_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    lease_expires_at REAL,
                    result_id TEXT NOT NULL,
                    integrated_state_revision INTEGER,
                    PRIMARY KEY (task_id, slice_id)
                );
                CREATE TABLE IF NOT EXISTS context_packets (
                    packet_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    slice_id TEXT NOT NULL,
                    packet_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    spec_revision INTEGER NOT NULL,
                    plan_revision INTEGER NOT NULL,
                    state_revision INTEGER NOT NULL,
                    lease_owner TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_packets_task_slice ON context_packets(task_id, slice_id);
                CREATE TABLE IF NOT EXISTS worker_results (
                    result_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    slice_id TEXT NOT NULL,
                    packet_id TEXT NOT NULL REFERENCES context_packets(packet_id),
                    worker_session_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    integrator_session_id TEXT NOT NULL,
                    integrated_state_revision INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_results_task ON worker_results(task_id, disposition);
                """
            )
            task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "owner_profile" not in task_columns:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN owner_profile TEXT NOT NULL DEFAULT 'default'"
                )
                conn.execute(
                    "UPDATE tasks SET owner_profile = ?",
                    (self.owner_profile,),
                )
            # One-time compatibility backfill from the legacy mutable JSON snapshot.
            # The normalized table is authoritative after this point; INSERT OR IGNORE
            # preserves exact replay semantics if an older database is reopened.
            rows = conn.execute(
                "SELECT task_id, source_events_json FROM tasks WHERE source_events_json != '[]'"
            ).fetchall()
            for row in rows:
                for event in _decode(row["source_events_json"], []):
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("event_id") or _digest(event))
                    payload = dict(event)
                    payload["event_id"] = event_id
                    recorded_at = str(payload.get("recorded_at") or _utc_now())
                    payload["recorded_at"] = recorded_at
                    conn.execute(
                        "INSERT OR IGNORE INTO source_events "
                        "(task_id, event_id, payload_json, recorded_at) VALUES (?, ?, ?, ?)",
                        (row["task_id"], event_id, _json(payload), recorded_at),
                    )
            legacy_rows = conn.execute(
                "SELECT task_id, state_revision, status FROM tasks WHERE NOT EXISTS "
                "(SELECT 1 FROM task_state_events e WHERE e.task_id = tasks.task_id)"
            ).fetchall()
            for row in legacy_rows:
                self._record_state_event(
                    conn,
                    row["task_id"],
                    int(row["state_revision"]),
                    "legacy_projection_anchored",
                    {"status": row["status"]},
                )

    @staticmethod
    def default_path() -> Path:
        import os

        explicit = os.environ.get("HERMES_TASK_RUNTIME_DB")
        if explicit:
            return Path(explicit).expanduser()

        from hermes_constants import get_hermes_home

        return get_hermes_home() / "state" / "task-runtime" / "task-runtime.db"

    def create_task(
        self,
        *,
        title: str,
        original_sources: Sequence[Mapping[str, Any]],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        sources = [dict(item) for item in original_sources]
        if not sources:
            raise ValueError("original_sources_required")
        for item in sources:
            if not str(item.get("source_id") or "") or not str(item.get("content") or ""):
                raise ValueError("original_source_missing_identity_or_content")
        now = _utc_now()
        identity = task_id or _task_id()
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, schema_name, owner_profile, title, status, original_sources_json,
                    source_events_json, spec_bundle_json, implementation_graph_json,
                    evidence_graph_json, closure_json, spec_revision, plan_revision,
                    state_revision, candidate_digest, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, '[]', '{}', '[]', '[]', '{}', 0, 0, 1, '', ?, ?)
                """,
                (
                    identity,
                    SCHEMA,
                    self.owner_profile,
                    str(title or "Untitled task"),
                    _json(sources),
                    now,
                    now,
                ),
            )
            self._record_state_event(
                conn,
                identity,
                1,
                "task_created",
                {
                    "title": str(title or "Untitled task"),
                    "owner_profile": self.owner_profile,
                },
            )
        return self.get_task(identity)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            self._assert_owner(row)
            slices = conn.execute(
                "SELECT slice_id, ordinal, definition_json, status, lease_owner, "
                "lease_expires_at, result_id, integrated_state_revision "
                "FROM task_slices WHERE task_id = ? ORDER BY ordinal, slice_id",
                (task_id,),
            ).fetchall()
            bindings = conn.execute(
                "SELECT entrypoint, session_id, created_at FROM entrypoint_bindings "
                "WHERE task_id = ? ORDER BY entrypoint, session_id",
                (task_id,),
            ).fetchall()
            source_events = conn.execute(
                "SELECT payload_json FROM source_events WHERE task_id = ? "
                "ORDER BY recorded_at, event_id",
                (task_id,),
            ).fetchall()
            state_events = conn.execute(
                "SELECT state_revision, event_type, payload_json, previous_event_digest, "
                "event_digest, recorded_at FROM task_state_events WHERE task_id = ? "
                "ORDER BY state_revision",
                (task_id,),
            ).fetchall()
        data = dict(row)
        if not self._state_event_ledger_valid(
            task_id, int(data["state_revision"]), state_events
        ):
            raise ValueError("task_state_event_ledger_invalid")
        return {
            "schema": data["schema_name"],
            "task_id": data["task_id"],
            "owner_profile": data["owner_profile"],
            "title": data["title"],
            "status": data["status"],
            "original_sources": _decode(data["original_sources_json"], []),
            "source_events": [_decode(item["payload_json"], {}) for item in source_events],
            "state_events": [
                {
                    "state_revision": int(item["state_revision"]),
                    "event_type": item["event_type"],
                    "payload": _decode(item["payload_json"], {}),
                    "previous_event_digest": item["previous_event_digest"],
                    "event_digest": item["event_digest"],
                    "recorded_at": item["recorded_at"],
                }
                for item in state_events
            ],
            "spec_bundle": _decode(data["spec_bundle_json"], {}),
            "implementation_graph": _decode(data["implementation_graph_json"], []),
            "evidence_graph": _decode(data["evidence_graph_json"], []),
            "closure": _decode(data["closure_json"], {}),
            "spec_revision": int(data["spec_revision"]),
            "plan_revision": int(data["plan_revision"]),
            "state_revision": int(data["state_revision"]),
            "candidate_digest": data["candidate_digest"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "bindings": [dict(item) for item in bindings],
            "slices": [
                {
                    **_decode(item["definition_json"], {}),
                    "status": item["status"],
                    "lease_owner": item["lease_owner"],
                    "lease_expires_at": item["lease_expires_at"],
                    "result_id": item["result_id"],
                    "integrated_state_revision": item["integrated_state_revision"],
                }
                for item in slices
            ],
        }

    def bind_entrypoint(self, task_id: str, *, entrypoint: str, session_id: str) -> dict[str, Any]:
        entrypoint = str(entrypoint or "").strip().lower()
        session_id = str(session_id or "").strip()
        if not entrypoint or not session_id:
            raise ValueError("entrypoint_and_session_id_required")
        with self._write() as conn:
            self._task_row(conn, task_id)
            existing = conn.execute(
                "SELECT task_id FROM entrypoint_bindings WHERE entrypoint = ? AND session_id = ?",
                (entrypoint, session_id),
            ).fetchone()
            if existing is not None:
                if existing["task_id"] != task_id:
                    raise ValueError("entrypoint_already_bound")
            else:
                conn.execute(
                    """
                    INSERT INTO entrypoint_bindings (entrypoint, session_id, task_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (entrypoint, session_id, task_id, _utc_now()),
                )
                self._bump_state(
                    conn,
                    task_id,
                    "entrypoint_bound",
                    {"entrypoint": entrypoint, "session_id": session_id},
                )
        return self.get_task(task_id)

    def resolve_entrypoint(self, entrypoint: str, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_id FROM entrypoint_bindings WHERE entrypoint = ? AND session_id = ?",
                (str(entrypoint or "").strip().lower(), str(session_id or "").strip()),
            ).fetchone()
        return self.get_task(row["task_id"]) if row else None

    def append_source_event(self, task_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        if not payload.get("content"):
            raise ValueError("source_event_content_required")
        with self._write() as conn:
            self._task_row(conn, task_id)
            event_id = str(payload.get("event_id") or _digest(payload))
            existing_row = conn.execute(
                "SELECT payload_json FROM source_events WHERE task_id = ? AND event_id = ?",
                (task_id, event_id),
            ).fetchone()
            if existing_row is not None:
                existing_identity = _decode(existing_row["payload_json"], {})
                existing_identity.pop("recorded_at", None)
                replay_identity = dict(payload)
                replay_identity["event_id"] = event_id
                replay_identity.pop("recorded_at", None)
                if existing_identity != replay_identity:
                    raise ValueError("source_event_id_conflict")
            else:
                payload["event_id"] = event_id
                payload.setdefault("recorded_at", _utc_now())
                conn.execute(
                    "INSERT INTO source_events (task_id, event_id, payload_json, recorded_at) "
                    "VALUES (?, ?, ?, ?)",
                    (task_id, event_id, _json(payload), str(payload["recorded_at"])),
                )
                self._bump_state(
                    conn,
                    task_id,
                    "source_event_appended",
                    {"event_id": event_id},
                )
        return self.get_task(task_id)

    def freeze_spec(
        self,
        task_id: str,
        spec_bundle: Mapping[str, Any],
        *,
        expected_state_revision: int,
    ) -> dict[str, Any]:
        spec = dict(spec_bundle)
        if not spec.get("required_clauses") or not spec.get("closure_criteria"):
            raise ValueError("spec_missing_required_clauses_or_closure_criteria")
        with self._write() as conn:
            row = self._task_row(conn, task_id)
            self._expect_revision(row, expected_state_revision)
            conn.execute(
                """
                UPDATE tasks SET spec_bundle_json = ?, spec_revision = spec_revision + 1,
                    state_revision = state_revision + 1, status = 'specified',
                    candidate_digest = '', evidence_graph_json = '[]', closure_json = '{}',
                    updated_at = ?
                WHERE task_id = ?
                """,
                (_json(spec), _utc_now(), task_id),
            )
            self._invalidate_claims(conn, task_id, "spec_changed")
            self._record_state_event(
                conn,
                task_id,
                int(row["state_revision"]) + 1,
                "spec_frozen",
                {"spec_revision": int(row["spec_revision"]) + 1},
            )
        return self.get_task(task_id)

    def set_plan(
        self,
        task_id: str,
        slices: Sequence[Mapping[str, Any]],
        *,
        expected_state_revision: int,
    ) -> dict[str, Any]:
        definitions = [dict(item) for item in slices]
        self._validate_plan(definitions)
        with self._write() as conn:
            row = self._task_row(conn, task_id)
            self._expect_revision(row, expected_state_revision)
            if int(row["spec_revision"]) < 1:
                raise ValueError("spec_not_frozen")
            self._invalidate_claims(conn, task_id, "plan_changed")
            conn.execute("DELETE FROM task_slices WHERE task_id = ?", (task_id,))
            for ordinal, definition in enumerate(definitions):
                conn.execute(
                    """
                    INSERT INTO task_slices (
                        task_id, slice_id, ordinal, definition_json, status, lease_owner,
                        lease_token, lease_expires_at, result_id, integrated_state_revision
                    ) VALUES (?, ?, ?, ?, 'pending', '', '', NULL, '', NULL)
                    """,
                    (task_id, definition["slice_id"], ordinal, _json(definition)),
                )
            conn.execute(
                """
                UPDATE tasks SET implementation_graph_json = ?, plan_revision = plan_revision + 1,
                    state_revision = state_revision + 1, status = 'planned', candidate_digest = '',
                    evidence_graph_json = '[]', closure_json = '{}', updated_at = ? WHERE task_id = ?
                """,
                (_json(definitions), _utc_now(), task_id),
            )
            conn.execute("UPDATE context_packets SET status = 'invalidated' WHERE task_id = ? AND status = 'active'", (task_id,))
            self._record_state_event(
                conn,
                task_id,
                int(row["state_revision"]) + 1,
                "plan_set",
                {
                    "plan_revision": int(row["plan_revision"]) + 1,
                    "slice_ids": [str(item["slice_id"]) for item in definitions],
                },
            )
        return self.get_task(task_id)

    def claim_next_slice(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        lease_seconds: int = 300,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now_value = float(time.time() if now is None else now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds_must_be_positive")
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            expired_slice_ids = self._expire_leases(conn, task_id, now_value)
            rows = conn.execute(
                "SELECT * FROM task_slices WHERE task_id = ? ORDER BY ordinal, slice_id",
                (task_id,),
            ).fetchall()
            statuses = {row["slice_id"]: row["status"] for row in rows}
            selected = None
            definition: dict[str, Any] = {}
            for row in rows:
                if row["status"] != "pending":
                    continue
                candidate = _decode(row["definition_json"], {})
                dependencies = [str(item) for item in candidate.get("dependencies") or []]
                if all(statuses.get(dep) == "completed" for dep in dependencies):
                    selected = row
                    definition = candidate
                    break
            if selected is None:
                return None
            lease_token = uuid.uuid4().hex
            expires = now_value + int(lease_seconds)
            state_revision = int(task["state_revision"]) + 1
            packet_id = uuid.uuid4().hex
            packet = {
                "schema": CONTEXT_PACKET_SCHEMA,
                "packet_id": packet_id,
                "task_id": task_id,
                "spec_revision": int(task["spec_revision"]),
                "plan_revision": int(task["plan_revision"]),
                "state_revision": state_revision,
                "slice_id": selected["slice_id"],
                "role": definition.get("role", "implementer"),
                "objective": definition.get("objective", ""),
                "required_clauses": list(definition.get("required_clauses") or []),
                "negative_requirements": list(definition.get("negative_requirements") or []),
                "source_refs": list(definition.get("source_refs") or []),
                "architecture_invariants": list(definition.get("architecture_invariants") or []),
                "owned_paths": list(definition.get("owned_paths") or []),
                "workspace_path": str(definition.get("workspace_path") or ""),
                "workspace_base_commit": str(definition.get("workspace_base_commit") or ""),
                "forbidden_changes": list(definition.get("forbidden_changes") or []),
                "dependencies": list(definition.get("dependencies") or []),
                "verification_commands": list(definition.get("verification_commands") or []),
                "evidence_sink": {"task_id": task_id, "slice_id": selected["slice_id"]},
                "authority": dict(definition.get("authority") or {}),
                "lease": {"owner": worker, "token": lease_token, "expires_at": expires},
            }
            packet["packet_digest"] = _digest(packet)
            conn.execute(
                """
                UPDATE task_slices SET status = 'claimed', lease_owner = ?, lease_token = ?,
                    lease_expires_at = ? WHERE task_id = ? AND slice_id = ? AND status = 'pending'
                """,
                (worker, lease_token, expires, task_id, selected["slice_id"]),
            )
            conn.execute(
                """
                INSERT INTO context_packets (
                    packet_id, task_id, slice_id, packet_digest, payload_json,
                    spec_revision, plan_revision, state_revision, lease_owner,
                    lease_token, lease_expires_at, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    packet_id, task_id, selected["slice_id"], packet["packet_digest"],
                    _json(packet), packet["spec_revision"], packet["plan_revision"],
                    packet["state_revision"], worker, lease_token, expires, _utc_now(),
                ),
            )
            conn.execute(
                "UPDATE tasks SET state_revision = ?, status = 'running', updated_at = ? WHERE task_id = ?",
                (state_revision, _utc_now(), task_id),
            )
            self._record_state_event(
                conn,
                task_id,
                state_revision,
                "slice_claimed",
                {
                    "slice_id": selected["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "expired_slice_ids": expired_slice_ids,
                },
            )
            return packet

    def verify_context_packet(self, packet: Mapping[str, Any]) -> bool:
        payload = dict(packet)
        expected = str(payload.pop("packet_digest", ""))
        return bool(expected) and expected == _digest(payload)

    def release_claim(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Abandon one active lease after local worker failure and make it retryable."""
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
        with self._write() as conn:
            self._task_row(conn, task_id)
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ? AND task_id = ?",
                (packet_id, task_id),
            ).fetchone()
            if packet is None:
                raise ValueError("packet_not_found")
            if packet["status"] != "active":
                raise ValueError("packet_not_active")
            if packet["lease_owner"] != worker:
                raise ValueError("lease_owner_mismatch")
            updated = conn.execute(
                """
                UPDATE task_slices
                SET status = 'pending', lease_owner = '', lease_token = '',
                    lease_expires_at = NULL, result_id = ''
                WHERE task_id = ? AND slice_id = ? AND status = 'claimed'
                    AND lease_owner = ? AND lease_token = ?
                """,
                (task_id, packet["slice_id"], worker, packet["lease_token"]),
            )
            if updated.rowcount != 1:
                raise ValueError("lease_fence_mismatch")
            conn.execute(
                "UPDATE context_packets SET status = 'abandoned' WHERE packet_id = ?",
                (packet_id,),
            )
            self._bump_state(
                conn,
                task_id,
                "slice_claim_released",
                {
                    "slice_id": packet["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "reason": str(reason or "worker_failed"),
                },
            )
        return {
            "task_id": task_id,
            "packet_id": packet_id,
            "worker_session_id": worker,
            "status": "abandoned",
            "reason": str(reason or "worker_failed"),
        }

    def quarantine_claim(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Fence an uncertain timed-out worker without making its workspace retryable."""
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
        with self._write() as conn:
            self._task_row(conn, task_id)
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ? AND task_id = ?",
                (packet_id, task_id),
            ).fetchone()
            if packet is None:
                raise ValueError("packet_not_found")
            if packet["status"] != "active":
                raise ValueError("packet_not_active")
            if packet["lease_owner"] != worker:
                raise ValueError("lease_owner_mismatch")
            updated = conn.execute(
                """
                UPDATE task_slices
                SET status = 'quarantined'
                WHERE task_id = ? AND slice_id = ? AND status = 'claimed'
                    AND lease_owner = ? AND lease_token = ?
                """,
                (task_id, packet["slice_id"], worker, packet["lease_token"]),
            )
            if updated.rowcount != 1:
                raise ValueError("lease_fence_mismatch")
            conn.execute(
                "UPDATE context_packets SET status = 'quarantined' WHERE packet_id = ?",
                (packet_id,),
            )
            self._bump_state(
                conn,
                task_id,
                "slice_claim_quarantined",
                {
                    "slice_id": packet["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "reason": str(reason or "worker_termination_unproven"),
                },
            )
        return {
            "task_id": task_id,
            "packet_id": packet_id,
            "worker_session_id": worker,
            "status": "quarantined",
            "reason": str(reason or "worker_termination_unproven"),
        }

    def submit_result(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        result: Mapping[str, Any],
        now: float | None = None,
    ) -> dict[str, Any]:
        payload = dict(result)
        worker = str(worker_session_id or "")
        now_value = float(time.time() if now is None else now)
        identity = _result_id(packet_id, worker, payload)
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            existing = conn.execute("SELECT * FROM worker_results WHERE result_id = ?", (identity,)).fetchone()
            if existing:
                return self._result_payload(existing, idempotent_replay=True)
            packet = conn.execute("SELECT * FROM context_packets WHERE packet_id = ?", (packet_id,)).fetchone()
            reasons: list[str] = []
            if packet is None:
                reasons.append("packet_not_found")
                slice_id = str(payload.get("slice_id") or "")
                packet_payload: dict[str, Any] = {}
            else:
                slice_id = packet["slice_id"]
                packet_payload = _decode(packet["payload_json"], {})
                prior_for_packet = conn.execute(
                    """
                    SELECT result_id FROM worker_results
                    WHERE task_id = ? AND packet_id = ?
                        AND disposition IN ('accepted_for_integration', 'integrated')
                    LIMIT 1
                    """,
                    (task_id, packet_id),
                ).fetchone()
                if prior_for_packet is not None and prior_for_packet["result_id"] != identity:
                    reasons.append("conflicting_result_for_packet")
                if packet["task_id"] != task_id:
                    reasons.append("packet_task_mismatch")
                if int(packet["spec_revision"]) != int(task["spec_revision"]):
                    reasons.append("stale_spec_revision")
                if int(packet["plan_revision"]) != int(task["plan_revision"]):
                    reasons.append("stale_plan_revision")
                if packet["status"] == "invalidated" and not any(reason.startswith("stale_") for reason in reasons):
                    reasons.append("packet_invalidated")
                if packet["lease_owner"] != worker:
                    reasons.append("lease_owner_mismatch")
                if now_value > float(packet["lease_expires_at"]):
                    reasons.append("lease_expired")
                if payload.get("input_digest") != packet["packet_digest"]:
                    reasons.append("input_digest_mismatch")
                if payload.get("slice_id") != slice_id:
                    reasons.append("slice_id_mismatch")
                if not self.verify_context_packet(packet_payload):
                    reasons.append("stored_packet_digest_invalid")
                owned = {str(path) for path in packet_payload.get("owned_paths") or []}
                forbidden = {str(path) for path in packet_payload.get("forbidden_changes") or []}
                changed = {str(path) for path in payload.get("changed_artifacts") or []}
                if not changed.issubset(owned):
                    reasons.append("changed_artifact_outside_owned_paths")
                if changed & forbidden:
                    reasons.append("forbidden_change_reported")
                required_fields = {
                    "claims", "changed_artifacts", "evidence_refs", "verification_results",
                    "risks", "unknowns", "state_delta_proposal", "candidate_digest",
                }
                if any(field not in payload for field in required_fields):
                    reasons.append("worker_result_missing_required_fields")
                else:
                    list_fields = (
                        "claims",
                        "changed_artifacts",
                        "evidence_refs",
                        "verification_results",
                        "risks",
                        "unknowns",
                    )
                    if any(not isinstance(payload.get(field), list) for field in list_fields):
                        reasons.append("worker_result_invalid_field_types")
                    else:
                        claims = {str(item) for item in payload["claims"] if str(item)}
                        assigned_claims = {
                            str(item)
                            for field in ("required_clauses", "negative_requirements")
                            for item in (packet_payload.get(field) or [])
                        }
                        if not assigned_claims.issubset(claims):
                            reasons.append("worker_claims_incomplete")
                        evidence_refs = payload["evidence_refs"]
                        if not evidence_refs or any(
                            not isinstance(item, Mapping)
                            or not str(item.get("ref") or "").strip()
                            for item in evidence_refs
                        ):
                            reasons.append("worker_evidence_refs_invalid")
                        verification_results = payload["verification_results"]
                        expected_commands = {
                            str(item) for item in packet_payload.get("verification_commands") or []
                        }
                        observed_commands = {
                            str(item.get("command") or "")
                            for item in verification_results
                            if isinstance(item, Mapping)
                        }
                        if (
                            not verification_results
                            or any(not isinstance(item, Mapping) for item in verification_results)
                            or not expected_commands.issubset(observed_commands)
                        ):
                            reasons.append("worker_verification_incomplete")
                        elif any(
                            type(item.get("exit_code")) is not int
                            or int(item["exit_code"]) != 0
                            for item in verification_results
                        ):
                            reasons.append("worker_verification_failed")
                        if payload.get("state_delta_proposal") != {"slice_status": "completed"}:
                            reasons.append("worker_state_delta_invalid")
                        if not str(payload.get("candidate_digest") or "").strip():
                            reasons.append("candidate_digest_required")
            disposition = "rejected" if reasons else "accepted_for_integration"
            now_iso = _utc_now()
            conn.execute(
                """
                INSERT INTO worker_results (
                    result_id, task_id, slice_id, packet_id, worker_session_id,
                    payload_digest, payload_json, disposition, reasons_json,
                    integrator_session_id, integrated_state_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?)
                """,
                (
                    identity, task_id, slice_id, packet_id, worker, _digest(payload),
                    _json(payload), disposition, _json(reasons), now_iso, now_iso,
                ),
            )
            if not reasons and packet is not None:
                conn.execute("UPDATE context_packets SET status = 'submitted' WHERE packet_id = ?", (packet_id,))
                conn.execute(
                    "UPDATE task_slices SET status = 'submitted', result_id = ? WHERE task_id = ? AND slice_id = ?",
                    (identity, task_id, slice_id),
                )
            self._bump_state(
                conn,
                task_id,
                "worker_result_submitted",
                {
                    "result_id": identity,
                    "packet_id": packet_id,
                    "slice_id": slice_id,
                    "disposition": disposition,
                    "reasons": reasons,
                },
            )
            row = conn.execute("SELECT * FROM worker_results WHERE result_id = ?", (identity,)).fetchone()
            return self._result_payload(row, idempotent_replay=False)

    def integrate_result(self, task_id: str, result_id: str, *, integrator_session_id: str) -> dict[str, Any]:
        integrator = str(integrator_session_id or "").strip()
        if not integrator:
            raise ValueError("integrator_session_id_required")
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            row = conn.execute("SELECT * FROM worker_results WHERE result_id = ? AND task_id = ?", (result_id, task_id)).fetchone()
            if row is None:
                raise KeyError(result_id)
            if row["disposition"] == "integrated":
                slice_row = conn.execute("SELECT * FROM task_slices WHERE task_id = ? AND slice_id = ?", (task_id, row["slice_id"])).fetchone()
                return {"task_id": task_id, "result_id": result_id, "slice_id": row["slice_id"], "slice_status": slice_row["status"], "idempotent_replay": True}
            if row["disposition"] != "accepted_for_integration":
                raise ValueError("result_not_accepted_for_integration")
            payload = _decode(row["payload_json"], {})
            slice_row = conn.execute("SELECT * FROM task_slices WHERE task_id = ? AND slice_id = ?", (task_id, row["slice_id"])).fetchone()
            definition = _decode(slice_row["definition_json"], {})
            dependencies = list(definition.get("dependencies") or [])
            for dependency in dependencies:
                dep = conn.execute("SELECT status FROM task_slices WHERE task_id = ? AND slice_id = ?", (task_id, dependency)).fetchone()
                if dep is None or dep["status"] != "completed":
                    raise ValueError("dependency_not_completed")
            packet = conn.execute("SELECT state_revision FROM context_packets WHERE packet_id = ?", (row["packet_id"],)).fetchone()
            changed = {str(path) for path in payload.get("changed_artifacts") or []}
            prior_rows = conn.execute(
                "SELECT payload_json FROM worker_results WHERE task_id = ? AND disposition = 'integrated' "
                "AND integrated_state_revision > ?",
                (task_id, int(packet["state_revision"])),
            ).fetchall()
            prior_changed = {
                str(path)
                for prior in prior_rows
                for path in (_decode(prior["payload_json"], {}).get("changed_artifacts") or [])
            }
            if changed & prior_changed:
                raise ValueError("write_set_conflict_since_packet")
            candidate = str(payload.get("candidate_digest") or "")
            if not candidate:
                raise ValueError("candidate_digest_required")
            new_revision = int(task["state_revision"]) + 1
            evidence = _decode(task["evidence_graph_json"], [])
            evidence.append(
                {
                    "result_id": result_id,
                    "slice_id": row["slice_id"],
                    "claims": list(payload.get("claims") or []),
                    "evidence_refs": list(payload.get("evidence_refs") or []),
                    "verification_results": list(payload.get("verification_results") or []),
                    "candidate_digest": candidate,
                    "integrated_state_revision": new_revision,
                }
            )
            now = _utc_now()
            conn.execute(
                "UPDATE worker_results SET disposition = 'integrated', integrator_session_id = ?, "
                "integrated_state_revision = ?, updated_at = ? WHERE result_id = ?",
                (integrator, new_revision, now, result_id),
            )
            conn.execute(
                "UPDATE task_slices SET status = 'completed', integrated_state_revision = ? "
                "WHERE task_id = ? AND slice_id = ?",
                (new_revision, task_id, row["slice_id"]),
            )
            conn.execute(
                "UPDATE tasks SET evidence_graph_json = ?, candidate_digest = ?, state_revision = ?, "
                "status = 'running', updated_at = ? WHERE task_id = ?",
                (_json(evidence), candidate, new_revision, now, task_id),
            )
            self._record_state_event(
                conn,
                task_id,
                new_revision,
                "worker_result_integrated",
                {
                    "result_id": result_id,
                    "slice_id": row["slice_id"],
                    "candidate_digest": candidate,
                    "integrator_session_id": integrator,
                },
            )
            return {"task_id": task_id, "result_id": result_id, "slice_id": row["slice_id"], "slice_status": "completed", "state_revision": new_revision, "candidate_digest": candidate, "idempotent_replay": False}

    def close_task(
        self,
        task_id: str,
        *,
        candidate_digest: str,
        verifier_session_id: str,
        claims: Mapping[str, str],
    ) -> dict[str, Any]:
        verifier = str(verifier_session_id or "").strip()
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            if str(candidate_digest) != str(task["candidate_digest"]):
                raise ValueError("candidate_digest_mismatch")
            incomplete = conn.execute(
                "SELECT slice_id FROM task_slices WHERE task_id = ? AND status != 'completed'",
                (task_id,),
            ).fetchall()
            if incomplete:
                raise ValueError("task_slices_incomplete")
            participants = conn.execute(
                "SELECT worker_session_id, integrator_session_id FROM worker_results WHERE task_id = ? AND disposition = 'integrated'",
                (task_id,),
            ).fetchall()
            participant_ids = {
                value
                for row in participants
                for value in (row["worker_session_id"], row["integrator_session_id"])
                if value
            }
            if not verifier or verifier in participant_ids:
                raise ValueError("closure_verifier_not_independent")
            spec = _decode(task["spec_bundle_json"], {})
            required = {str(item) for item in spec.get("closure_criteria") or []}
            passed = {str(key) for key, value in claims.items() if str(value).lower() == "pass"}
            if not required.issubset(passed):
                raise ValueError("closure_claims_incomplete")
            closure = {
                "schema": "hermes_closure_receipt_v1",
                "task_id": task_id,
                "candidate_digest": candidate_digest,
                "verifier_session_id": verifier,
                "claims": dict(claims),
                "closed_at": _utc_now(),
            }
            conn.execute(
                "UPDATE tasks SET status = 'closed', closure_json = ?, state_revision = state_revision + 1, updated_at = ? WHERE task_id = ?",
                (_json(closure), _utc_now(), task_id),
            )
            self._record_state_event(
                conn,
                task_id,
                int(task["state_revision"]) + 1,
                "task_closed",
                {"candidate_digest": candidate_digest, "verifier_session_id": verifier},
            )
        return self.get_task(task_id)

    @staticmethod
    def _state_event_ledger_valid(
        task_id: str,
        current_revision: int,
        rows: Sequence[sqlite3.Row],
    ) -> bool:
        if not rows or int(rows[-1]["state_revision"]) != int(current_revision):
            return False
        first_revision = int(rows[0]["state_revision"])
        if [int(row["state_revision"]) for row in rows] != list(
            range(first_revision, int(current_revision) + 1)
        ):
            return False
        previous_digest = ""
        for row in rows:
            if str(row["previous_event_digest"]) != previous_digest:
                return False
            identity = {
                "task_id": task_id,
                "state_revision": int(row["state_revision"]),
                "event_type": str(row["event_type"]),
                "payload": _decode(row["payload_json"], {}),
                "previous_event_digest": previous_digest,
                "recorded_at": str(row["recorded_at"]),
            }
            if _digest(identity) != str(row["event_digest"]):
                return False
            previous_digest = str(row["event_digest"])
        return True

    @staticmethod
    def _record_state_event(
        conn: sqlite3.Connection,
        task_id: str,
        state_revision: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        previous = conn.execute(
            "SELECT event_digest FROM task_state_events WHERE task_id = ? "
            "ORDER BY state_revision DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        previous_digest = str(previous["event_digest"] if previous else "")
        recorded_at = _utc_now()
        event_payload = dict(payload)
        identity = {
            "task_id": task_id,
            "state_revision": int(state_revision),
            "event_type": str(event_type),
            "payload": event_payload,
            "previous_event_digest": previous_digest,
            "recorded_at": recorded_at,
        }
        event_digest = _digest(identity)
        conn.execute(
            "INSERT INTO task_state_events "
            "(task_id, state_revision, event_type, payload_json, previous_event_digest, "
            "event_digest, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                int(state_revision),
                str(event_type),
                _json(event_payload),
                previous_digest,
                event_digest,
                recorded_at,
            ),
        )

    def _assert_owner(self, row: sqlite3.Row) -> None:
        if str(row["owner_profile"]) != self.owner_profile:
            raise PermissionError("task_owner_profile_mismatch")

    def _task_row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        self._assert_owner(row)
        return row

    @staticmethod
    def _expect_revision(row: sqlite3.Row, expected: int) -> None:
        actual = int(row["state_revision"])
        if actual != int(expected):
            raise RevisionConflict(f"state_revision_conflict expected={expected} actual={actual}")

    @staticmethod
    def _bump_state(
        conn: sqlite3.Connection,
        task_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> int:
        row = conn.execute(
            "SELECT state_revision FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        revision = int(row["state_revision"]) + 1
        conn.execute(
            "UPDATE tasks SET state_revision = ?, updated_at = ? WHERE task_id = ?",
            (revision, _utc_now(), task_id),
        )
        TaskRuntimeStore._record_state_event(
            conn, task_id, revision, event_type, payload
        )
        return revision

    @staticmethod
    def _invalidate_claims(conn: sqlite3.Connection, task_id: str, reason: str) -> None:
        invalidation_reason = f"invalidated_by_{str(reason or 'revision_change')}"
        conn.execute(
            "UPDATE context_packets SET status = 'invalidated' "
            "WHERE task_id = ? AND status IN ('active', 'submitted')",
            (task_id,),
        )
        conn.execute(
            "UPDATE worker_results SET disposition = 'invalidated', reasons_json = ?, updated_at = ? "
            "WHERE task_id = ? AND disposition IN ('accepted_for_integration', 'integrated')",
            (_json([invalidation_reason]), _utc_now(), task_id),
        )
        conn.execute(
            "UPDATE task_slices SET status = 'pending', lease_owner = '', lease_token = '', "
            "lease_expires_at = NULL, result_id = '', integrated_state_revision = NULL "
            "WHERE task_id = ? AND status IN ('claimed', 'submitted', 'completed')",
            (task_id,),
        )

    @staticmethod
    def _expire_leases(conn: sqlite3.Connection, task_id: str, now: float) -> list[str]:
        expired = conn.execute(
            "SELECT slice_id FROM task_slices WHERE task_id = ? AND status = 'claimed' AND lease_expires_at < ?",
            (task_id, now),
        ).fetchall()
        for row in expired:
            conn.execute(
                "UPDATE task_slices SET status = 'pending', lease_owner = '', lease_token = '', lease_expires_at = NULL "
                "WHERE task_id = ? AND slice_id = ?",
                (task_id, row["slice_id"]),
            )
            conn.execute(
                "UPDATE context_packets SET status = 'expired' WHERE task_id = ? AND slice_id = ? AND status = 'active'",
                (task_id, row["slice_id"]),
            )
        return [str(row["slice_id"]) for row in expired]

    @staticmethod
    def _validate_plan(definitions: Sequence[Mapping[str, Any]]) -> None:
        if not definitions:
            raise ValueError("implementation_graph_required")
        ids = [str(item.get("slice_id") or "") for item in definitions]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("slice_ids_must_be_unique_and_nonempty")
        known = set(ids)
        for item in definitions:
            dependencies = {str(dep) for dep in item.get("dependencies") or []}
            if not dependencies.issubset(known):
                raise ValueError("slice_dependency_missing")
            if str(item["slice_id"]) in dependencies:
                raise ValueError("slice_self_dependency")
            required = {
                "objective", "required_clauses", "negative_requirements", "source_refs",
                "owned_paths", "forbidden_changes", "verification_commands", "authority",
            }
            if any(field not in item for field in required):
                raise ValueError("slice_definition_missing_required_fields")
        visited: set[str] = set()
        visiting: set[str] = set()
        by_id = {str(item["slice_id"]): item for item in definitions}

        def visit(slice_id: str) -> None:
            if slice_id in visiting:
                raise ValueError("implementation_graph_cycle")
            if slice_id in visited:
                return
            visiting.add(slice_id)
            for dependency in by_id[slice_id].get("dependencies") or []:
                visit(str(dependency))
            visiting.remove(slice_id)
            visited.add(slice_id)

        for slice_id in ids:
            visit(slice_id)

    @staticmethod
    def _result_payload(row: sqlite3.Row, *, idempotent_replay: bool) -> dict[str, Any]:
        return {
            "schema": WORKER_RESULT_SCHEMA,
            "result_id": row["result_id"],
            "task_id": row["task_id"],
            "slice_id": row["slice_id"],
            "packet_id": row["packet_id"],
            "worker_session_id": row["worker_session_id"],
            "payload_digest": row["payload_digest"],
            "disposition": row["disposition"],
            "reasons": _decode(row["reasons_json"], []),
            "idempotent_replay": idempotent_replay,
        }
