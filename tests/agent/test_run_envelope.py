from __future__ import annotations

import json
from pathlib import Path

from agent.run_envelope import RunEnvelopeV1, append_run_event, build_projection_ref


def test_run_envelope_v1_requires_unified_core_fields():
    envelope = RunEnvelopeV1(
        run_id="kanban:42",
        surface="kanban",
        lifecycle_state="running",
        trigger={"kind": "kanban_worker", "task_id": "t_demo"},
        proof_refs=["kanban_db:task_events:t_demo:claimed"],
        projection_refs=[build_projection_ref("kanban.db", "tasks/t_demo")],
    )

    payload = envelope.to_dict()

    assert payload["schema"] == "hermes_run_envelope_v1"
    assert payload["run_id"] == "kanban:42"
    assert payload["lifecycle_state"] == "running"
    assert payload["proof_refs"] == ["kanban_db:task_events:t_demo:claimed"]
    assert payload["projection_refs"] == [
        {"projection": "kanban.db", "ref": "tasks/t_demo"}
    ]
    assert payload["reducer_checkpoint"]["schema"] == "hermes_run_reducer_checkpoint_v1"


def test_append_run_event_writes_append_only_ledger_and_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_RUN_LEDGER_DIR", str(tmp_path))

    first = append_run_event(
        RunEnvelopeV1(
            run_id="taskpool:abc",
            surface="taskpool",
            lifecycle_state="claimed",
            trigger={"kind": "local_brain_taskpool", "task_id": "abc"},
            proof_refs=["file:/brain/tasks/abc.md"],
        )
    )
    second = append_run_event(
        RunEnvelopeV1(
            run_id="taskpool:abc",
            surface="taskpool",
            lifecycle_state="running",
            trigger={"kind": "local_brain_taskpool", "task_id": "abc"},
            proof_refs=["file:/brain/tasks/abc.md"],
        )
    )

    ledger = tmp_path / "run_events.jsonl"
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    checkpoint = json.loads((tmp_path / "run_reducer_checkpoint.json").read_text(encoding="utf-8"))

    assert len(lines) == 2
    assert lines[0]["event_seq"] == 1
    assert lines[1]["event_seq"] == 2
    assert first["ledger_path"] == str(ledger)
    assert second["checkpoint_path"] == str(tmp_path / "run_reducer_checkpoint.json")
    assert checkpoint["runs"]["taskpool:abc"]["lifecycle_state"] == "running"
    assert checkpoint["runs"]["taskpool:abc"]["last_event_seq"] == 2


def test_append_run_event_rebuilds_corrupt_checkpoint_from_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_RUN_LEDGER_DIR", str(tmp_path))

    append_run_event(
        RunEnvelopeV1(
            run_id="dispatch:tick-1",
            surface="dispatch_tick",
            lifecycle_state="tick_ok",
            trigger={"kind": "dispatch_tick"},
            proof_refs=["jsonl:/tmp/tick.jsonl"],
        )
    )
    (tmp_path / "run_reducer_checkpoint.json").write_text("not-json", encoding="utf-8")
    result = append_run_event(
        RunEnvelopeV1(
            run_id="dispatch:tick-2",
            surface="dispatch_tick",
            lifecycle_state="tick_ok",
            trigger={"kind": "dispatch_tick"},
            proof_refs=["jsonl:/tmp/tick.jsonl"],
        )
    )

    lines = [json.loads(line) for line in (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines()]
    checkpoint = json.loads((tmp_path / "run_reducer_checkpoint.json").read_text(encoding="utf-8"))

    assert result["event_seq"] == 2
    assert [line["event_seq"] for line in lines] == [1, 2]
    assert checkpoint["last_event_seq"] == 2
    assert checkpoint["runs"]["dispatch:tick-1"]["lifecycle_state"] == "tick_ok"
    assert checkpoint["runs"]["dispatch:tick-2"]["lifecycle_state"] == "tick_ok"


def test_append_run_event_reconciles_stale_valid_checkpoint_from_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_RUN_LEDGER_DIR", str(tmp_path))

    append_run_event(
        RunEnvelopeV1(
            run_id="kanban:1",
            surface="kanban",
            lifecycle_state="running",
            trigger={"kind": "kanban_worker"},
            proof_refs=["kanban_db:task_events:t_demo:claimed"],
        )
    )
    stale_checkpoint = {
        "schema": "hermes_run_reducer_checkpoint_v1",
        "last_event_seq": 0,
        "runs": {},
    }
    (tmp_path / "run_reducer_checkpoint.json").write_text(
        json.dumps(stale_checkpoint),
        encoding="utf-8",
    )

    result = append_run_event(
        RunEnvelopeV1(
            run_id="kanban:1",
            surface="kanban",
            lifecycle_state="completed",
            trigger={"kind": "kanban_worker"},
            proof_refs=["kanban_db:task_events:t_demo:completed"],
        )
    )

    lines = [json.loads(line) for line in (tmp_path / "run_events.jsonl").read_text(encoding="utf-8").splitlines()]
    checkpoint = json.loads((tmp_path / "run_reducer_checkpoint.json").read_text(encoding="utf-8"))

    assert result["event_seq"] == 2
    assert [line["event_seq"] for line in lines] == [1, 2]
    assert checkpoint["last_event_seq"] == 2
    assert checkpoint["runs"]["kanban:1"]["lifecycle_state"] == "completed"
