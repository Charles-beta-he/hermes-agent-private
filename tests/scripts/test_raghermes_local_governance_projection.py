from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

from scripts import raghermes_local_governance_projection as projection


def test_projection_stdout_passes_source_probes():
    report = projection.build_projection(
        Namespace(
            repo_root=str(projection.REPO_ROOT),
            state_db=None,
            kanban_db=None,
        )
    )

    assert report["boundary"] == "read_only_projection_no_db_write_no_task_claim_no_external_side_effect"
    assert report["projection_hash"].startswith("sha256:")
    source_status = {probe["id"]: probe["status"] for probe in report["source_probes"]}
    assert source_status["session_sqlite_fts_trigram"] == "PASS"
    assert source_status["tool_result_persistence"] == "PASS"
    assert report["retrieval_quality"]["status"] == "PASS"
    assert report["retrieval_quality"]["boundary"] == "temporary_fixture_only_no_live_db_mutation"
    assert {probe["status"] for probe in report["database_probes"]} == {"SKIPPED"}


def test_fixture_retrieval_quality_uses_in_memory_fts_and_provenance():
    result = projection.evaluate_fixture_retrieval_quality()

    assert result["status"] == "PASS"
    assert result["storage"] == "sqlite_memory_fts5"
    assert result["document_count"] == 4
    assert result["case_count"] == 3
    assert result["failed_cases"] == []
    assert all(case["recall_at_k_ok"] for case in result["case_results"])
    assert all(case["required_terms_ok"] for case in result["case_results"])
    assert all(case["provenance_fields_ok"] for case in result["case_results"])


def test_sqlite_db_probe_uses_readonly_schema_check(tmp_path: Path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sessions(id TEXT)")
        conn.execute("CREATE TABLE messages(id INTEGER)")
        conn.execute("CREATE TABLE messages_fts(content TEXT)")
        conn.execute("CREATE TABLE messages_fts_trigram(content TEXT)")
        conn.execute("CREATE TABLE schema_version(version INTEGER)")

    record = projection.probe_sqlite_db(db_path, projection.REQUIRED_SESSION_TABLES, "session_state")

    assert record["status"] == "PASS"
    assert record["missing_required_tables"] == []
    assert set(record["present_required_tables"]) == set(projection.REQUIRED_SESSION_TABLES)


def test_write_projection_artifact_round_trips(tmp_path: Path):
    report = projection.build_projection(
        Namespace(
            repo_root=str(projection.REPO_ROOT),
            state_db=None,
            kanban_db=None,
        )
    )
    path = projection.write_projection(report, tmp_path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["projection_hash"] == report["projection_hash"]
    assert path.name.startswith("raghermes_local_governance.")
