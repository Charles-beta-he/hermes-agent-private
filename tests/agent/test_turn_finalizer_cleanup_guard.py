"""Regression test for #8049.

When the post-loop cleanup chain in ``finalize_turn`` raises — trajectory
save (file I/O), resource teardown (remote VM/browser), or session
persistence (SQLite) — the partial ``final_response`` the caller is waiting
for must still be returned.  Previously any of those raised straight out of
``run_conversation``, so a subprocess wrapper saw an empty stdout with no
traceback and lost the whole turn.
"""

import pytest

from agent.turn_finalizer import finalize_turn


class _StubBudget:
    used = 5
    max_total = 3
    remaining = 0


class _StubCompressor:
    last_prompt_tokens = 0


class _StubAgent:
    """Minimal agent surface that ``finalize_turn`` reads from."""

    def __init__(self, *, raise_in):
        self._raise_in = set(raise_in)
        self.max_iterations = 3
        self.iteration_budget = _StubBudget()
        self.context_compressor = _StubCompressor()
        self.model = "stub/model"
        self.provider = "stub"
        self.base_url = "http://stub"
        self.session_id = "sess-1"
        self.quiet_mode = True
        self.platform = "cli"
        self._interrupt_requested = False
        self._interrupt_message = None
        self._tool_guardrail_halt_decision = None
        self._response_was_previewed = False
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        for attr in (
            "session_input_tokens",
            "session_output_tokens",
            "session_cache_read_tokens",
            "session_cache_write_tokens",
            "session_reasoning_tokens",
            "session_prompt_tokens",
            "session_completion_tokens",
            "session_total_tokens",
            "session_estimated_cost_usd",
        ):
            setattr(self, attr, 0)
        self.session_cost_status = "ok"
        self.session_cost_source = "stub"

    # --- fallible cleanup surfaces -------------------------------------
    def _save_trajectory(self, *a, **k):
        if "save_trajectory" in self._raise_in:
            raise RuntimeError("trajectory disk full")

    def _cleanup_task_resources(self, *a, **k):
        if "cleanup_task_resources" in self._raise_in:
            raise RuntimeError("docker teardown EOF")

    def _drop_trailing_empty_response_scaffolding(self, *a, **k):
        pass

    def _persist_session(self, *a, **k):
        if "persist_session" in self._raise_in:
            raise RuntimeError("sqlite database is locked")

    # --- harmless no-ops ------------------------------------------------
    def _emit_status(self, *a, **k):
        pass

    def _safe_print(self, *a, **k):
        pass

    def _handle_max_iterations(self, messages, n):
        return "PARTIAL SUMMARY FROM MODEL"

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return False

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **k):
        pass


def _run(
    agent,
    *,
    final_response=None,
    api_call_count=3,
    turn_exit_reason="unknown",
):
    messages = [
        {"role": "user", "content": "do a thing"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
    ]
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=api_call_count,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=None,
        effective_task_id="task-1",
        turn_id="turn-1",
        user_message="do a thing",
        original_user_message="do a thing",
        _should_review_memory=False,
        _turn_exit_reason=turn_exit_reason,
    )


def test_all_cleanup_steps_raise_response_still_returned():
    agent = _StubAgent(
        raise_in=("save_trajectory", "cleanup_task_resources", "persist_session")
    )
    result = _run(agent)
    assert result["final_response"] == "PARTIAL SUMMARY FROM MODEL"
    labels = [e.split(":")[0] for e in result["cleanup_errors"]]
    assert labels == ["save_trajectory", "cleanup_task_resources", "persist_session"]


@pytest.mark.parametrize(
    "step", ["save_trajectory", "cleanup_task_resources", "persist_session"]
)
def test_single_cleanup_step_raises_does_not_skip_others(step):
    agent = _StubAgent(raise_in=(step,))
    result = _run(agent)
    # Response survives.
    assert result["final_response"] == "PARTIAL SUMMARY FROM MODEL"
    # Exactly the failing step is recorded; the others ran without error.
    assert result["cleanup_errors"] == [
        next(
            e
            for e in result["cleanup_errors"]
            if e.startswith(step)
        )
    ]
    assert len(result["cleanup_errors"]) == 1


def test_clean_turn_has_no_cleanup_errors_key():
    agent = _StubAgent(raise_in=())
    result = _run(agent)
    assert result["final_response"] == "PARTIAL SUMMARY FROM MODEL"
    assert result["completed"] is False
    assert "cleanup_errors" not in result


def test_finalizer_passes_structured_todo_continuation_contract(monkeypatch):
    import agent.conversation_loop as conversation_loop
    from tools.todo_tool import TodoStore

    captured = {}

    def capture_reason(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(conversation_loop, "_self_advance_checkpoint_reason", capture_reason)
    agent = _StubAgent(raise_in=())
    store = TodoStore()
    setattr(agent, "_todo_store", store)
    store.write([
        {"id": "next", "content": "Next", "status": "pending"},
    ])
    store.set_continuation_mode("whole_goal")

    _run(agent, final_response="report", api_call_count=1)

    assert captured["continuation_mode"] == "whole_goal"
    assert captured["todo_items"] == [
        {"id": "next", "content": "Next", "status": "pending"},
    ]


def test_text_response_on_last_allowed_call_is_completed():
    agent = _StubAgent(raise_in=())
    result = _run(
        agent,
        final_response="final report",
        api_call_count=agent.max_iterations,
        turn_exit_reason="text_response(finish_reason=stop)",
    )
    assert result["final_response"] == "final report"
    assert result["completed"] is True


def test_result_surfaces_finalized_image_intent_shadow_readback():
    from agent.image_intent_routing import run_image_intent_shadow

    class _Provider:
        name = "fake"

        def capabilities(self):
            return {"modalities": ["text"], "max_reference_images": 0}

    agent = _StubAgent(raise_in=())
    agent._last_image_intent_shadow = run_image_intent_shadow(
        user_message="生成一张图片",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        provider=_Provider(),
        turn_id="turn-1",
    )
    result = _run(
        agent,
        final_response="final report",
        api_call_count=1,
        turn_exit_reason="text_response(finish_reason=stop)",
    )
    shadow = result["image_intent_shadow"]
    assert shadow["status"] == "finalized"
    assert shadow["runtime_mutation_authorized"] is False
    assert shadow["alignment"]["status"] == "conflict"
    assert shadow["alignment"]["production_gate"] == "block"


def test_normal_finalizer_surfaces_readback_when_config_reload_fails(monkeypatch):
    from agent.image_intent_routing import run_image_intent_shadow

    agent = _StubAgent(raise_in=())
    agent._last_image_intent_shadow = run_image_intent_shadow(
        user_message="生成一张图片",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        capabilities={
            "provider": "fake",
            "model": "fake-v1",
            "modalities": ["text"],
            "max_reference_images": 0,
        },
        turn_id="turn-1",
    )
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )

    result = _run(
        agent,
        final_response="final report",
        api_call_count=1,
        turn_exit_reason="text_response(finish_reason=stop)",
    )

    assert result["image_intent_shadow"]["status"] == "finalized"
    assert agent._last_image_intent_shadow["status"] == "finalized"


def test_run_conversation_wrapper_finalizes_direct_return_shadow(monkeypatch):
    import agent.conversation_loop as conversation_loop
    from agent.image_intent_routing import run_image_intent_shadow

    class _DirectReturnAgent:
        pass

    agent = _DirectReturnAgent()

    def _direct_return(*args, **kwargs):
        agent._last_image_intent_shadow = run_image_intent_shadow(
            user_message="生成一张图片",
            config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
            image_tool_available=True,
            capabilities={
                "provider": "fake",
                "model": "fake-v1",
                "modalities": ["text"],
                "max_reference_images": 0,
            },
            turn_id="direct-return",
        )
        agent._last_image_intent_shadow_turn_id = "direct-return"
        return {
            "completed": False,
            "messages": [
                {"role": "user", "content": "生成一张图片"},
                {"role": "assistant", "content": "blocked before normal finalizer"},
            ],
        }

    monkeypatch.setattr(conversation_loop, "_run_conversation_impl", _direct_return)
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {"image_intent_routing": {"enabled": True, "mode": "shadow"}},
    )

    result = conversation_loop.run_conversation(agent, "生成一张图片")

    assert result["image_intent_shadow"]["status"] == "finalized"
    assert result["image_intent_shadow"]["alignment"]["production_gate"] == "block"
    assert agent._last_image_intent_shadow == result["image_intent_shadow"]


def test_run_conversation_wrapper_drops_receipt_from_previous_invocation(monkeypatch):
    import agent.conversation_loop as conversation_loop
    from agent.image_intent_routing import run_image_intent_shadow

    class _DirectReturnAgent:
        pass

    agent = _DirectReturnAgent()
    agent._last_image_intent_shadow = run_image_intent_shadow(
        user_message="旧回合：生成一张图片",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        capabilities={
            "provider": "fake",
            "model": "fake-v1",
            "modalities": ["text"],
            "max_reference_images": 0,
        },
        turn_id="old-turn",
    )
    agent._last_image_intent_shadow_turn_id = "old-turn"
    monkeypatch.setattr(
        conversation_loop,
        "_run_conversation_impl",
        lambda *args, **kwargs: {
            "completed": False,
            "messages": [{"role": "user", "content": "新回合：解释图片格式"}],
        },
    )

    result = conversation_loop.run_conversation(agent, "新回合：解释图片格式")

    assert "image_intent_shadow" not in result
    assert not hasattr(agent, "_last_image_intent_shadow")
    assert not hasattr(agent, "_last_image_intent_shadow_turn_id")


def test_run_conversation_wrapper_finalizes_when_config_reload_fails(monkeypatch):
    import agent.conversation_loop as conversation_loop
    from agent.image_intent_routing import run_image_intent_shadow

    class _DirectReturnAgent:
        pass

    agent = _DirectReturnAgent()

    def _direct_return(*args, **kwargs):
        agent._last_image_intent_shadow = run_image_intent_shadow(
            user_message="生成一张图片",
            config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
            image_tool_available=True,
            capabilities={
                "provider": "fake",
                "model": "fake-v1",
                "modalities": ["text"],
                "max_reference_images": 0,
            },
            turn_id="direct-return",
        )
        agent._last_image_intent_shadow_turn_id = "direct-return"
        return {
            "completed": False,
            "messages": [
                {"role": "user", "content": "生成一张图片"},
                {"role": "assistant", "content": "blocked before normal finalizer"},
            ],
        }

    monkeypatch.setattr(conversation_loop, "_run_conversation_impl", _direct_return)
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )

    result = conversation_loop.run_conversation(agent, "生成一张图片")

    assert result["image_intent_shadow"]["status"] == "finalized"
    assert agent._last_image_intent_shadow == result["image_intent_shadow"]
