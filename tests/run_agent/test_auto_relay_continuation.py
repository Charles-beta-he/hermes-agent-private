import json
from types import SimpleNamespace

from agent.conversation_loop import (
    _build_auto_relay_prompt,
    _build_recurrent_task_state_hint,
    _capture_auto_relay_summary,
    _is_auto_relay_exit_reason,
    _self_advance_checkpoint_reason,
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


def test_auto_relay_suppresses_graph_terminal_reasons():
    assert _is_auto_relay_exit_reason("graph_budget_exhausted") is False
    assert _is_auto_relay_exit_reason("graph_budget_persistence_failed") is False
    assert _is_auto_relay_exit_reason("guardrail_halt") is False


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


def test_capture_auto_relay_summary_requires_authoritative_handoff():
    authoritative = SimpleNamespace(_last_iteration_summary_authoritative=True)
    synthetic = SimpleNamespace(_last_iteration_summary_authoritative=False)

    assert _capture_auto_relay_summary(authoritative, 'real handoff') == 'real handoff'
    assert _capture_auto_relay_summary(synthetic, 'I reached the iteration limit and could not summarize.') == ''


def test_build_recurrent_task_state_hint_prefers_compact_resume_state():
    agent = SimpleNamespace(task_id="task-1")

    state = _build_recurrent_task_state_hint(
        agent=agent,
        reason="max_iterations_reached(8/8)",
        summary="Run verification then report proof",
        relay_index=2,
        messages=[{"role": "user", "content": "Finish the Hermes proof bundle"}] * 9,
    )

    assert state["schema"] == "hermes_recurrent_task_state_v1"
    assert state["task_id"] == "task-1"
    assert state["attempt"] == 2
    assert state["objective"] == "Finish the Hermes proof bundle"
    assert state["remaining_steps"] == ["continue the user task from the exhausted-turn summary"]
    assert "do_not_dump_full_history_when_compact_state_covers_resume" in state["wrong_source_hypotheses"]
    assert state["token_estimate"] > 0
    assert state["max_chars"] == 2400


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
    assert payload["recurrent_task_state"]["schema"] == "hermes_recurrent_task_state_v1"
    assert payload["recurrent_task_state"]["next_bounded_action"].startswith("verify current files/state")
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
    assert "DecisionPacket-style human gate" in prompt
    assert "model agreement as authority" in prompt


def test_self_advance_structured_whole_goal_contract_requests_relay():
    response = "arbitrary progress response; wording is not part of the decision"
    assert _self_advance_checkpoint_reason(
        final_response=response,
        continuation_mode="whole_goal",
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) == "self_advance_checkpoint"


def test_self_advance_manual_contract_does_not_relay_open_work():
    assert _self_advance_checkpoint_reason(
        final_response="arbitrary response",
        continuation_mode="manual",
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None


def test_self_advance_open_todos_relay_progress_report_without_future_tense_marker():
    messages = [{"role": "user", "content": "授权，执行 whole-goal 自动推进"}]
    for index in range(8):
        messages.extend(
            [
                {"role": "assistant", "content": f"checkpoint {index}"},
                {"role": "user", "content": f"进度 {index}"},
            ]
        )

    assert _self_advance_checkpoint_reason(
        final_response=(
            "当前进度：22 项完成，4 项待办，当前无进行中任务。\n"
            "当前续接点：B8 bounded core integration。"
        ),
        user_message="进度",
        original_user_message="进度",
        messages=messages,
        continuation_mode="whole_goal",
        todo_items=[
            {"id": "b8-1", "content": "Model B8", "status": "pending"},
            {"id": "b8-2", "content": "Write RED tests", "status": "pending"},
        ],
        interrupted=False,
    ) == "self_advance_checkpoint"


def test_self_advance_open_todos_do_not_bypass_hard_gate_or_create_authority():
    open_todos = [{"id": "deploy", "content": "Deploy", "status": "pending"}]
    assert _self_advance_checkpoint_reason(
        final_response="B8 已验证；生产发布需要你授权。",
        user_message="继续推进",
        original_user_message="继续推进",
        messages=[{"role": "user", "content": "继续推进"}],
        todo_items=open_todos,
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="当前有本地待办。",
        user_message="普通问答",
        original_user_message="普通问答",
        messages=[{"role": "user", "content": "普通问答"}],
        todo_items=open_todos,
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="当前进度：下一步将 DELETE production resource。",
        user_message="继续推进",
        original_user_message="继续推进",
        messages=[{"role": "user", "content": "继续推进"}],
        todo_items=open_todos,
        interrupted=False,
    ) is None


def test_self_advance_response_wording_cannot_override_structured_contract():
    assert _self_advance_checkpoint_reason(
        final_response="contains any natural-language blocker or progress wording",
        continuation_mode="whole_goal",
        todo_items=[{"id": "b8", "content": "B8", "status": "pending"}],
        interrupted=False,
    ) == "self_advance_checkpoint"


def test_self_advance_completed_todos_do_not_relay_plain_progress_report():
    assert _self_advance_checkpoint_reason(
        final_response="全部本地目标已收敛。",
        user_message="继续推进",
        original_user_message="继续推进",
        messages=[{"role": "user", "content": "继续推进"}],
        todo_items=[{"id": "done", "content": "Done", "status": "completed"}],
        interrupted=False,
    ) is None


def test_self_advance_open_todos_respect_later_explicit_revocation():
    assert _self_advance_checkpoint_reason(
        final_response="当前仍有本地待办。",
        user_message="停止自动推进，只汇报进度",
        original_user_message="停止自动推进，只汇报进度",
        messages=[
            {"role": "user", "content": "授权，执行 whole-goal 自动推进"},
            {"role": "assistant", "content": "已开始。"},
            {"role": "user", "content": "停止自动推进，只汇报进度"},
        ],
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None


def test_self_advance_relay_prompt_cannot_regrant_revoked_authority():
    relay_prompt = _build_auto_relay_prompt(
        relay_index=1,
        max_relay_count=12,
        reason="max_iterations_reached",
        summary="earlier whole-goal 自动推进 work",
    )
    assert _self_advance_checkpoint_reason(
        final_response="当前进度：仍有待办。",
        user_message=relay_prompt,
        original_user_message=relay_prompt,
        messages=[
            {"role": "user", "content": "授权，执行 whole-goal 自动推进"},
            {"role": "user", "content": "停止自动推进，只汇报进度"},
            {"role": "user", "content": relay_prompt},
        ],
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None


def test_self_advance_stale_original_message_cannot_override_history_revocation():
    assert _self_advance_checkpoint_reason(
        final_response="当前进度：仍有待办。",
        user_message="进度",
        original_user_message="授权，执行 whole-goal 自动推进",
        messages=[
            {"role": "user", "content": "授权，执行 whole-goal 自动推进"},
            {"role": "user", "content": "停止自动推进，只汇报进度"},
            {"role": "user", "content": "进度"},
        ],
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None


def test_self_advance_question_does_not_create_authorization():
    assert _self_advance_checkpoint_reason(
        final_response="当前仍有本地待办。",
        user_message="是否自动化推进？",
        original_user_message="是否自动化推进？",
        messages=[{"role": "user", "content": "是否自动化推进？"}],
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None


def test_self_advance_open_todos_do_not_swallow_unrelated_answer():
    base_messages = [
        {"role": "user", "content": "授权，执行 whole-goal 自动推进"},
        {"role": "assistant", "content": "已开始。"},
    ]
    assert _self_advance_checkpoint_reason(
        final_response="现在是星期日。",
        user_message="今天星期几？",
        original_user_message="今天星期几？",
        messages=base_messages + [{"role": "user", "content": "今天星期几？"}],
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="还有 3 项待办。",
        user_message="还有哪些待办？",
        original_user_message="还有哪些待办？",
        messages=base_messages + [{"role": "user", "content": "还有哪些待办？"}],
        todo_items=[{"id": "next", "content": "Next", "status": "pending"}],
        interrupted=False,
    ) is None


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


def test_try_auto_relay_refuses_blank_raw_summary_even_if_rendered_explanation_exists(monkeypatch):
    import agent.conversation_loop as conversation_loop

    calls = []

    def fake_run_conversation(*_args, **_kwargs):
        calls.append((_args, _kwargs))
        return {"final_response": "should not run"}

    monkeypatch.setattr(conversation_loop, "run_conversation", fake_run_conversation)
    agent = SimpleNamespace(
        auto_relay_enabled=True,
        _relay_count=0,
        max_relay_count=3,
        quiet_mode=True,
        session_id="relay-session",
        parent_session_id=None,
        model="m",
        provider="p",
        max_iterations=2,
        iteration_budget=IterationBudget(2),
    )

    # The caller must pass the raw _handle_max_iterations summary, not the
    # later user-facing turn-completion explanation. A blank raw summary stays
    # non-relayable even if UI code later renders explanatory text.
    result = _try_auto_relay(
        agent,
        final_response="",
        interrupted=False,
        turn_exit_reason="max_iterations_reached(2/2)",
        messages=[{"role": "user", "content": "original"}],
        api_call_count=2,
        task_id="task-1",
        stream_callback=None,
    )

    assert result is None
    assert calls == []
    assert agent._relay_count == 0
