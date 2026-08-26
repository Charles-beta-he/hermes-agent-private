import json
import sqlite3
from types import SimpleNamespace

import pytest

from agent.task_runtime import TaskRuntimeStore
from agent.task_runtime_ingress import hydrate_bound_task_context


def test_bound_turn_records_exact_source_and_returns_bounded_projection(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(db_path))
    store = TaskRuntimeStore(db_path)
    task = store.create_task(
        title="Cross-entrypoint task",
        original_sources=[{"source_id": "request-1", "kind": "user_message", "content": "original request"}],
    )
    store.bind_entrypoint(task["task_id"], entrypoint="cli", session_id="cli-session")

    agent = SimpleNamespace(platform="cli", session_id="cli-session")
    context = hydrate_bound_task_context(
        agent,
        user_message="continue exactly from source",
        turn_id="turn-1",
    )

    assert context.startswith("[TASK_RUNTIME_CONTEXT v1]\n")
    assert context.endswith("\n[/TASK_RUNTIME_CONTEXT]")
    payload = json.loads(context.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert payload["task_id"] == task["task_id"]
    assert payload["binding"] == {"entrypoint": "cli", "session_id": "cli-session"}
    assert "sources" not in payload
    assert len(context.encode("utf-8")) <= 12_000

    reopened = TaskRuntimeStore(db_path).get_task(task["task_id"])
    events = [event for event in reopened["source_events"] if event["kind"] == "user_message"]
    assert any(event["content"] == "continue exactly from source" for event in events)
    assert any(event["turn_id"] == "turn-1" for event in events)


def test_cli_and_telegram_share_task_after_store_reconstruction(tmp_path, monkeypatch):
    from tools.registry import registry
    import tools.task_runtime_tool  # noqa: F401

    db_path = tmp_path / "runtime.db"
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(db_path))
    created = json.loads(
        registry.dispatch(
            "task_runtime",
            {
                "action": "create",
                "title": "Cross entrypoint",
                "original_sources": [
                    {"source_id": "cli-source", "kind": "user_message", "content": "build it"}
                ],
            },
            session_id="cli-e2e",
            platform="cli",
        )
    )
    assert created["success"] is True
    task_id = created["task"]["task_id"]
    registry.dispatch(
        "task_runtime",
        {
            "action": "bind",
            "task_id": task_id,
            "entrypoint": "telegram",
            "target_session_id": "tg-e2e",
        },
        session_id="cli-e2e",
        platform="cli",
    )

    cli_context = hydrate_bound_task_context(
        SimpleNamespace(platform="cli", session_id="cli-e2e"),
        user_message="cli continuation",
        turn_id="cli-turn",
    )
    tg_context = hydrate_bound_task_context(
        SimpleNamespace(platform="telegram", session_id="tg-e2e"),
        user_message="telegram continuation",
        turn_id="tg-turn",
    )
    assert task_id in cli_context
    assert task_id in tg_context

    # Simulate entrypoint process destruction by constructing a new store.
    reconstructed = TaskRuntimeStore(db_path)
    cli_binding = reconstructed.resolve_entrypoint("cli", "cli-e2e")
    tg_binding = reconstructed.resolve_entrypoint("telegram", "tg-e2e")
    assert cli_binding is not None and cli_binding["task_id"] == task_id
    assert tg_binding is not None and tg_binding["task_id"] == task_id
    events = reconstructed.get_task(task_id)["source_events"]
    assert {(event["entrypoint"], event["content"]) for event in events} >= {
        ("cli", "cli continuation"),
        ("telegram", "telegram continuation"),
    }


def test_bound_turn_replay_is_exactly_once_and_conflicts_are_rejected(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(db_path))
    store = TaskRuntimeStore(db_path)
    task = store.create_task(
        title="Exactly once ingress",
        original_sources=[{"source_id": "request-1", "kind": "user_message", "content": "original"}],
    )
    store.bind_entrypoint(task["task_id"], entrypoint="telegram", session_id="tg:42:7")
    agent = SimpleNamespace(platform="telegram", session_id="tg:42:7")

    first = hydrate_bound_task_context(agent, user_message="same turn", turn_id="update-99")
    first_state = store.get_task(task["task_id"])
    replay = hydrate_bound_task_context(agent, user_message="same turn", turn_id="update-99")
    replay_state = store.get_task(task["task_id"])

    assert first is not None and replay is not None
    assert len(replay_state["source_events"]) == 1
    assert replay_state["state_revision"] == first_state["state_revision"]

    with pytest.raises(ValueError, match="source_event_id_conflict"):
        store.append_source_event(
            task["task_id"],
            {
                "event_id": "telegram:tg:42:7:update-99",
                "source_id": "telegram:tg:42:7:update-99",
                "kind": "user_message",
                "content": "different content under same turn identity",
                "entrypoint": "telegram",
                "session_id": "tg:42:7",
                "turn_id": "update-99",
            },
        )
    assert store.get_task(task["task_id"])["source_events"][0]["content"] == "same turn"
    with store._connect() as conn:
        normalized = conn.execute(
            "SELECT event_id, payload_json FROM source_events WHERE task_id = ?",
            (task["task_id"],),
        ).fetchall()
        legacy_snapshot = conn.execute(
            "SELECT source_events_json FROM tasks WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()["source_events_json"]
    assert len(normalized) == 1
    assert json.loads(normalized[0]["payload_json"])["content"] == "same turn"
    assert legacy_snapshot == "[]"

    with pytest.raises(sqlite3.IntegrityError, match="source_events_append_only"):
        with store._write() as conn:
            conn.execute(
                "UPDATE source_events SET payload_json = '{}' WHERE task_id = ?",
                (task["task_id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="source_events_append_only"):
        with store._write() as conn:
            conn.execute("DELETE FROM source_events WHERE task_id = ?", (task["task_id"],))


def test_unbound_turn_has_no_projection_or_source_write(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.db"
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(db_path))
    agent = SimpleNamespace(platform="telegram", session_id="tg-unbound")

    assert hydrate_bound_task_context(agent, user_message="hello", turn_id="turn-2") is None
    store = TaskRuntimeStore(db_path)
    assert store.resolve_entrypoint("telegram", "tg-unbound") is None
