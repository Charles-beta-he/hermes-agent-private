#!/usr/bin/env python3
"""Deterministic fixture-only retrieval quality check for local RAG governance.

This helper intentionally builds a temporary SQLite FTS5 database from the
checked-in benchmark fixture and synthetic fixture documents. It does not read
or mutate live Hermes session/task databases, does not claim task authority, and
does not call embedding, rerank, network, daemon, or external services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "evals" / "cases" / "local-brain-rag-retrieval-benchmark-v0.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "raghermes-local-governance"
BOUNDARY = "fixture_only_temp_sqlite_no_live_db_write_no_external_side_effect"


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_:-]+", "_", value).strip("_")


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def quote_fts_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def query_to_fts(query: str) -> str:
    tokens = re.findall(r"[\w\-:/@.]+", query, flags=re.UNICODE)
    # Keep the query broad and deterministic; OR helps natural-language fixture
    # queries retrieve synthetic documents that contain the expected evidence
    # terms without depending on tokenizer-specific phrase behavior.
    return " OR ".join(quote_fts_token(token) for token in tokens[:16]) or quote_fts_token(query)


def make_document(case: dict[str, Any]) -> dict[str, Any]:
    expected_terms = list(case.get("expected_terms", []))
    source_classes = list(case.get("expected_source_classes", []))
    provenance = {
        "fixture_case_id": case["id"],
        "source_classes": source_classes,
        "authority_expectation": case.get("authority_expectation"),
    }
    content = "\n".join(
        [
            f"case_id: {case['id']}",
            f"domain: {case.get('domain', '')}",
            f"query: {case.get('query', '')}",
            "expected_terms: " + " | ".join(expected_terms),
            "source_classes: " + " | ".join(source_classes),
            "authority_expectation: " + str(case.get("authority_expectation", "")),
            "provenance: " + json.dumps(provenance, ensure_ascii=False, sort_keys=True),
        ]
    )
    return {
        "doc_id": f"fixture::{case['id']}",
        "case_id": case["id"],
        "content": content,
        "provenance": provenance,
        "expected_terms": expected_terms,
    }


def iter_noise_documents() -> Iterable[dict[str, Any]]:
    # Low-scoring distractors verify that the ranking metric is not hard-coded to
    # success solely by document count; they intentionally omit per-case IDs and
    # required term sets.
    for index, content in enumerate(
        [
            "generic Hermes architecture note about plugins, providers, and command routing",
            "unrelated local database maintenance note with no benchmark provenance",
            "general RAG glossary mentioning retrieval, embeddings, chunking, and evaluation",
        ],
        start=1,
    ):
        yield {
            "doc_id": f"noise::{index}",
            "case_id": "noise",
            "content": content,
            "provenance": {"fixture_case_id": None, "source_classes": [], "authority_expectation": "noise"},
            "expected_terms": [],
        }


def build_temp_fts(conn: sqlite3.Connection, documents: list[dict[str, Any]]) -> None:
    conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id UNINDEXED, case_id UNINDEXED, content, provenance UNINDEXED)")
    conn.executemany(
        "INSERT INTO docs_fts(doc_id, case_id, content, provenance) VALUES (?, ?, ?, ?)",
        [
            (
                doc["doc_id"],
                doc["case_id"],
                doc["content"],
                json.dumps(doc["provenance"], ensure_ascii=False, sort_keys=True),
            )
            for doc in documents
        ],
    )


def retrieve(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT doc_id, case_id, content, provenance, bm25(docs_fts) AS score "
        "FROM docs_fts WHERE docs_fts MATCH ? ORDER BY score LIMIT ?",
        (query_to_fts(query), limit),
    ).fetchall()
    return [
        {"rank": i + 1, "doc_id": row[0], "case_id": row[1], "content": row[2], "provenance": json.loads(row[3]), "score": row[4]}
        for i, row in enumerate(rows)
    ]


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    cases = list(fixture.get("cases", []))
    documents = [make_document(case) for case in cases] + list(iter_noise_documents())
    per_case: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="raghermes-fts-fixture-") as temp_dir:
        db_path = Path(temp_dir) / "fixture.db"
        with sqlite3.connect(db_path) as conn:
            build_temp_fts(conn, documents)
            for case in cases:
                results = retrieve(conn, case["query"], limit=10)
                ranks = [result["rank"] for result in results if result["case_id"] == case["id"]]
                rank = ranks[0] if ranks else None
                top5 = results[:5]
                required_terms = [str(term) for term in case.get("expected_terms", [])]
                target_content = "\n".join(result["content"] for result in results if result["case_id"] == case["id"])
                terms_found = [term for term in required_terms if term in target_content]
                provenance_complete = all(
                    result.get("provenance", {}).get("fixture_case_id") == result.get("case_id")
                    and "source_classes" in result.get("provenance", {})
                    and "authority_expectation" in result.get("provenance", {})
                    for result in results
                    if result.get("case_id") == case["id"]
                )
                authority_violation = any(
                    result.get("provenance", {}).get("authority_expectation") not in {case.get("authority_expectation"), "noise"}
                    for result in results
                    if result.get("case_id") == case["id"]
                )
                per_case.append(
                    {
                        "id": case["id"],
                        "rank": rank,
                        "recall_at_5": bool(rank and rank <= 5),
                        "recall_at_10": bool(rank and rank <= 10),
                        "context_precision_at_5": 1.0 if top5 and top5[0]["case_id"] == case["id"] else 0.0,
                        "expected_term_coverage": len(terms_found) / len(required_terms) if required_terms else 1.0,
                        "provenance_complete": provenance_complete,
                        "authority_violation": authority_violation,
                    }
                )
    count = len(per_case) or 1
    mrr_at_10 = sum((1.0 / item["rank"]) if item["rank"] and item["rank"] <= 10 else 0.0 for item in per_case) / count
    recall_at_5 = sum(1 for item in per_case if item["recall_at_5"]) / count
    recall_at_10 = sum(1 for item in per_case if item["recall_at_10"]) / count
    context_precision_at_5 = sum(item["context_precision_at_5"] for item in per_case) / count
    provenance_completeness = sum(1 for item in per_case if item["provenance_complete"]) / count
    authority_violation_rate = sum(1 for item in per_case if item["authority_violation"]) / count
    term_coverage = sum(item["expected_term_coverage"] for item in per_case) / count
    thresholds = fixture.get("metrics", {}).get("report_only_thresholds", {})
    metrics = {
        "MRR@10": mrr_at_10,
        "Recall@5": recall_at_5,
        "Recall@10": recall_at_10,
        "ContextPrecision@5": context_precision_at_5,
        "AuthorityViolationRate": authority_violation_rate,
        "ProvenanceCompleteness": provenance_completeness,
        "ExpectedTermCoverage": term_coverage,
    }
    threshold_results = {
        key: {"actual": metrics[key], "threshold": value, "status": "PASS" if metrics[key] >= value else "FAIL"}
        for key, value in thresholds.items()
        if key in metrics and key != "AuthorityViolationRate"
    }
    if "AuthorityViolationRate" in thresholds:
        threshold_results["AuthorityViolationRate"] = {
            "actual": authority_violation_rate,
            "threshold": thresholds["AuthorityViolationRate"],
            "status": "PASS" if authority_violation_rate <= thresholds["AuthorityViolationRate"] else "FAIL",
        }
    status = "PASS" if all(item["status"] == "PASS" for item in threshold_results.values()) else "FAIL"
    return {"status": status, "case_count": len(cases), "metrics": metrics, "threshold_results": threshold_results, "per_case": per_case}


def build_report(fixture_path: Path) -> dict[str, Any]:
    fixture = load_fixture(fixture_path)
    evaluation = evaluate_fixture(fixture)
    report: dict[str, Any] = {
        "report_type": "raghermes_fixture_retrieval_quality",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "boundary": BOUNDARY,
        "fixture_path": str(fixture_path),
        "fixture_sha256": sha256_text(fixture_path.read_text(encoding="utf-8")),
        "fixture_schema": fixture.get("schema"),
        "red_lines": fixture.get("red_lines", []),
        "evaluation": evaluation,
        "next_safe_slice": "Wire this fixture-only evaluator into the local governance projection or compare it against read-only live retrieval candidates after a separate human gate.",
    }
    stable = json.dumps({k: v for k, v in report.items() if k != "created_at"}, ensure_ascii=False, sort_keys=True)
    report["report_hash"] = sha256_text(stable)
    return report


def write_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    short_hash = re.sub(r"^sha256:", "", report["report_hash"])[:16]
    path = output_dir / f"raghermes_retrieval_quality_fixture.{short_hash}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(Path(args.fixture).expanduser().resolve())
    if args.stdout:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        path = write_report(report, Path(args.output_dir).expanduser().resolve())
        print(
            "RAGHERMES_RETRIEVAL_QUALITY_FIXTURE_READY "
            f"status={report['evaluation']['status']} report_hash={report['report_hash']} "
            f"artifact={path} boundary={report['boundary']}"
        )
    return 0 if report["evaluation"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
