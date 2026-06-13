#!/usr/bin/env python3
"""Project Hermes' local RAG/evidence/database governance surface.

This is a read-only control-plane helper for the RAGHermes absorption/evaluation
path. It inspects source files and optional SQLite databases, then emits a
stable JSON report describing local retrieval/evidence storage capabilities and
open governance gaps. It never mutates session state, Kanban/taskpool state,
credentials, cron, or external systems.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "raghermes-local-governance"

SOURCE_PROBES = [
    {
        "id": "session_sqlite_fts_trigram",
        "path": "hermes_state.py",
        "patterns": [
            "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts",
            "messages_fts_trigram",
            "tokenize='trigram'",
            "BEGIN IMMEDIATE",
            "_WRITE_MAX_RETRIES",
            "wal_checkpoint(TRUNCATE)",
        ],
        "capability": "SQLite session ledger with FTS5/trigram retrieval and bounded write-contention handling.",
    },
    {
        "id": "session_storage_docs",
        "path": "website/docs/developer-guide/session-storage.md",
        "patterns": [
            "messages_fts",
            "messages_fts_trigram",
            "WAL mode",
            "Write Contention Handling",
        ],
        "capability": "Operator-facing documentation for session storage, retrieval indexes, and contention policy.",
    },
    {
        "id": "tool_result_persistence",
        "path": "tools/tool_result_storage.py",
        "patterns": [
            "maybe_persist_tool_result",
            "enforce_turn_budget",
            "Full output saved to:",
            "cat >",
        ],
        "capability": "Large tool outputs can be spilled to local artifacts and referenced by pointer instead of flooding context.",
    },
    {
        "id": "sidecar_taskpool_proof_ingest",
        "path": "session_sidecar/consumer.py",
        "patterns": [
            "taskpool_proof_ingest",
            "strip_sensitive",
            "proof-log",
        ],
        "capability": "Sidecar evidence can be redacted and converted into taskpool-friendly proof records.",
    },
]

REQUIRED_SESSION_TABLES = ["sessions", "messages", "messages_fts", "messages_fts_trigram", "schema_version"]
REQUIRED_KANBAN_TABLES = ["tasks", "task_events"]


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def probe_sources(repo_root: Path) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for probe in SOURCE_PROBES:
        path = repo_root / probe["path"]
        record: dict[str, Any] = {
            "id": probe["id"],
            "path": str(path),
            "capability": probe["capability"],
            "exists": path.exists(),
            "patterns": [],
        }
        text = read_text(path) if path.exists() else ""
        for pattern in probe["patterns"]:
            record["patterns"].append({"pattern": pattern, "found": pattern in text})
        record["status"] = "PASS" if record["exists"] and all(p["found"] for p in record["patterns"]) else "FAIL"
        record["source_sha256"] = sha256_text(text) if text else None
        probes.append(record)
    return probes


def sqlite_tables(db_path: Path) -> set[str]:
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    return {str(row[0]) for row in rows}


def probe_sqlite_db(db_path: Path | None, required_tables: list[str], label: str) -> dict[str, Any]:
    if db_path is None:
        return {"label": label, "status": "SKIPPED", "reason": "no database path supplied", "required_tables": required_tables}
    record: dict[str, Any] = {"label": label, "path": str(db_path), "required_tables": required_tables}
    if not db_path.exists():
        record.update({"status": "FAIL", "reason": "database path does not exist"})
        return record
    try:
        tables = sqlite_tables(db_path)
    except sqlite3.Error as exc:
        record.update({"status": "FAIL", "reason": f"sqlite read-only open/query failed: {exc}"})
        return record
    record["present_required_tables"] = sorted(t for t in required_tables if t in tables)
    record["missing_required_tables"] = sorted(t for t in required_tables if t not in tables)
    record["table_count"] = len(tables)
    record["status"] = "PASS" if not record["missing_required_tables"] else "FAIL"
    return record


def derive_findings(source_probes: list[dict[str, Any]], db_probes: list[dict[str, Any]]) -> list[dict[str, str]]:
    findings = []
    for probe in source_probes:
        findings.append({
            "id": probe["id"],
            "status": probe["status"],
            "finding": probe["capability"] if probe["status"] == "PASS" else "Expected source-level governance marker was missing.",
        })
    for probe in db_probes:
        findings.append({
            "id": f"db_{probe['label']}",
            "status": probe["status"],
            "finding": "Live/read-only SQLite schema contains required governance tables." if probe["status"] == "PASS" else probe.get("reason", "Required live DB tables are missing or unverified."),
        })
    return findings


def build_projection(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    state_db = Path(args.state_db).expanduser().resolve() if args.state_db else None
    kanban_db = Path(args.kanban_db).expanduser().resolve() if args.kanban_db else None
    source_probes = probe_sources(repo_root)
    db_probes = [
        probe_sqlite_db(state_db, REQUIRED_SESSION_TABLES, "session_state"),
        probe_sqlite_db(kanban_db, REQUIRED_KANBAN_TABLES, "kanban_taskpool"),
    ]
    projection = {
        "projection_type": "raghermes_local_evidence_database_governance",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "boundary": "read_only_projection_no_db_write_no_task_claim_no_external_side_effect",
        "repo_root": str(repo_root),
        "source_probes": source_probes,
        "database_probes": db_probes,
        "findings": derive_findings(source_probes, db_probes),
        "governance_gaps": [
            "No production RAG ingestion/query pipeline is authorized by this projection; it only inventories local primitives.",
            "Live DB probes are read-only schema checks and do not certify recall quality, retention policy, PII policy, or embedding freshness.",
            "Any canonical taskpool/mission synchronization or external retrieval service needs a separate human gate if it changes lifecycle, credentials, cadence, or external systems.",
        ],
        "next_safe_slice": "Add deterministic fixture-based retrieval-quality checks over temporary SQLite/FTS data, still with no live DB mutation.",
    }
    stable = json.dumps({k: v for k, v in projection.items() if k != "created_at"}, ensure_ascii=False, sort_keys=True)
    projection["projection_hash"] = sha256_text(stable)
    return projection


def write_projection(projection: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    short_hash = re.sub(r"^sha256:", "", projection["projection_hash"])[:16]
    path = output_dir / f"raghermes_local_governance.{short_hash}.json"
    path.write_text(json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--state-db", default=str(Path.home() / ".hermes" / "state.db"))
    parser.add_argument("--kanban-db", default=str(Path.home() / ".hermes" / "kanban.db"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--stdout", action="store_true", help="print JSON to stdout instead of writing an artifact")
    args = parser.parse_args(argv)
    projection = build_projection(args)
    if args.stdout:
        print(json.dumps(projection, ensure_ascii=False, sort_keys=True))
    else:
        path = write_projection(projection, Path(args.output_dir))
        print(
            "RAGHERMES_LOCAL_GOVERNANCE_PROJECTION_READY "
            f"status=PASS projection_hash={projection['projection_hash']} artifact={path} "
            f"boundary={projection['boundary']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
