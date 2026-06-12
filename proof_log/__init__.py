from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

SCHEMA_VERSION = "proof-log-entry/v1"
LINK_TYPES = {"browser", "cron", "script"}
STATUSES = {"started", "succeeded", "failed", "blocked", "cancelled", "timed_out", "partial", "redacted"}
_FALSEY = {"", "0", "false", "no", "off", "disabled"}


def proof_log_enabled() -> bool:
    return os.environ.get("HERMES_PROOF_LOG", "").strip().lower() not in _FALSEY


def default_db_path() -> Path:
    override = os.environ.get("HERMES_PROOF_LOG_DB", "").strip()
    if override:
        return Path(override)
    profile = os.environ.get("HERMES_PROFILE", "").strip()
    home = get_hermes_home()
    if profile and home.name != profile:
        candidate = home / "profiles" / profile
        if candidate.parent.exists() or home.name == ".hermes":
            return candidate / "proof_logs.db"
    return home / "proof_logs.db"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS proof_logs (
          entry_id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          profile TEXT NOT NULL,
          session_id TEXT,
          link_type TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          entry_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_proof_logs_agent_created ON proof_logs(agent_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_proof_logs_link_created ON proof_logs(link_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_proof_logs_created ON proof_logs(created_at);
        """
    )


def finalize_entry(draft: dict[str, Any]) -> dict[str, Any]:
    ts = str(draft.get("created_at") or draft.get("started_at") or now_iso())
    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "entry_id": draft.get("entry_id") or f"pl_{uuid.uuid4().hex}",
        "agent_id": draft.get("agent_id") or "hermes-agent",
        "profile": draft.get("profile") or os.environ.get("HERMES_PROFILE") or "default",
        "session_id": draft.get("session_id"),
        "link_type": draft.get("link_type"),
        "status": draft.get("status") or "succeeded",
        "created_at": ts,
        "started_at": draft.get("started_at") or ts,
        "ended_at": draft.get("ended_at") or now_iso(),
        "duration_ms": draft.get("duration_ms"),
        "input_summary": str(draft.get("input_summary") or ""),
        "output_summary": str(draft.get("output_summary") or ""),
        "tool_calls": list(draft.get("tool_calls") or []),
        "errors": list(draft.get("errors") or []),
        "payload": draft.get("payload") or {"kind": "generic", "redaction": {"state": "raw", "policy": "none", "fields": [], "payload_hash": None}, "data": {}},
        "metadata": dict(draft.get("metadata") or {}),
    }
    for key in ("run_id", "tenant", "link_ref", "correlation_id", "parent_entry_id", "retention"):
        if key in draft:
            entry[key] = draft[key]
    if entry["link_type"] not in LINK_TYPES:
        raise ValueError(f"invalid proof log link_type: {entry['link_type']!r}")
    if entry["status"] not in STATUSES:
        raise ValueError(f"invalid proof log status: {entry['status']!r}")
    unsigned = dict(entry)
    unsigned.pop("integrity", None)
    entry["integrity"] = {"canonical_json_sha256": _sha256(unsigned)}
    return entry


def write_proof_log(entry: dict[str, Any], *, db_path: str | Path | None = None) -> dict[str, Any]:
    final = finalize_entry(entry)
    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(final, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    with sqlite3.connect(path) as con:
        _init_db(con)
        con.execute(
            "INSERT OR REPLACE INTO proof_logs(entry_id, agent_id, profile, session_id, link_type, status, created_at, entry_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                final["entry_id"],
                final["agent_id"],
                final["profile"],
                final.get("session_id"),
                final["link_type"],
                final["status"],
                final["created_at"],
                payload,
            ),
        )
    return {"entry_id": final["entry_id"], "persisted": True, "store": "sqlite", "db_path": str(path)}


def get_proof_log(entry_id: str, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    path = Path(db_path) if db_path is not None else default_db_path()
    if not path.exists():
        return None
    with sqlite3.connect(path) as con:
        row = con.execute("SELECT entry_json FROM proof_logs WHERE entry_id = ?", (entry_id,)).fetchone()
    return json.loads(row[0]) if row else None
