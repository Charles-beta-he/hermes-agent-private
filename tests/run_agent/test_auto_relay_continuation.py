import json
from types import SimpleNamespace

from agent.conversation_loop import (
    _build_auto_relay_prompt,
    _is_auto_relay_exit_reason,
    _reset_auto_relay_count_for_root_turn,
    _should_auto_relay,
    _try_auto_relay,
    _write_auto_relay_packet,
)
from agent.iteration_budget import IterationBudget


def test_auto_relay_recognizes_budget_and_max_iteration_exits():
    assert _is_auto_relay_exit_reason("budget_exhausted") is True
    assert _is_auto_relay_exit_reason("max_iterations_reached(60/60)") is True
    assert _is_auto_relay_exit_reason("text_response(finish_reason=stop)") is False


def test_should_auto_relay_requires_enabled_summary_and_capacity():
    agent = SimpleNamespace(auto_relay_enabled=True, _relay_count=0, max_relay_count=3)

    assert _should_auto_relay(
        agent,
        final_response="handoff summary",
        interrupted=False,
        turn_exit_reason="max_iterations_reached(60/60)",
    ) is True
    assert _should_auto_relay(
        agent,
        final_response="",
        interrupted=False,
        turn_exit_reason="max_iterations_reached(60/60)",
    ) is False
    agent._relay_count = 3
    assert _should_auto_relay(
        agent,
        final_response="handoff summary",
        interrupted=False,
        turn_exit_reason="max_iterations_reached(60/60)",
    ) is False


def test_reset_auto_relay_count_only_for_root_turns():
    agent = SimpleNamespace(_relay_count=3, _auto_relay_active=False)
    _reset_auto_relay_count_for_root_turn(agent)
    assert agent._relay_count == 0

    agent._relay_count = 2
    agent._auto_relay_active = True
    _reset_auto_relay_count_for_root_turn(agent)
    assert agent._relay_count == 2


def test_write_auto_relay_packet_and_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_RELAY_PACKET_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_CONTINUATION_DIR", str(tmp_path / "continuations"))
    agent = SimpleNamespace(
        _relay_count=0,
        max_relay_count=3,
        session_id="session/with spaces",
        parent_session_id="parent",
        model="m",
        provider="p",
        max_iterations=8,
        iteration_budget=IterationBudget(8),
    )
    agent.iteration_budget.consume()

    path = _write_auto_relay_packet(
        agent,
        reason="max_iterations_reached(8/8)",
        summary="summary text",
        messages=[{"role": "user", "content": "hello"}],
        api_call_count=8,
    )
    payload = json.loads(path.read_text())

    assert payload["schema"] == "hermes_continuation_packet_v1"
    assert payload["metadata"]["legacy_schema"] == "hermes_auto_relay_packet_v1"
    assert payload["reason"] == "max_iterations"
    assert payload["summary"] == "summary text"
    assert payload["limits"]["relay_index"] == 1
    assert payload["metadata"]["messages_snapshot"][-1]["content"] == "hello"

    prompt = _build_auto_relay_prompt(
        relay_index=1,
        max_relay_count=3,
        reason="max_iterations_reached(8/8)",
        summary="summary text",
    )
    assert "Auto-relay 1/3" in prompt
    assert "summary text" in prompt
    assert "Continue the task" in prompt


def test_try_auto_relay_starts_successor_turn_and_annotates_result(tmp_path, monkeypatch):
    import agent.conversation_loop as conversation_loop

    monkeypatch.setenv("HERMES_RELAY_PACKET_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_CONTINUATION_DIR", str(tmp_path / "continuations"))
    calls = []

    def fake_run_conversation(agent, user_message, media, history, task_id, stream_callback, *, persist_user_message):
        calls.append({
            "user_message": user_message,
            "media": media,
            "history": history,
            "task_id": task_id,
            "stream_callback": stream_callback,
            "persist_user_message": persist_user_message,
            "auto_relay_active": getattr(agent, "_auto_relay_active", False),
        })
        return {"final_response": "successor final"}

    monkeypatch.setattr(conversation_loop, "run_conversation", fake_run_conversation)
    agent = SimpleNamespace(
        auto_relay_enabled=True,
        _relay_count=0,
        max_relay_count=3,
        quiet_mode=True,
        _safe_print=lambda *_args, **_kwargs: None,
        session_id="relay-session",
        parent_session_id=None,
        model="m",
        provider="p",
        max_iterations=2,
        iteration_budget=IterationBudget(2),
        _budget_exhausted_injected=True,
        _budget_grace_call=True,
    )
    agent.iteration_budget.consume()
    messages = [{"role": "user", "content": "original"}]

    result = _try_auto_relay(
        agent,
        final_response="partial handoff summary",
        interrupted=False,
        turn_exit_reason="max_iterations_reached(2/2)",
        messages=messages,
        api_call_count=2,
        task_id="task-1",
        stream_callback="callback",
    )

    assert result["final_response"] == "successor final"
    assert result["auto_relay"]["triggered"] is True
    assert result["auto_relay"]["relay_index"] == 1
    assert result["auto_relay"]["parent_turn_exit_reason"] == "max_iterations_reached(2/2)"
    assert calls and "Auto-relay 1/3" in calls[0]["user_message"]
    assert calls[0]["history"] == messages
    assert calls[0]["persist_user_message"] == calls[0]["user_message"]
    assert calls[0]["auto_relay_active"] is True
    assert agent._auto_relay_active is False
    assert agent._relay_count == 1
    assert agent.iteration_budget.used == 0
    assert agent._budget_exhausted_injected is False
    assert agent._budget_grace_call is False
    assert json.loads((tmp_path / "relay-relay-session-1.json").read_text())["summary"] == "partial handoff summary"
