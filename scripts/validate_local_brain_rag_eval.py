#!/usr/bin/env python3
"""Validate the v0 Hermes local-brain RAG retrieval benchmark fixture.

This validator is intentionally deterministic and side-effect free. It checks
that the fixture contains exactly the required labeled cases and, optionally,
probes a Hermes state.db for the current SQLite FTS5/trigram baseline surfaces.
It does not run embeddings, vector DBs, rerankers, or model calls.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

REQUIRED_SCHEMA = "hermes_local_brain_rag_retrieval_benchmark_v0"
REQUIRED_CASE_COUNT = 30
REQUIRED_METRICS = {
    "MRR@10",
    "Recall@5",
    "Recall@10",
    "ContextPrecision@5",
    "AuthorityViolationRate",
    "ProvenanceCompleteness",
}
REQUIRED_CASE_FIELDS = {
    "id",
    "domain",
    "query",
    "expected_source_classes",
    "expected_terms",
    "authority_expectation",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"fixture root must be object: {path}")
    return data


def _validate_fixture(data: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if data.get("schema") != REQUIRED_SCHEMA:
        failures.append(f"schema mismatch: {data.get('schema')!r}")

    cases = data.get("cases")
    if not isinstance(cases, list):
        failures.append("cases must be a list")
        cases = []
    if len(cases) != REQUIRED_CASE_COUNT:
        failures.append(f"case_count expected {REQUIRED_CASE_COUNT}, got {len(cases)}")

    ids: set[str] = set()
    domains: set[str] = set()
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            failures.append(f"case[{idx}] must be object")
            continue
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            failures.append(f"case[{idx}] missing fields: {sorted(missing)}")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid.startswith("lb-rag-"):
            failures.append(f"case[{idx}] invalid id: {cid!r}")
        elif cid in ids:
            failures.append(f"duplicate case id: {cid}")
        else:
            ids.add(cid)
        if isinstance(case.get("domain"), str):
            domains.add(case["domain"])
        if not isinstance(case.get("query"), str) or len(case.get("query", "")) < 12:
            failures.append(f"case[{idx}] query too short")
        if not case.get("expected_terms"):
            failures.append(f"case[{idx}] expected_terms empty")
        if not case.get("expected_source_classes"):
            failures.append(f"case[{idx}] expected_source_classes empty")

    metrics = set(data.get("metrics", {}).get("ranking", [])) | set(data.get("metrics", {}).get("governance", []))
    missing_metrics = REQUIRED_METRICS - metrics
    if missing_metrics:
        failures.append(f"missing metrics: {sorted(missing_metrics)}")

    return {
        "status": "PASS" if not failures else "FAIL",
        "case_count": len(cases),
        "domain_count": len(domains),
        "domains": sorted(domains),
        "metric_count": len(metrics),
        "failures": failures,
    }


def _probe_state_db(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"status": "WARN", "reason": "state_db_missing", "path": str(db_path)}
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        compile_opts = [row[0] for row in conn.execute("PRAGMA compile_options")]
        fts5_compiled = any("FTS5" in opt.upper() for opt in compile_opts)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        probe: dict[str, Any] = {
            "status": "PASS",
            "path": str(db_path),
            "sqlite_version": sqlite3.sqlite_version,
            "fts5_compiled": fts5_compiled,
            "has_messages": "messages" in tables,
            "has_messages_fts": "messages_fts" in tables,
            "has_messages_fts_trigram": "messages_fts_trigram" in tables,
            "fts_trigger_count": len([t for t in triggers if t.startswith("messages_fts")]),
        }
        if not probe["has_messages_fts"]:
            probe["status"] = "WARN"
            probe["reason"] = "messages_fts_missing"
        return probe
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--probe-state-db", type=Path)
    args = parser.parse_args()

    fixture = _validate_fixture(_load_json(args.fixture))
    result: dict[str, Any] = {"fixture": fixture}
    if args.probe_state_db:
        result["fts5_probe"] = _probe_state_db(args.probe_state_db)

    result["status"] = "PASS" if fixture["status"] == "PASS" and result.get("fts5_probe", {"status": "PASS"})["status"] in {"PASS", "WARN"} else "FAIL"
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
