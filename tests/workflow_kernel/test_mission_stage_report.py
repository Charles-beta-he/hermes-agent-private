from __future__ import annotations

import hashlib
import json
import multiprocessing
import queue
import sqlite3
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hermes_cli.missions import MissionBudget, MissionStore
from workflow_kernel.mission_stage_report import (
    MissionStageReportError,
    append_mission_stage_report,
)


def _create_mission(path: Path) -> tuple[MissionStore, str, int]:
    store = MissionStore(path)
    mission = store.create_mission(
        direction="isolated F4 mission fixture",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=2),
        enable=False,
    )
    return store, mission.mission_id, store.mission_revision(mission.mission_id)


def _request(db_path: Path, mission_id: str, revision: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "mission_store_path": db_path,
        "mission_id": mission_id,
        "expected_revision": revision,
        "idempotency_key": "effect-stage-report-001",
        "summary": "isolated verification completed",
        "evidence": ("pytest:pass",),
        "risks": ("live integration remains closed",),
        "next_step": "review exact bytes",
        "needs_user": False,
    }
    values.update(overrides)
    return values


def _canonical_digest(value: object) -> str:
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [thaw(nested) for nested in item]
        return item

    encoded = json.dumps(
        thaw(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _race_append(args: tuple[str, str, int, str]) -> tuple[str, str]:
    db_path, mission_id, revision, key = args
    try:
        result = append_mission_stage_report(
            **_request(Path(db_path), mission_id, revision, idempotency_key=key)
        )
        return "applied", str(result["event"]["event_id"])
    except MissionStageReportError as exc:
        return exc.code, ""


def _gated_race_append(
    db_path: str,
    mission_id: str,
    revision: int,
    key: str,
    gate: Any,
    ready: Any,
    results: Any,
) -> None:
    ready.put(key)
    if not gate.wait(timeout=10):
        results.put(("gate_timeout", ""))
        return
    results.put(_race_append((db_path, mission_id, revision, key)))


def _contended_append_without_init(
    db_path: str,
    mission_id: str,
    revision: int,
    key: str,
    gate: Any,
    ready: Any,
    results: Any,
    sql_trace: Any,
) -> None:
    original_init = MissionStore._init_db
    original_connect = MissionStore._connect

    def traced_connect(store: MissionStore) -> sqlite3.Connection:
        conn = original_connect(store)

        def trace(statement: str) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                sql_trace.put(statement)

        conn.set_trace_callback(trace)
        return conn

    setattr(MissionStore, "_init_db", lambda self: None)
    setattr(MissionStore, "_connect", traced_connect)
    try:
        _gated_race_append(db_path, mission_id, revision, key, gate, ready, results)
    finally:
        setattr(MissionStore, "_connect", original_connect)
        setattr(MissionStore, "_init_db", original_init)


def _hold_immediate_transaction(
    db_path: str,
    acquired: Any,
    release: Any,
    results: Any,
) -> None:
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("BEGIN IMMEDIATE")
        acquired.set()
        if not release.wait(timeout=10):
            results.put(("holder_timeout", ""))
            return
        conn.rollback()
        results.put(("holder_released", ""))
    finally:
        conn.close()


def _stop_process(process: Any) -> None:
    process.join(timeout=2)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


def _run_gated_race(
    ctx: Any,
    db_path: Path,
    mission_id: str,
    revision: int,
    keys: list[str],
) -> list[tuple[str, str]]:
    gate = ctx.Event()
    ready = ctx.Queue()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_gated_race_append,
            args=(str(db_path), mission_id, revision, key, gate, ready, results),
        )
        for key in keys
    ]
    started: list[Any] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        assert {ready.get(timeout=10) for _ in processes} == set(keys)
        gate.set()
        collected = [results.get(timeout=10) for _ in processes]
    finally:
        gate.set()
        for process in started:
            _stop_process(process)
    for process in processes:
        assert process.exitcode == 0
    return collected


def _create_legacy_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE missions (
                mission_id TEXT PRIMARY KEY, direction TEXT NOT NULL, status TEXT NOT NULL,
                cadence TEXT NOT NULL, deliver TEXT NOT NULL, budget_json TEXT NOT NULL,
                stage TEXT NOT NULL, constraints_json TEXT NOT NULL, non_goals_json TEXT NOT NULL,
                success_criteria_json TEXT NOT NULL, allowed_actions_json TEXT NOT NULL,
                blocked_actions_json TEXT NOT NULL, plan_ref TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mission_events (
                event_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, event_type TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL, created_at REAL NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO missions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy", "legacy mission", "paused", "every 1h", "local",
                json.dumps({"max_ticks": 1}), "intake", "[]", "[]", "[]", "[]", "[]", "", 1.0, 1.0,
            ),
        )
        conn.execute(
            "INSERT INTO mission_events VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-event", "legacy", "mission_created", "legacy", "{}", 1.0),
        )


def _open_legacy_store_worker(db_path: str, gate: Any, queue: Any) -> None:
    if not gate.wait(timeout=10):
        queue.put("gate_timeout")
        return
    try:
        MissionStore(db_path)
        queue.put("opened")
    except BaseException as exc:
        queue.put(f"{type(exc).__name__}: {exc}")


class _FaultConnection:
    def __init__(self, connection: sqlite3.Connection, fault: str):
        self._connection = connection
        self._fault = fault

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        normalized = " ".join(sql.split())
        markers = {
            "begin": "BEGIN IMMEDIATE",
            "select": "SELECT 1 FROM missions",
            "insert": "INSERT INTO mission_events",
            "update": "UPDATE missions SET updated_at",
            "readback": "SELECT * FROM mission_events WHERE event_id",
        }
        marker = markers.get(self._fault)
        if marker is not None and marker in normalized:
            raise sqlite3.OperationalError(f"injected {self._fault} failure")
        return self._connection.execute(sql, parameters)

    def commit(self) -> None:
        if self._fault == "commit":
            raise sqlite3.OperationalError("injected commit failure")
        self._connection.commit()
        if self._fault == "commit_after":
            raise sqlite3.OperationalError("injected sensitive post-commit detail")


def _install_transaction_fault(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    original_connect = MissionStore._connect
    connection_calls = 0

    def connect(self: MissionStore) -> Any:
        nonlocal connection_calls
        connection_calls += 1
        connection = original_connect(self)
        if connection_calls == 2:
            return _FaultConnection(connection, fault)
        return connection

    monkeypatch.setattr(MissionStore, "_connect", connect)


def test_appends_one_non_actionable_stage_report_with_exact_readback(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    before = store.get_mission(mission_id)

    result = append_mission_stage_report(**_request(db_path, mission_id, revision))

    after = store.get_mission(mission_id)
    assert result["mission_id"] == mission_id
    assert result["mission_revision"] == revision + 1
    assert result["event"]["event_type"] == "stage_report"
    assert result["event"]["payload"] == {
        "evidence": ("pytest:pass",),
        "risks": ("live integration remains closed",),
        "next_step": "review exact bytes",
        "needs_user": False,
    }
    assert before is not None and after is not None
    assert (after.status, after.stage) == (before.status, before.stage)
    assert store.pending_flow_signals(mission_id) == []
    assert store.count_events(mission_id, {"stage_report"}) == 1
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT event_type, summary, payload_json, idempotency_key,
                   request_digest, mission_revision
            FROM mission_events WHERE event_id = ?
            """,
            (result["event"]["event_id"],),
        ).fetchone()
    assert dict(row) == {
        "event_type": "stage_report",
        "summary": "isolated verification completed",
        "payload_json": json.dumps(
            {
                "evidence": ["pytest:pass"],
                "risks": ["live integration remains closed"],
                "next_step": "review exact bytes",
                "needs_user": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "idempotency_key": "effect-stage-report-001",
        "request_digest": result["request_digest"],
        "mission_revision": revision + 1,
    }
    assert len(result["request_digest"]) == 64
    assert len(result["result_digest"]) == 64


def test_request_and_result_digests_are_domain_separated_and_recomputable(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    _, mission_id, revision = _create_mission(db_path)

    result = append_mission_stage_report(**_request(db_path, mission_id, revision))

    expected_request = {
        "schema": "mission_stage_report_request_v1",
        "operation": "append_stage_report",
        "mission_id": mission_id,
        "expected_revision": revision,
        "idempotency_key": "effect-stage-report-001",
        "summary": "isolated verification completed",
        "payload": {
            "evidence": ["pytest:pass"],
            "risks": ["live integration remains closed"],
            "next_step": "review exact bytes",
            "needs_user": False,
        },
    }
    assert result["request_digest"] == _canonical_digest(expected_request)
    body = dict(result)
    result_digest = body.pop("result_digest")
    assert result_digest == _canonical_digest(body)


def test_unencodable_surrogate_input_is_rejected_with_stable_code(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    _, mission_id, revision = _create_mission(db_path)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(
            **_request(db_path, mission_id, revision, summary="bad-\ud800")
        )

    assert raised.value.code == "request_invalid"


def test_same_request_replays_original_event_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    first = append_mission_stage_report(**_request(db_path, mission_id, revision))
    store.append_event(mission_id, "tick_dry_run", "unrelated", {}, enqueue_flow=False)
    before_count = store.count_events(mission_id, {"stage_report", "tick_dry_run"})

    replay = append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert replay == first
    assert store.count_events(mission_id, {"stage_report", "tick_dry_run"}) == before_count
    assert store.count_events(mission_id, {"stage_report"}) == 1


def test_conflicting_key_reuse_fails_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    append_mission_stage_report(**_request(db_path, mission_id, revision))
    before = db_path.read_bytes()

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(
            **_request(db_path, mission_id, revision, summary="different report")
        )

    assert raised.value.code == "idempotency_conflict"
    assert db_path.read_bytes() == before
    assert store.count_events(mission_id, {"stage_report"}) == 1


def test_stale_revision_fails_without_stage_or_status_change(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    store.append_event(mission_id, "tick_dry_run", "advance revision", {}, enqueue_flow=False)
    before = store.get_mission(mission_id)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(
            **_request(db_path, mission_id, revision, idempotency_key="stale")
        )

    after = store.get_mission(mission_id)
    assert raised.value.code == "revision_conflict"
    assert before is not None and after is not None
    assert (after.status, after.stage) == (before.status, before.stage)
    assert store.count_events(mission_id, {"stage_report"}) == 0


def test_stage_report_never_uses_actionable_flow_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    import hermes_cli.missions as missions

    monkeypatch.setattr(missions, "ACTIONABLE_FLOW_EVENT_TYPES", {*missions.ACTIONABLE_FLOW_EVENT_TYPES, "stage_report"})

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("flow path must remain unreachable")

    monkeypatch.setattr(MissionStore, "enqueue_flow_signal", forbidden)
    monkeypatch.setattr(MissionStore, "dispatch_pending_flow_signals", forbidden)

    append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert store.pending_flow_signals(mission_id) == []


def test_two_processes_on_same_revision_apply_exactly_one_distinct_request(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    ctx = multiprocessing.get_context("spawn")
    results = _run_gated_race(
        ctx,
        db_path,
        mission_id,
        revision,
        ["race-a", "race-b"],
    )

    assert sorted(code for code, _ in results) == ["applied", "revision_conflict"]
    assert store.count_events(mission_id, {"stage_report"}) == 1
    assert store.mission_revision(mission_id) == revision + 1


def test_two_processes_on_same_revision_replay_one_same_key_event(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    ctx = multiprocessing.get_context("spawn")
    results = _run_gated_race(
        ctx,
        db_path,
        mission_id,
        revision,
        ["race-same", "race-same"],
    )

    assert [code for code, _ in results] == ["applied", "applied"]
    assert len({event_id for _, event_id in results}) == 1
    assert store.count_events(mission_id, {"stage_report"}) == 1
    assert store.mission_revision(mission_id) == revision + 1


def test_begin_immediate_contender_waits_for_holder_then_applies(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    release = ctx.Event()
    holder_results = ctx.Queue()
    contender_gate = ctx.Event()
    contender_ready = ctx.Queue()
    contender_results = ctx.Queue()
    contender_sql_trace = ctx.Queue()
    holder = ctx.Process(
        target=_hold_immediate_transaction,
        args=(str(db_path), acquired, release, holder_results),
    )
    contender = ctx.Process(
        target=_contended_append_without_init,
        args=(
            str(db_path),
            mission_id,
            revision,
            "contended",
            contender_gate,
            contender_ready,
            contender_results,
            contender_sql_trace,
        ),
    )

    holder_started = False
    contender_started = False
    try:
        holder.start()
        holder_started = True
        assert acquired.wait(timeout=10)
        contender.start()
        contender_started = True
        assert contender_ready.get(timeout=10) == "contended"
        contender_gate.set()
        with pytest.raises(queue.Empty):
            contender_sql_trace.get(timeout=0.25)
        with pytest.raises(queue.Empty):
            contender_results.get(timeout=0.25)
        assert contender.is_alive()
    finally:
        release.set()
        if holder_started:
            _stop_process(holder)
        if contender_started:
            _stop_process(contender)

    assert holder_results.get(timeout=10) == ("holder_released", "")
    assert contender_sql_trace.get(timeout=10).lstrip().upper().startswith("SELECT")
    assert contender_results.get(timeout=10)[0] == "applied"
    assert holder.exitcode == 0
    assert contender.exitcode == 0
    assert store.count_events(mission_id, {"stage_report"}) == 1
    assert store.mission_revision(mission_id) == revision + 1


def test_legacy_database_migration_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)

    store = MissionStore(db_path)

    assert store.get_mission("legacy") is not None
    assert store.list_events("legacy")[0]["event_id"] == "legacy-event"
    assert store.mission_revision("legacy") == 1
    with store._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(mission_events)")}
    assert {"idempotency_key", "request_digest", "mission_revision"} <= columns


def test_concurrent_legacy_database_migration_is_serialized(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    for attempt in range(3):
        db_path = tmp_path / f"legacy-race-{attempt}.db"
        _create_legacy_database(db_path)
        gate = ctx.Event()
        queue = ctx.Queue()
        processes = [
            ctx.Process(target=_open_legacy_store_worker, args=(str(db_path), gate, queue))
            for _ in range(2)
        ]
        started: list[Any] = []
        try:
            for process in processes:
                process.start()
                started.append(process)
            gate.set()
            results = [queue.get(timeout=10) for _ in processes]
        finally:
            gate.set()
            for process in started:
                _stop_process(process)
        for process in processes:
            assert process.exitcode == 0
        assert results == ["opened", "opened"]


@pytest.mark.parametrize(
    "field",
    [
        "mission_store_path",
        "mission_id",
        "expected_revision",
        "idempotency_key",
        "summary",
        "evidence",
        "risks",
        "next_step",
        "needs_user",
    ],
)
def test_required_payload_fields_cannot_be_omitted(tmp_path: Path, field: str) -> None:
    db_path = tmp_path / f"missing-{field}.db"
    store, mission_id, revision = _create_mission(db_path)
    request = _request(db_path, mission_id, revision)
    request.pop(field)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**request)

    assert raised.value.code == "request_invalid"
    assert store.count_events(mission_id, {"stage_report"}) == 0


def test_unknown_request_field_has_stable_code(tmp_path: Path) -> None:
    db_path = tmp_path / "unknown-field.db"
    store, mission_id, revision = _create_mission(db_path)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(
            **_request(db_path, mission_id, revision), unknown_field="hostile"
        )

    assert raised.value.code == "request_invalid"
    assert store.count_events(mission_id, {"stage_report"}) == 0


@pytest.mark.parametrize("field", ["evidence", "risks"])
def test_duplicate_canonical_sequence_entries_are_rejected(
    tmp_path: Path, field: str
) -> None:
    db_path = tmp_path / f"duplicate-{field}.db"
    store, mission_id, revision = _create_mission(db_path)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(
            **_request(db_path, mission_id, revision, **{field: ["same", " same "]})
        )

    assert raised.value.code == "request_invalid"
    assert store.count_events(mission_id, {"stage_report"}) == 0


def test_missing_store_path_does_not_create_filesystem_entries(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-parent" / "typo.db"

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, "missing", 0))

    assert raised.value.code == "mission_not_found"
    assert not db_path.parent.exists()
    assert not db_path.exists()


def test_malformed_named_unique_indexes_and_duplicate_replay_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "malformed-index.db"
    store, mission_id, revision = _create_mission(db_path)
    request = _request(db_path, mission_id, revision)
    first = append_mission_stage_report(**request)
    with store._connect() as conn:
        conn.execute("DROP INDEX idx_mission_events_idempotency")
        conn.execute("DROP INDEX idx_mission_events_revision")
        conn.execute(
            "CREATE INDEX idx_mission_events_idempotency "
            "ON mission_events(mission_id, idempotency_key)"
        )
        conn.execute(
            "CREATE INDEX idx_mission_events_revision "
            "ON mission_events(mission_id, mission_revision)"
        )
        conn.execute(
            """
            INSERT INTO mission_events (
                event_id, mission_id, event_type, summary, payload_json, created_at,
                idempotency_key, request_digest, mission_revision
            )
            SELECT ?, mission_id, event_type, summary, payload_json, created_at,
                   idempotency_key, request_digest, mission_revision
            FROM mission_events WHERE event_id = ?
            """,
            ("duplicate-event", first["event"]["event_id"]),
        )

    with pytest.raises(RuntimeError, match="mission_schema_mismatch"):
        MissionStore(db_path)
    with pytest.raises(MissionStageReportError) as adapter_error:
        append_mission_stage_report(**request)
    assert adapter_error.value.code == "durability_unknown"
    with pytest.raises(RuntimeError, match="readback_mismatch"):
        store.append_stage_report_idempotent(
            mission_id,
            expected_revision=revision,
            idempotency_key=request["idempotency_key"],
            request_digest=first["request_digest"],
            summary=request["summary"],
            payload={
                "evidence": list(request["evidence"]),
                "risks": list(request["risks"]),
                "next_step": request["next_step"],
                "needs_user": request["needs_user"],
            },
        )


def test_unresolvable_path_and_unbounded_revision_have_stable_codes(tmp_path: Path) -> None:
    _, mission_id, revision = _create_mission(tmp_path / "missions.db")
    cases = [
        ({"mission_store_path": "~hgwk-user-that-does-not-exist-4d/db.sqlite"}, "request_invalid"),
        ({"mission_store_path": "invalid\x00sqlite-path"}, "request_invalid"),
        ({"expected_revision": 2**63 - 1}, "request_invalid"),
    ]
    for overrides, code in cases:
        request = _request(tmp_path / "missions.db", mission_id, revision)
        request.update(overrides)
        with pytest.raises(MissionStageReportError) as raised:
            append_mission_stage_report(**request)
        assert raised.value.code == code


def test_path_resolution_exception_details_and_chain_are_not_exposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "missions.db"
    _, mission_id, revision = _create_mission(db_path)

    def fail_expanduser(self: Path) -> Path:
        raise OSError("sensitive-path-detail")

    monkeypatch.setattr(Path, "expanduser", fail_expanduser)
    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert raised.value.code == "request_invalid"
    assert str(raised.value) == "request_invalid: mission_store_path is not resolvable"
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    formatted = "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
    assert "sensitive-path-detail" not in formatted
    assert "OSError" not in formatted


def test_type_changed_boolean_readback_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    append_mission_stage_report(**_request(db_path, mission_id, revision))
    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_id, payload_json FROM mission_events WHERE mission_id = ? AND event_type = 'stage_report'",
            (mission_id,),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["needs_user"] = 0
        conn.execute(
            "UPDATE mission_events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(payload), row["event_id"]),
        )

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))
    assert raised.value.code == "readback_mismatch"


@pytest.mark.parametrize("column", ["event_id", "created_at", "mission_revision"])
def test_malformed_durable_event_envelope_fails_with_stable_readback_code(
    tmp_path: Path, column: str
) -> None:
    db_path = tmp_path / f"{column}.db"
    store, mission_id, revision = _create_mission(db_path)
    append_mission_stage_report(**_request(db_path, mission_id, revision))
    with store._connect() as conn:
        if column == "event_id":
            conn.execute(
                "UPDATE mission_events SET event_id = ? WHERE mission_id = ? AND event_type = 'stage_report'",
                (sqlite3.Binary(b"malformed-event-id"), mission_id),
            )
        elif column == "created_at":
            conn.execute(
                "UPDATE mission_events SET created_at = ? WHERE mission_id = ? AND event_type = 'stage_report'",
                (float("inf"), mission_id),
            )
        else:
            conn.execute(
                "UPDATE mission_events SET mission_revision = ? WHERE mission_id = ? AND event_type = 'stage_report'",
                (sqlite3.Binary(b"malformed-revision"), mission_id),
            )

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))
    assert raised.value.code == "readback_mismatch"


def test_malformed_openable_schema_row_has_stable_storage_error(tmp_path: Path) -> None:
    db_path = tmp_path / "malformed-openable.db"
    store, mission_id, revision = _create_mission(db_path)
    request = _request(db_path, mission_id, revision)
    append_mission_stage_report(**request)
    with store._connect() as conn:
        conn.execute("ALTER TABLE mission_events DROP COLUMN summary")
        conn.execute("ALTER TABLE mission_events DROP COLUMN payload_json")

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**request)

    assert raised.value.code == "durability_unknown"
    assert store.count_events(mission_id, {"stage_report"}) == 1


def _assert_backend_exception_details_are_not_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_error: Exception,
) -> None:
    db_path = tmp_path / "backend-error.db"
    _, mission_id, revision = _create_mission(db_path)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise backend_error

    monkeypatch.setattr(MissionStore, "append_stage_report_idempotent", fail)
    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert raised.value.code == "durability_unknown"
    assert str(raised.value) == (
        "durability_unknown: mission store operation outcome is unknown"
    )
    assert "sensitive-backend-detail" not in str(raised.value)
    assert type(backend_error).__name__ not in str(raised.value)
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    formatted = "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
    assert "sensitive-backend-detail" not in formatted
    assert type(backend_error).__name__ not in formatted


@pytest.mark.parametrize(
    "backend_error",
    [
        sqlite3.DatabaseError("sensitive-backend-detail"),
        OSError("sensitive-backend-detail"),
        RuntimeError("sensitive-backend-detail"),
        IndexError("sensitive-backend-detail"),
        ValueError("sensitive-backend-detail"),
    ],
    ids=["sqlite", "os", "runtime", "malformed-state", "unexpected-value"],
)
def test_backend_exception_details_are_not_exposed_parametrized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_error: Exception,
) -> None:
    _assert_backend_exception_details_are_not_exposed(
        tmp_path, monkeypatch, backend_error
    )


def test_invalid_requests_and_missing_mission_have_stable_codes(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    _, mission_id, revision = _create_mission(db_path)
    cases = [
        ({"expected_revision": True}, "request_invalid"),
        ({"expected_revision": -1}, "request_invalid"),
        ({"mission_id": "m" * 257}, "request_invalid"),
        ({"idempotency_key": "k" * 513}, "request_invalid"),
        ({"summary": "s" * 4097}, "request_invalid"),
        ({"next_step": "n" * 4097}, "request_invalid"),
        ({"evidence": "not-a-sequence-list"}, "request_invalid"),
        ({"evidence": ["item"] * 257}, "request_invalid"),
        ({"evidence": [object()]}, "request_invalid"),
        ({"risks": [object()]}, "request_invalid"),
        ({"needs_user": 1}, "request_invalid"),
        ({"mission_id": "missing"}, "mission_not_found"),
    ]
    for overrides, code in cases:
        request = _request(db_path, mission_id, revision)
        request.update(overrides)
        with pytest.raises(MissionStageReportError) as raised:
            append_mission_stage_report(**request)
        assert raised.value.code == code


def test_corrupted_idempotent_readback_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    append_mission_stage_report(**_request(db_path, mission_id, revision))
    with store._connect() as conn:
        conn.execute(
            "UPDATE mission_events SET payload_json = ? WHERE mission_id = ? AND event_type = 'stage_report'",
            ('{"evidence":["tampered"],"risks":[],"next_step":"","needs_user":false}', mission_id),
        )
    before = db_path.read_bytes()

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert raised.value.code == "readback_mismatch"
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("fault", ["begin", "select", "insert", "update", "commit"])
def test_precommit_sql_faults_are_unknown_and_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    db_path = tmp_path / f"{fault}.db"
    store, mission_id, revision = _create_mission(db_path)
    before = store.get_mission(mission_id)
    _install_transaction_fault(monkeypatch, fault)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert raised.value.code == "durability_unknown"
    assert store.count_events(mission_id, {"stage_report"}) == 0
    assert store.mission_revision(mission_id) == revision
    after = store.get_mission(mission_id)
    assert before is not None and after is not None
    assert (after.status, after.stage, after.updated_at) == (
        before.status,
        before.stage,
        before.updated_at,
    )


def test_commit_after_durability_is_unknown_and_exact_replay_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "commit-after.db"
    store, mission_id, revision = _create_mission(db_path)
    request = _request(db_path, mission_id, revision)
    _install_transaction_fault(monkeypatch, "commit_after")

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**request)

    assert raised.value.code == "durability_unknown"
    assert "sensitive post-commit detail" not in str(raised.value)
    assert store.count_events(mission_id, {"stage_report"}) == 1
    assert store.mission_revision(mission_id) == revision + 1
    durable = [
        event
        for event in store.list_events(mission_id)
        if event["event_type"] == "stage_report"
    ]
    assert len(durable) == 1

    replay = append_mission_stage_report(**request)

    assert replay["event"]["event_id"] == durable[0]["event_id"]
    assert replay["mission_revision"] == revision + 1
    assert store.count_events(mission_id, {"stage_report"}) == 1


def test_actual_postcommit_sql_readback_failure_is_unknown_and_replay_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "readback.db"
    store, mission_id, revision = _create_mission(db_path)
    _install_transaction_fault(monkeypatch, "readback")

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert raised.value.code == "durability_unknown"
    assert store.count_events(mission_id, {"stage_report"}) == 1
    monkeypatch.undo()
    recovered = append_mission_stage_report(**_request(db_path, mission_id, revision))
    assert recovered["mission_revision"] == revision + 1
    assert store.count_events(mission_id, {"stage_report"}) == 1


def test_post_commit_observation_failure_is_unknown_and_replay_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    original = MissionStore.append_stage_report_idempotent
    calls = 0

    def append_then_lose_observation(self: MissionStore, *args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        event = original(self, *args, **kwargs)
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("injected post-commit observation failure")
        return event

    monkeypatch.setattr(MissionStore, "append_stage_report_idempotent", append_then_lose_observation)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))
    assert raised.value.code == "durability_unknown"
    assert store.count_events(mission_id, {"stage_report"}) == 1

    recovered = append_mission_stage_report(**_request(db_path, mission_id, revision))
    assert recovered["mission_revision"] == revision + 1
    assert store.count_events(mission_id, {"stage_report"}) == 1


def test_adapter_maps_precommit_sqlite_failure_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)

    def fail_before_write(self: MissionStore, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise sqlite3.OperationalError("injected begin failure")

    monkeypatch.setattr(MissionStore, "append_stage_report_idempotent", fail_before_write)

    with pytest.raises(MissionStageReportError) as raised:
        append_mission_stage_report(**_request(db_path, mission_id, revision))
    assert raised.value.code == "durability_unknown"
    assert store.count_events(mission_id, {"stage_report"}) == 0


def test_cleanup_failure_does_not_override_classified_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    store.append_event(mission_id, "tick_dry_run", "advance", {}, enqueue_flow=False)
    raw = store._connect()

    class CloseFailConnection:
        @property
        def in_transaction(self) -> bool:
            return raw.in_transaction

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return raw.execute(*args, **kwargs)

        def commit(self) -> None:
            raw.commit()

        def rollback(self) -> None:
            raw.rollback()

        def close(self) -> None:
            raw.close()
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(store, "_connect", lambda: CloseFailConnection())

    with pytest.raises(ValueError, match="revision_conflict"):
        store.append_stage_report_idempotent(
            mission_id,
            expected_revision=revision,
            idempotency_key="cleanup-conflict",
            request_digest="0" * 64,
            summary="must not append",
            payload={"evidence": [], "risks": [], "next_step": "", "needs_user": False},
        )


def test_adapter_does_not_touch_cron_gateway_or_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hermes-home"
    sentinel_paths = [
        hermes_home / "cron" / "jobs.json",
        hermes_home / "gateway" / "state.json",
        tmp_path / "unrelated.txt",
    ]
    for path in sentinel_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"sentinel:{path.name}", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    before = {path: path.read_bytes() for path in sentinel_paths}
    db_path = tmp_path / "isolated" / "missions.db"
    store, mission_id, revision = _create_mission(db_path)

    append_mission_stage_report(**_request(db_path, mission_id, revision))

    assert {path: path.read_bytes() for path in sentinel_paths} == before
    assert {path.name for path in db_path.parent.iterdir()} == {"missions.db"}
    assert store.pending_flow_signals(mission_id) == []


def test_returned_result_is_recursively_immutable(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store, mission_id, revision = _create_mission(db_path)
    result = append_mission_stage_report(**_request(db_path, mission_id, revision))
    durable = store.list_events_by_type(mission_id, "stage_report")[0]

    with pytest.raises(TypeError):
        result["mission_id"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        result["event"]["payload"]["evidence"][0] = "changed"

    assert store.list_events_by_type(mission_id, "stage_report")[0] == durable
