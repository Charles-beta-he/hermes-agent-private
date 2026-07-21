from __future__ import annotations

import copy
import json
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

from workflow_kernel.workflow_run_event_ledger import (
    WorkflowRunEventLedgerError,
    append_workflow_run_event,
    derive_workflow_run_event_id_v1,
    read_workflow_run_events,
)


FIXTURES = Path(__file__).resolve().parents[1] / "contracts/fixtures/workflow-run-event-v1/cases.json"


def _first_event() -> dict[str, object]:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return copy.deepcopy(next(case["value"] for case in cases if case["name"] == "valid first workflow event"))


def _candidate(first: dict[str, object], suffix: str) -> dict[str, object]:
    body = {
        "schema_version": "workflow_run_event_v1",
        "run_id": first["run_id"],
        "sequence": 2,
        "previous_event_id": first["event_id"],
        "idempotency_key": f"run-1:2:{suffix}",
        "occurred_at": "2026-07-20T00:00:02Z",
        "provenance": {
            "producer": "workflow_runtime_claim_executor",
            "artifact_path": f"docs/private/runtime/{suffix}.json",
            "artifact_digest": "sha256:" + suffix[0] * 64,
        },
        "transition": {"from": "claimed", "to": "running"},
    }
    return {**body, "event_id": derive_workflow_run_event_id_v1(body)}


def _append_worker(root: str, ledger: str, event: dict[str, object]) -> str:
    try:
        result = append_workflow_run_event(Path(ledger), event, private_root=Path(root))
        return str(result["status"])
    except WorkflowRunEventLedgerError as exc:
        return f"error:{exc.code}"


def test_multiprocess_exact_retries_produce_one_record(tmp_path: Path) -> None:
    root = tmp_path / "private"
    ledger = root / "events.jsonl"
    event = _first_event()
    ctx = get_context("spawn")

    with ProcessPoolExecutor(max_workers=6, mp_context=ctx) as pool:
        results = list(pool.map(_append_worker, [str(root)] * 12, [str(ledger)] * 12, [event] * 12))

    assert results.count("appended") == 1
    assert results.count("duplicate") == 11
    assert read_workflow_run_events(ledger, private_root=root) == [event]
    assert ledger.read_bytes().count(b"\n") == 1


def test_multiprocess_semantic_collision_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "private"
    ledger = root / "events.jsonl"
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    alpha = _candidate(first, "alpha")
    beta = _candidate(first, "beta")
    beta["idempotency_key"] = alpha["idempotency_key"]
    beta_body = {key: value for key, value in beta.items() if key != "event_id"}
    beta["event_id"] = derive_workflow_run_event_id_v1(beta_body)
    candidates = [alpha, beta]
    ctx = get_context("spawn")

    with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
        results = list(pool.map(_append_worker, [str(root)] * 2, [str(ledger)] * 2, candidates))

    assert sorted(results) == ["appended", "error:idempotency_conflict"]
    events = read_workflow_run_events(ledger, private_root=root)
    assert len(events) == 2
    assert events[0] == first
    assert events[1] in candidates


def test_multiprocess_competing_next_events_leave_one_valid_tip(tmp_path: Path) -> None:
    root = tmp_path / "private"
    ledger = root / "events.jsonl"
    first = _first_event()
    append_workflow_run_event(ledger, first, private_root=root)
    candidates = [_candidate(first, "alpha"), _candidate(first, "beta")]
    ctx = get_context("spawn")

    with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
        results = list(pool.map(_append_worker, [str(root)] * 2, [str(ledger)] * 2, candidates))

    assert sorted(results) == ["appended", "error:sequence_mismatch"]
    events = read_workflow_run_events(ledger, private_root=root)
    assert len(events) == 2
    assert events[0] == first
    assert events[1] in candidates
