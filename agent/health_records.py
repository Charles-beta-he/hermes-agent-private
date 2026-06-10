"""Unified health-record persistence for Hermes runtime chains.

Records live in ``state.db`` so skills, connectors, browser automation, cron,
and selfcheck/API consumers can query the same compact projection instead of
scraping separate logs.  The helpers are deliberately small and best-effort:
runtime hot paths may call them without risking user-facing failures.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home
from hermes_state import apply_wal_with_fallback

SCHEMA = "hermes_health_records_v1"
STALE_HALF_LIFE_SECONDS = 24 * 60 * 60
_INITIAL_SCORE = 100.0
_FAILURE_PENALTY = 30.0
_SUCCESS_RECOVERY_FACTOR = 0.20
_LOCK = threading.Lock()

HEALTH_RECORDS_SQL = """
CREATE TABLE IF NOT EXISTS health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_label TEXT,
    status TEXT NOT NULL,
    health_score REAL NOT NULL DEFAULT 100.0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    observation_count INTEGER NOT NULL DEFAULT 0,
    failure_rate REAL NOT NULL DEFAULT 0.0,
    last_observed_at REAL NOT NULL,
    last_success_at REAL,
    last_failure_at REAL,
    last_error TEXT,
    details_json TEXT,
    updated_at REAL NOT NULL,
    UNIQUE(chain_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_health_records_chain
    ON health_records(chain_type, target_id);
CREATE INDEX IF NOT EXISTS idx_health_records_status
    ON health_records(status, updated_at DESC);
"""


def _db_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else get_hermes_home() / "state.db"


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    db = _db_path(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10.0)
    conn.row_factory = sqlite3.Row
    apply_wal_with_fallback(conn, db_label="state.db")
    return conn


def ensure_health_schema(db_path: str | Path | None = None) -> None:
    """Create the health_records table/indexes in state.db if missing."""
    with _connect(db_path) as conn:
        conn.executescript(HEALTH_RECORDS_SQL)
        conn.commit()


def _decay_score(score: float, last_observed_at: float, now: float) -> float:
    age = max(0.0, float(now) - float(last_observed_at or now))
    return max(0.0, min(100.0, float(score) * math.pow(0.5, age / STALE_HALF_LIFE_SECONDS)))


def _normalize_status(status: str, success: bool) -> str:
    raw = (status or "").strip().lower()
    if raw in {"success", "ok", "healthy"}:
        return "success"
    if raw in {"degraded", "warning", "warn", "error", "failed", "failure"}:
        return "degraded"
    return "success" if success else "degraded"


def _row_to_record(row: sqlite3.Row, *, now: float | None = None) -> dict[str, Any]:
    ts = time.time() if now is None else float(now)
    details = None
    try:
        details = json.loads(row["details_json"]) if row["details_json"] else None
    except Exception:
        details = None
    health_score = float(row["health_score"] or 0.0)
    effective = _decay_score(health_score, float(row["last_observed_at"] or ts), ts)
    record = {
        "schema": SCHEMA,
        "id": int(row["id"]),
        "chain_type": row["chain_type"],
        "target_id": row["target_id"],
        "target_label": row["target_label"],
        "status": row["status"],
        "health_score": round(health_score, 4),
        "effective_health_score": round(effective, 4),
        "failure_count": int(row["failure_count"] or 0),
        "observation_count": int(row["observation_count"] or 0),
        "failure_rate": round(float(row["failure_rate"] or 0.0), 4),
        "last_observed_at": float(row["last_observed_at"] or 0.0),
        "last_success_at": row["last_success_at"],
        "last_failure_at": row["last_failure_at"],
        "last_error": row["last_error"],
        "details": details,
        "updated_at": float(row["updated_at"] or 0.0),
    }
    return record


def record_health_observation(
    *,
    chain_type: str,
    target_id: str,
    status: str = "success",
    success: bool = True,
    target_label: str | None = None,
    error_message: str | None = None,
    details: Mapping[str, Any] | None = None,
    observed_at: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Upsert one health observation and return the latest structured record."""
    chain = str(chain_type or "unknown").strip() or "unknown"
    target = str(target_id or "unknown").strip() or "unknown"
    now = time.time() if observed_at is None else float(observed_at)
    norm_status = _normalize_status(status, success)
    safe_error = str(error_message or "")[:1000] or None
    details_json = json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True) if details else None

    with _LOCK:
        with _connect(db_path) as conn:
            conn.executescript(HEALTH_RECORDS_SQL)
            row = conn.execute(
                "SELECT * FROM health_records WHERE chain_type = ? AND target_id = ?",
                (chain, target),
            ).fetchone()
            if row is None:
                observations = 1
                failures = 0 if success else 1
                previous = _INITIAL_SCORE
                decayed = previous
                score = min(100.0, decayed + (100.0 - decayed) * _SUCCESS_RECOVERY_FACTOR) if success else max(0.0, decayed - _FAILURE_PENALTY)
                conn.execute(
                    """
                    INSERT INTO health_records (
                        chain_type, target_id, target_label, status, health_score,
                        failure_count, observation_count, failure_rate,
                        last_observed_at, last_success_at, last_failure_at,
                        last_error, details_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chain, target, target_label, norm_status, score,
                        failures, observations, failures / observations,
                        now, now if success else None, None if success else now,
                        safe_error, details_json, now,
                    ),
                )
            else:
                observations = int(row["observation_count"] or 0) + 1
                failures = int(row["failure_count"] or 0) + (0 if success else 1)
                decayed = _decay_score(float(row["health_score"] or _INITIAL_SCORE), float(row["last_observed_at"] or now), now)
                score = min(100.0, decayed + (100.0 - decayed) * _SUCCESS_RECOVERY_FACTOR) if success else max(0.0, decayed - _FAILURE_PENALTY)
                conn.execute(
                    """
                    UPDATE health_records
                       SET target_label = COALESCE(?, target_label),
                           status = ?,
                           health_score = ?,
                           failure_count = ?,
                           observation_count = ?,
                           failure_rate = ?,
                           last_observed_at = ?,
                           last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                           last_failure_at = CASE WHEN ? THEN last_failure_at ELSE ? END,
                           last_error = CASE WHEN ? THEN NULL ELSE ? END,
                           details_json = COALESCE(?, details_json),
                           updated_at = ?
                     WHERE chain_type = ? AND target_id = ?
                    """,
                    (
                        target_label, norm_status, score, failures, observations,
                        failures / max(1, observations), now,
                        1 if success else 0, now,
                        1 if success else 0, now,
                        1 if success else 0, safe_error,
                        details_json, now, chain, target,
                    ),
                )
            conn.commit()
            out = conn.execute(
                "SELECT * FROM health_records WHERE chain_type = ? AND target_id = ?",
                (chain, target),
            ).fetchone()
            return _row_to_record(out, now=now) if out is not None else {}


def read_health_summary(
    *,
    chain_type: str | None = None,
    limit: int = 100,
    now: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable snapshot of latest health records."""
    ts = time.time() if now is None else float(now)
    try:
        ensure_health_schema(db_path)
        with _connect(db_path) as conn:
            if chain_type:
                rows = conn.execute(
                    """
                    SELECT * FROM health_records
                     WHERE chain_type = ?
                     ORDER BY updated_at DESC, id DESC
                     LIMIT ?
                    """,
                    (chain_type, max(1, int(limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM health_records
                     ORDER BY updated_at DESC, id DESC
                     LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
    except sqlite3.Error as exc:
        return {"schema": SCHEMA, "status": "error", "error": str(exc), "records": []}

    records = [_row_to_record(row, now=ts) for row in rows]
    worst = min((r["effective_health_score"] for r in records), default=100.0)
    degraded = sum(1 for r in records if r["status"] != "success" or r["effective_health_score"] < 70.0)
    return {
        "schema": SCHEMA,
        "status": "ok" if degraded == 0 else "degraded",
        "generated_at": ts,
        "record_count": len(records),
        "degraded_count": degraded,
        "worst_effective_health_score": round(worst, 4),
        "records": records,
    }


__all__ = [
    "SCHEMA",
    "STALE_HALF_LIFE_SECONDS",
    "ensure_health_schema",
    "record_health_observation",
    "read_health_summary",
]
