"""Shared norm governance primitives for Hermes multi-session coordination.

This module provides the production substrate for the rule:
"sessions use snapshots; durable norms live in a versioned source of truth".

It intentionally has no provider/model dependencies.  The store is SQLite-backed so
multiple CLI/gateway/cron processes can coordinate through the same profile-scoped
state file, while tests can use a tmp_path database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hermes_constants import get_hermes_home


SCHEMA_VERSION = 1
NORM_SNAPSHOT_SCHEMA = "hermes_norm_snapshot_v1"
FUSION_RECORD_SCHEMA = "hermes_task_fusion_record_v1"


class NormGovernanceError(RuntimeError):
    """Base class for norm governance failures."""


class NormConflict(NormGovernanceError):
    """Raised when compare-and-swap rejects a stale norm update."""

    def __init__(self, key: str, expected_revision: int | None, current_revision: int):
        self.key = key
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"stale norm write for {key}: expected revision {expected_revision}, "
            f"current revision {current_revision}"
        )


class LeaseConflict(NormGovernanceError):
    """Raised when another live session owns a write lease."""

    def __init__(self, resource_key: str, active_owner_session_id: str, expires_at: float):
        self.resource_key = resource_key
        self.active_owner_session_id = active_owner_session_id
        self.expires_at = expires_at
        super().__init__(
            f"resource {resource_key} is leased by {active_owner_session_id} "
            f"until {expires_at:.3f}"
        )


@dataclass(frozen=True)
class NormRecord:
    key: str
    content: str
    scope: str
    source: str
    revision: int
    content_hash: str
    updated_at: float


@dataclass(frozen=True)
class LeaseRecord:
    resource_key: str
    owner_session_id: str
    task_id: str
    base_hash: str
    intent: str
    write_mode: str
    expires_at: float
    heartbeat_at: float


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_json_dumps(value))


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def normalize_resource_key(kind: str, identifier: str, fragment: str | None = None) -> str:
    """Return the canonical resource identity used by leases/write sets.

    The normalizer is intentionally conservative: it trims whitespace and uses a
    simple `kind:identifier#fragment` form so callers can represent files, skills,
    memories, config sections, kanban rows, brain sections, and future resource
    classes without schema churn.
    """

    clean_kind = str(kind or "").strip().lower().replace(" ", "_")
    clean_identifier = str(identifier or "").strip()
    if not clean_kind or not clean_identifier:
        raise ValueError("resource kind and identifier are required")
    key = f"{clean_kind}:{clean_identifier}"
    if fragment:
        key = f"{key}#{str(fragment).strip()}"
    return key


class NormGovernanceStore:
    """SQLite-backed SSOT for norms, session snapshots, leases, and fusion records."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = get_hermes_home() / "state" / "norm_governance.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');

                CREATE TABLE IF NOT EXISTS norms (
                    key TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'global',
                    source TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS norm_events (
                    event_id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    old_revision INTEGER,
                    new_revision INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS leases (
                    resource_key TEXT PRIMARY KEY,
                    owner_session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL DEFAULT '',
                    base_hash TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL DEFAULT '',
                    write_mode TEXT NOT NULL DEFAULT 'patch',
                    expires_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fusion_records (
                    fusion_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    @staticmethod
    def _row_to_norm(row: sqlite3.Row) -> NormRecord:
        return NormRecord(
            key=row["key"],
            content=row["content"],
            scope=row["scope"],
            source=row["source"],
            revision=int(row["revision"]),
            content_hash=row["content_hash"],
            updated_at=float(row["updated_at"]),
        )

    def get_norm(self, key: str) -> NormRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM norms WHERE key = ?", (key,)).fetchone()
            return self._row_to_norm(row) if row else None

    def upsert_norm(
        self,
        key: str,
        content: str,
        *,
        scope: str = "global",
        source: str = "",
        expected_revision: int | None = None,
        now: float | None = None,
    ) -> NormRecord:
        """Create/update a norm using revision CAS.

        `expected_revision=0` means create-only.  Any other integer must match
        the current row revision.  `None` permits an unconditional writer for
        trusted bootstrap/migration callers, but normal tools should pass an
        expected revision from their NormSnapshot.
        """

        ts = time.time() if now is None else float(now)
        content_hash = _sha256_text(content)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM norms WHERE key = ?", (key,)).fetchone()
            current_revision = int(row["revision"]) if row else 0
            if expected_revision is not None and expected_revision != current_revision:
                conn.execute("ROLLBACK")
                raise NormConflict(key, expected_revision, current_revision)
            new_revision = current_revision + 1
            conn.execute(
                """
                INSERT INTO norms(key, content, scope, source, revision, content_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    content=excluded.content,
                    scope=excluded.scope,
                    source=excluded.source,
                    revision=excluded.revision,
                    content_hash=excluded.content_hash,
                    updated_at=excluded.updated_at
                """,
                (key, content, scope or "global", source or "", new_revision, content_hash, ts),
            )
            conn.execute(
                """
                INSERT INTO norm_events(event_id, key, old_revision, new_revision, content_hash, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), key, current_revision or None, new_revision, content_hash, source or "", ts),
            )
            conn.execute("COMMIT")
        return self.get_norm(key)  # type: ignore[return-value]

    def build_snapshot(self, *, scopes: Sequence[str] | None = None) -> dict[str, Any]:
        """Build the effective versioned norm snapshot for a session/task."""

        params: list[Any] = []
        where = ""
        if scopes:
            where = "WHERE scope IN (%s)" % ",".join("?" for _ in scopes)
            params.extend(scopes)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM norms {where} ORDER BY key", params).fetchall()
        norms = {row["key"]: {
            "scope": row["scope"],
            "source": row["source"],
            "revision": int(row["revision"]),
            "content_hash": row["content_hash"],
            "updated_at": float(row["updated_at"]),
        } for row in rows}
        revision_vector = {key: meta["revision"] for key, meta in norms.items()}
        registry_revision = max(revision_vector.values(), default=0)
        snapshot = {
            "schema": NORM_SNAPSHOT_SCHEMA,
            "registry_revision": registry_revision,
            "generated_at": time.time(),
            "scopes": list(scopes or []),
            "norms": norms,
            "revision_vector": revision_vector,
        }
        snapshot["snapshot_hash"] = _sha256_json({
            "schema": snapshot["schema"],
            "registry_revision": snapshot["registry_revision"],
            "scopes": snapshot["scopes"],
            "norms": snapshot["norms"],
        })
        return snapshot

    def detect_stale_snapshot(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        revision_vector = snapshot.get("revision_vector") or {}
        if not isinstance(revision_vector, Mapping):
            return [{"key": "<snapshot>", "reason": "missing_revision_vector"}]
        stale: list[dict[str, Any]] = []
        for key, snap_rev_raw in revision_vector.items():
            current = self.get_norm(str(key))
            snap_rev = int(snap_rev_raw)
            if current is None:
                stale.append({
                    "key": str(key),
                    "snapshot_revision": snap_rev,
                    "current_revision": None,
                    "reason": "deleted_or_missing",
                })
            elif current.revision != snap_rev:
                stale.append({
                    "key": str(key),
                    "snapshot_revision": snap_rev,
                    "current_revision": current.revision,
                    "reason": "revision_changed",
                })
        return stale

    def acquire_lease(
        self,
        *,
        resource_key: str,
        owner_session_id: str,
        task_id: str = "",
        ttl_seconds: int = 300,
        base_hash: str = "",
        intent: str = "",
        write_mode: str = "patch",
        now: float | None = None,
    ) -> LeaseRecord:
        ts = time.time() if now is None else float(now)
        expires_at = ts + max(1, int(ttl_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM leases WHERE resource_key = ?", (resource_key,)).fetchone()
            if row and float(row["expires_at"]) > ts and row["owner_session_id"] != owner_session_id:
                conn.execute("ROLLBACK")
                raise LeaseConflict(resource_key, row["owner_session_id"], float(row["expires_at"]))
            conn.execute(
                """
                INSERT INTO leases(resource_key, owner_session_id, task_id, base_hash, intent, write_mode, expires_at, heartbeat_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_key) DO UPDATE SET
                    owner_session_id=excluded.owner_session_id,
                    task_id=excluded.task_id,
                    base_hash=excluded.base_hash,
                    intent=excluded.intent,
                    write_mode=excluded.write_mode,
                    expires_at=excluded.expires_at,
                    heartbeat_at=excluded.heartbeat_at
                """,
                (resource_key, owner_session_id, task_id or "", base_hash or "", intent or "", write_mode or "patch", expires_at, ts),
            )
            conn.execute("COMMIT")
        return LeaseRecord(resource_key, owner_session_id, task_id or "", base_hash or "", intent or "", write_mode or "patch", expires_at, ts)

    def release_lease(self, resource_key: str, owner_session_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM leases WHERE resource_key = ? AND owner_session_id = ?",
                (resource_key, owner_session_id),
            )
            return cur.rowcount > 0

    def active_leases(self, *, now: float | None = None) -> list[dict[str, Any]]:
        ts = time.time() if now is None else float(now)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM leases WHERE expires_at > ? ORDER BY resource_key", (ts,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def detect_write_set_conflicts(write_sets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        by_resource: dict[str, list[tuple[str, str]]] = {}
        for ws in write_sets:
            session_id = str(ws.get("session_id") or "")
            task_id = str(ws.get("task_id") or "")
            for resource in ws.get("resources") or []:
                by_resource.setdefault(str(resource), []).append((session_id, task_id))
        conflicts: list[dict[str, Any]] = []
        for resource, owners in sorted(by_resource.items()):
            sessions = _dedupe_preserve_order(session for session, _ in owners if session)
            task_ids = _dedupe_preserve_order(task for _, task in owners if task)
            if len(sessions) > 1 or len(task_ids) > 1:
                conflicts.append({
                    "resource_key": resource,
                    "sessions": sessions,
                    "task_ids": task_ids,
                    "reason": "overlapping_write_set",
                })
        return conflicts

    def create_fusion_record(
        self,
        *,
        parent_task_ids: Sequence[str],
        write_sets: Sequence[Mapping[str, Any]],
        norm_revisions: Mapping[str, int],
        evidence_refs: Sequence[Mapping[str, Any]],
        acceptance_criteria: Sequence[str],
        active_writer: str,
        status: str = "open",
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)
        fusion_id = f"fusion-{uuid.uuid4()}"
        payload = {
            "schema": FUSION_RECORD_SCHEMA,
            "fusion_id": fusion_id,
            "status": status,
            "parent_task_ids": list(parent_task_ids),
            "write_sets": [dict(ws) for ws in write_sets],
            "norm_revisions": dict(norm_revisions),
            "evidence_refs": [dict(ref) for ref in evidence_refs],
            "acceptance_criteria": _dedupe_preserve_order(str(item) for item in acceptance_criteria),
            "active_writer": active_writer,
            "conflicts": self.detect_write_set_conflicts(write_sets),
            "created_at": ts,
            "updated_at": ts,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fusion_records(fusion_id, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fusion_id, _json_dumps(payload), status, ts, ts),
            )
        return payload

    def get_fusion_record(self, fusion_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM fusion_records WHERE fusion_id = ?", (fusion_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None
