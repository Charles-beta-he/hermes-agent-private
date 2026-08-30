import json
from types import SimpleNamespace

from agent.conversation_loop import (
    _apply_whole_goal_continuation_directive,
    _build_auto_relay_prompt,
    _build_recurrent_task_state_hint,
    _capture_auto_relay_summary,
    _is_auto_relay_exit_reason,
    _self_advance_checkpoint_reason,
    _reset_auto_relay_count_for_root_turn,
    _should_auto_relay,
    _try_auto_relay,
    _whole_goal_continuation_directive,
    _write_auto_relay_packet,
)
from agent.iteration_budget import IterationBudget
from tools.todo_tool import TodoStore


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


def test_whole_goal_directive_classifier_is_explicit_and_revocable():
    assert _whole_goal_continuation_directive("继续推进") == "whole_goal"
    assert _whole_goal_continuation_directive("授权你执行 whole-goal 自动推进") == "whole_goal"
    assert _whole_goal_continuation_directive("停止自动推进，只汇报进度") == "manual"
    assert _whole_goal_continuation_directive("我现在撤销自动推进") == "manual"
    assert _whole_goal_continuation_directive("现在停止 whole-goal") == "manual"
    assert _whole_goal_continuation_directive("Please stop auto-advance") == "manual"
    assert _whole_goal_continuation_directive("I revoke whole-goal") == "manual"
    assert _whole_goal_continuation_directive("我只想让你汇报进度") == "manual"
    assert _whole_goal_continuation_directive("我授权你不要自动推进") == "manual"
    assert _whole_goal_continuation_directive("我允许你停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("I authorize you to stop auto-advance") == "manual"
    assert _whole_goal_continuation_directive("I authorize you not to auto-advance") == "manual"
    assert _whole_goal_continuation_directive("Actually, stop auto-advance") == "manual"
    assert _whole_goal_continuation_directive("I now revoke auto-advance") == "manual"
    assert _whole_goal_continuation_directive("cancel auto-advance") == "manual"
    assert _whole_goal_continuation_directive("turn off auto-advance") == "manual"
    assert _whole_goal_continuation_directive("end whole-goal") == "manual"
    assert _whole_goal_continuation_directive("关闭自动推进") == "manual"
    assert _whole_goal_continuation_directive("终止 whole-goal") == "manual"
    assert _whole_goal_continuation_directive("退出自动推进 loop") == "manual"
    assert _whole_goal_continuation_directive("pause auto-advance") == "manual"
    assert _whole_goal_continuation_directive("halt whole-goal") == "manual"
    assert _whole_goal_continuation_directive("suspend auto-advance") == "manual"
    assert _whole_goal_continuation_directive("abort whole-goal") == "manual"
    assert _whole_goal_continuation_directive("quit auto-advance") == "manual"
    assert _whole_goal_continuation_directive("shut off whole-goal") == "manual"
    assert _whole_goal_continuation_directive("请停止，自动推进太危险了") == "manual"
    assert _whole_goal_continuation_directive("请停止，我觉得自动推进太危险了") == "manual"
    assert _whole_goal_continuation_directive("请停止。自动推进太危险了") == "manual"
    assert _whole_goal_continuation_directive("请停止；自动推进太危险了") == "manual"
    assert _whole_goal_continuation_directive("请停止\n自动推进太危险了") == "manual"
    assert _whole_goal_continuation_directive("继续推进，请停止") == "manual"
    assert _whole_goal_continuation_directive("取消自动推进但不要停止测试") == "manual"
    assert _whole_goal_continuation_directive("please stop, auto-advance is risky") == "manual"
    assert _whole_goal_continuation_directive("please stop, i think auto-advance is risky") == "manual"
    assert _whole_goal_continuation_directive("continue whole-goal, please stop") == "manual"
    assert _whole_goal_continuation_directive("不要停止，自动推进很好") is None
    assert _whole_goal_continuation_directive("我暂时停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("我已经撤销自动推进") == "manual"
    assert _whole_goal_continuation_directive("我决定停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("现在请停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("请您停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("您停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("麻烦停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("麻烦你停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("我要求停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("Could you stop auto-advance") == "manual"
    assert _whole_goal_continuation_directive("Would you please stop whole-goal") == "manual"
    assert _whole_goal_continuation_directive("请勿自动推进") == "manual"
    assert _whole_goal_continuation_directive("请别自动推进") == "manual"
    assert _whole_goal_continuation_directive("停止自动推进，好吗？") == "manual"
    assert _whole_goal_continuation_directive("取消自动推进，是不是更安全") == "manual"
    assert _whole_goal_continuation_directive("不要再自动推进了，为什么会跑偏") == "manual"
    assert _whole_goal_continuation_directive("是否会自动推进？") is None
    assert _whole_goal_continuation_directive("之前好像有过自推进方案，是否改改能完善？") is None
    assert _whole_goal_continuation_directive("如果用户说‘授权自动推进’，会怎样？") is None
    assert _whole_goal_continuation_directive("停止自动推进会丢失进度吗？") is None
    assert _whole_goal_continuation_directive("停止自动推进会怎样") is None
    assert _whole_goal_continuation_directive("授权你自动推进会发生什么") is None
    assert _whole_goal_continuation_directive("自动推进，是什么") is None
    assert _whole_goal_continuation_directive("授权你自动推进，如何运作") is None
    assert _whole_goal_continuation_directive("停止自动推进，会不会丢进度") == "manual"
    assert _whole_goal_continuation_directive("授权自动推进不是我的意思") is None
    assert _whole_goal_continuation_directive("授权自动推进仅作示例") is None
    assert _whole_goal_continuation_directive("我授权你自动推进，纯属引用") is None
    assert _whole_goal_continuation_directive("continue whole-goal: explain this phrase") is None
    assert _whole_goal_continuation_directive("authorize you to auto-advance: this is an example") is None
    assert _whole_goal_continuation_directive("不要停止自动推进") is None
    assert _whole_goal_continuation_directive("授权自动推进吗") is None
    assert _whole_goal_continuation_directive("授权自动推进是不是表示后台运行") is None
    assert _whole_goal_continuation_directive("授权自动推进意味着什么") is None
    assert _whole_goal_continuation_directive("授权自动推进的风险") is None
    assert _whole_goal_continuation_directive("授权自动推进：这是一句示例") is None
    assert _whole_goal_continuation_directive("请解释自动推进") is None
    assert _whole_goal_continuation_directive("请您解释停止自动推进的含义") is None
    assert _whole_goal_continuation_directive("麻烦你分析停止自动推进会怎样") is None
    assert _whole_goal_continuation_directive("Please explain stop auto-advance") is None
    assert _whole_goal_continuation_directive("停止自动推进，并解释原因") == "manual"
    assert _whole_goal_continuation_directive("分析完后停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("解释一下再停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("解释一下并停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("解释清楚并停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("解释之后停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("explain and stop auto-advance") == "manual"
    assert _whole_goal_continuation_directive("无需停止自动推进并取消自动推进") == "manual"
    assert _whole_goal_continuation_directive("Assume tests pass, stop auto-advance") == "manual"
    assert _whole_goal_continuation_directive("无需停止当前测试，请取消自动推进") == "manual"
    assert _whole_goal_continuation_directive("别停止手上的活，停止自动推进") == "manual"
    assert _whole_goal_continuation_directive("extend auto-advance") is None
    assert _whole_goal_continuation_directive("他说，我授权你自动推进") is None
    assert _whole_goal_continuation_directive("我原本想说，继续推进") is None
    assert _whole_goal_continuation_directive("上一条消息是这样的，我授权你自动推进") is None
    assert _whole_goal_continuation_directive("He said, continue whole-goal") is None
    assert _whole_goal_continuation_directive("继续推进，无需停止自动推进") is None
    assert _whole_goal_continuation_directive("继续推进，但每步都先征求我同意") is None
    assert _whole_goal_continuation_directive("授权你自动推进，但先问我再执行每一步") is None
    assert _whole_goal_continuation_directive("我授权你自动推进") == "whole_goal"
    assert _whole_goal_continuation_directive(
        "授权你自动推进，不要 push"
    ) == "whole_goal"


def test_whole_goal_directive_application_separates_persistent_and_turn_state():
    store = TodoStore()
    agent = SimpleNamespace(_todo_store=store, _auto_relay_active=False)

    assert _apply_whole_goal_continuation_directive(agent, "继续推进") == "whole_goal"
    assert store.continuation_mode() == "whole_goal"
    assert agent._turn_continuation_requested is True

    assert _apply_whole_goal_continuation_directive(agent, "今天星期几？") is None
    assert store.continuation_mode() == "whole_goal"
    assert agent._turn_continuation_requested is False

    assert _apply_whole_goal_continuation_directive(
        agent, "我现在撤销自动推进，只汇报进度"
    ) == "manual"
    assert store.continuation_mode() == "manual"
    assert agent._turn_continuation_requested is False

    assert _apply_whole_goal_continuation_directive(
        agent, "授权自动推进会怎样"
    ) is None
    assert store.continuation_mode() == "manual"
    assert agent._turn_continuation_requested is False

    assert _apply_whole_goal_continuation_directive(
        agent, "我授权你自动推进"
    ) == "whole_goal"
    assert store.continuation_mode() == "whole_goal"
    assert agent._turn_continuation_requested is True

    assert _apply_whole_goal_continuation_directive(
        agent, "我授权你不要自动推进"
    ) == "manual"
    assert store.continuation_mode() == "manual"
    assert agent._turn_continuation_requested is False

    assert _apply_whole_goal_continuation_directive(agent, "继续推进") == "whole_goal"
    assert _apply_whole_goal_continuation_directive(agent, "我决定停止自动推进") == "manual"
    assert store.continuation_mode() == "manual"
    assert agent._turn_continuation_requested is False

    assert _apply_whole_goal_continuation_directive(agent, "继续推进") == "whole_goal"
    assert _apply_whole_goal_continuation_directive(agent, "I authorize you not to auto-advance") == "manual"
    assert store.continuation_mode() == "manual"
    assert agent._turn_continuation_requested is False


def test_synthetic_relay_inherits_but_cannot_create_whole_goal_mode():
    store = TodoStore()
    agent = SimpleNamespace(_todo_store=store, _auto_relay_active=True)

    assert _apply_whole_goal_continuation_directive(
        agent, "[Auto-relay] summary mentions 自动推进"
    ) is None
    assert agent._turn_continuation_requested is False

    store.set_continuation_mode("whole_goal")
    assert _apply_whole_goal_continuation_directive(agent, "[Auto-relay]") is None
    assert agent._turn_continuation_requested is False

    agent._relay_chain_continuation_requested = True
    assert _apply_whole_goal_continuation_directive(agent, "[Auto-relay]") == "whole_goal"
    assert agent._turn_continuation_requested is True


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
        turn_continuation_requested=True,
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


def test_self_advance_whole_goal_contract_requires_turn_admission():
    open_todos = [{"id": "next", "content": "Next", "status": "pending"}]

    assert _self_advance_checkpoint_reason(
        final_response="ordinary answer while older work remains open",
        continuation_mode="whole_goal",
        turn_continuation_requested=False,
        todo_items=open_todos,
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="bounded progress checkpoint",
        continuation_mode="whole_goal",
        turn_continuation_requested=True,
        todo_items=open_todos,
        interrupted=False,
    ) == "self_advance_checkpoint"


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
        turn_continuation_requested=True,
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
        continuation_mode="whole_goal",
        turn_continuation_requested=True,
        todo_items=open_todos,
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="当前有本地待办。",
        user_message="普通问答",
        original_user_message="普通问答",
        messages=[{"role": "user", "content": "普通问答"}],
        continuation_mode="whole_goal",
        turn_continuation_requested=False,
        todo_items=open_todos,
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="当前进度：下一步将 DELETE production resource。",
        user_message="继续推进",
        original_user_message="继续推进",
        messages=[{"role": "user", "content": "继续推进"}],
        continuation_mode="whole_goal",
        turn_continuation_requested=True,
        todo_items=open_todos,
        interrupted=False,
    ) is None
    assert _self_advance_checkpoint_reason(
        final_response="The next step requires a testflight approval.",
        continuation_mode="whole_goal",
        turn_continuation_requested=True,
        todo_items=open_todos,
        interrupted=False,
    ) is None
    for hard_gate in (
        "下一步需要确认。",
        "下一步等待审批。",
        "下一步等你选择。",
        "The next step needs approval.",
        "The next step requires confirmation.",
        "The next step is waiting for user input.",
        "A human decision is required.",
        "Your approval is required before deploy.",
        "需要您确认后继续。",
        "下一步将发布到生产。",
        "下一步将部署到生产。",
        "The next step is to publish the release.",
        "The next step is to deploy to production.",
        "The next step requires payment.",
        "The next step sends data to a customer.",
    ):
        assert _self_advance_checkpoint_reason(
            final_response=hard_gate,
            continuation_mode="whole_goal",
            turn_continuation_requested=True,
            todo_items=open_todos,
            interrupted=False,
        ) is None


def test_self_advance_response_wording_cannot_override_structured_contract():
    assert _self_advance_checkpoint_reason(
        final_response="Client-side deployment config and payment parser tests are complete; 支付模块测试通过。",
        continuation_mode="whole_goal",
        turn_continuation_requested=True,
        todo_items=[{"id": "b7", "content": "Continue local tests", "status": "pending"}],
        interrupted=False,
    ) == "self_advance_checkpoint"
    assert _self_advance_checkpoint_reason(
        final_response="contains any natural-language blocker or progress wording",
        continuation_mode="whole_goal",
        turn_continuation_requested=True,
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


def test_auto_relay_projects_bound_task_without_source_or_authority_revival(tmp_path, monkeypatch):
    import agent.conversation_loop as conversation_loop
    from agent.task_runtime import TaskRuntimeStore
    from agent.task_runtime_ingress import hydrate_bound_task_context

    db_path = tmp_path / "runtime.db"
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(db_path))
    monkeypatch.setenv("HERMES_RELAY_PACKET_DIR", str(tmp_path / "relay"))
    monkeypatch.setenv("HERMES_CONTINUATION_DIR", str(tmp_path / "continuations"))
    runtime_store = TaskRuntimeStore(db_path)
    task = runtime_store.create_task(
        title="Relay conversation task",
        original_sources=[
            {"source_id": "request-1", "kind": "user_message", "content": "original"}
        ],
    )
    runtime_store.bind_entrypoint(
        task["task_id"], entrypoint="cli", session_id="relay-session"
    )
    todo_store = TodoStore()
    projected = []

    def fake_run_conversation(
        agent,
        user_message,
        media,
        history,
        task_id,
        stream_callback,
        *,
        persist_user_message,
    ):
        projected.append(
            hydrate_bound_task_context(
                agent,
                user_message=persist_user_message,
                turn_id="relay-turn-1",
            )
        )
        assert _apply_whole_goal_continuation_directive(agent, user_message) is None
        return {"final_response": "successor final", "messages": history}

    monkeypatch.setattr(conversation_loop, "run_conversation", fake_run_conversation)
    agent = SimpleNamespace(
        auto_relay_enabled=True,
        _relay_count=0,
        max_relay_count=3,
        quiet_mode=True,
        _safe_print=lambda *_args, **_kwargs: None,
        session_id="relay-session",
        platform="cli",
        parent_session_id=None,
        model="m",
        provider="p",
        max_iterations=2,
        iteration_budget=IterationBudget(2),
        _budget_exhausted_injected=True,
        _budget_grace_call=True,
        _todo_store=todo_store,
        _relay_chain_continuation_requested=False,
    )

    result = _try_auto_relay(
        agent,
        final_response="summary mentions 自动推进",
        interrupted=False,
        turn_exit_reason="max_iterations_reached(2/2)",
        messages=[{"role": "user", "content": "original"}],
        api_call_count=2,
        task_id=task["task_id"],
        stream_callback=None,
    )

    assert result["final_response"] == "successor final"
    assert projected and task["task_id"] in projected[0]
    assert runtime_store.get_task(task["task_id"])["source_events"] == []
    assert todo_store.continuation_mode() == "manual"
    assert agent._turn_continuation_requested is False


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
