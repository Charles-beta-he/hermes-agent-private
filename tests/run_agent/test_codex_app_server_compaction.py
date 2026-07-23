import threading
from types import SimpleNamespace

import pytest

from agent.codex_runtime import _record_codex_app_server_compaction
from agent.conversation_compression import COMPACTION_STATUS, compress_context
from agent.iteration_budget import IterationBudget
from agent.transports.codex_app_server_session import CodexAppServerSession, TurnResult


def test_codex_compaction_startup_failure_does_not_charge_dispatch():
    class StartupFailureSession(CodexAppServerSession):
        def ensure_started(self):
            raise TimeoutError("startup")

        def _format_error_with_stderr(
            self, prefix, exc="", *, tail_lines=20
        ):
            return f"{prefix}: {exc}"

    session = object.__new__(StartupFailureSession)
    admissions = []

    result = session.compact_thread(
        dispatch_gate=lambda: admissions.append("charged") or True
    )

    assert result.should_retire is True
    assert result.error is not None
    assert "startup failed" in result.error
    assert admissions == []


class FakeCodexSession:
    def __init__(self, result, *, close_result=None):
        self.result = result
        self.close_result = close_result
        self.calls = 0
        self.closed = False

    def compact_thread(self, *, dispatch_gate=None):
        if dispatch_gate is not None and not dispatch_gate():
            return TurnResult(error="graph budget exhausted")
        self.calls += 1
        return self.result

    def close(self):
        self.closed = True
        return self.close_result


class DummyAgent:
    def __init__(
        self,
        result,
        *,
        auto_compaction="native",
    ):
        self.api_mode = "codex_app_server"
        self.codex_app_server_auto_compaction = auto_compaction
        self.session_id = "hermes-session-1"
        self.platform = "cli"
        self._cached_system_prompt = "cached prompt"
        self._codex_session = FakeCodexSession(result)
        self._codex_session_retirement_pending = False
        self.context_compressor = SimpleNamespace(
            compression_count=0,
            last_compression_rough_tokens=0,
            last_prompt_tokens=123,
            last_completion_tokens=45,
            awaiting_real_usage_after_compression=False,
        )
        self.statuses = []
        self.warnings = []
        self.events = []
        self.built_prompts = []
        self.session_api_calls = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._session_db = None

    def _emit_status(self, message):
        self.statuses.append(message)

    def _emit_warning(self, message):
        self.warnings.append(message)

    def _build_system_prompt(self, system_message):
        self.built_prompts.append(system_message)
        return "built prompt"

    def event_callback(self, name, payload):
        self.events.append((name, payload))


def test_codex_app_server_native_auto_mode_leaves_thread_compaction_to_codex():
    agent = DummyAgent(
        TurnResult(thread_id="thread-1", turn_id="compact-turn-1")
    )
    messages = [{"role": "user", "content": "hi"}]

    returned, prompt = compress_context(
        agent,
        messages,
        "system",
        approx_tokens=100000,
        task_id="test",
    )

    assert returned is messages
    assert prompt == "cached prompt"
    assert agent._codex_session.calls == 0
    assert agent.context_compressor.compression_count == 0
    assert agent.events == []


def test_codex_app_server_manual_compression_routes_to_codex_thread():
    agent = DummyAgent(
        TurnResult(thread_id="thread-1", turn_id="compact-turn-1")
    )
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    returned, prompt = compress_context(
        agent,
        messages,
        "system",
        approx_tokens=100000,
        task_id="test",
        force=True,
    )

    assert returned is messages
    assert prompt == "cached prompt"
    assert agent._codex_session.calls == 1
    assert agent.context_compressor.compression_count == 1
    assert agent.context_compressor.last_compression_rough_tokens == 100000
    assert agent.context_compressor.last_prompt_tokens == -1
    assert agent.context_compressor.last_completion_tokens == 0
    assert agent.context_compressor.awaiting_real_usage_after_compression is True
    assert agent.events == [
        (
            "session:compress",
            {
                "platform": "cli",
                "session_id": "hermes-session-1",
                "old_session_id": "",
                "in_place": False,
                "compression_count": 1,
                "runtime": "codex_app_server",
                "thread_id": "thread-1",
                "turn_id": "compact-turn-1",
            },
        )
    ]


def test_codex_app_server_compaction_charges_before_rpc():
    agent = DummyAgent(
        TurnResult(thread_id="thread-1", turn_id="compact-turn-1")
    )
    graph = IterationBudget(1)
    setattr(agent, "graph_budget", graph)
    setattr(agent, "_last_budget_reservation", (False, False))

    compress_context(
        agent,
        [{"role": "user", "content": "hi"}],
        "system",
        approx_tokens=100000,
        force=True,
    )
    assert graph.used == 1
    assert agent._codex_session.calls == 1

    returned, _ = compress_context(
        agent,
        [{"role": "user", "content": "again"}],
        "system",
        approx_tokens=100000,
        force=True,
    )
    assert returned == [{"role": "user", "content": "again"}]
    assert any("graph budget exhausted" in warning for warning in agent.warnings)
    assert graph.used == 1
    assert agent._codex_session.calls == 1


def test_codex_app_server_hermes_mode_auto_compression_routes_to_codex_thread():
    agent = DummyAgent(
        TurnResult(thread_id="thread-1", turn_id="compact-turn-1"),
        auto_compaction="hermes",
    )
    messages = [{"role": "user", "content": "hi"}]

    returned, prompt = compress_context(
        agent,
        messages,
        "system",
        approx_tokens=100000,
    )

    assert returned is messages
    assert prompt == "cached prompt"
    assert agent._codex_session.calls == 1
    assert agent.context_compressor.compression_count == 1


def test_codex_app_server_compression_failure_preserves_bookkeeping():
    agent = DummyAgent(TurnResult(error="compact failed"))
    messages = [{"role": "user", "content": "hi"}]

    returned, prompt = compress_context(
        agent,
        messages,
        "system",
        approx_tokens=100000,
        force=True,
    )

    assert returned is messages
    assert prompt == "cached prompt"
    assert agent._codex_session.calls == 1
    assert agent.context_compressor.compression_count == 0
    assert agent.context_compressor.last_prompt_tokens == 123
    assert agent.warnings


def test_codex_app_server_compaction_retirement_failure_retains_retry_ownership():
    agent = DummyAgent(
        TurnResult(
            thread_id="thread-1",
            turn_id="compact-turn-1",
            should_retire=True,
        )
    )
    session = agent._codex_session
    session.close_result = False

    returned, prompt = compress_context(
        agent,
        [{"role": "user", "content": "hi"}],
        "system",
        approx_tokens=100000,
        task_id="test",
        force=True,
    )

    assert returned == [{"role": "user", "content": "hi"}]
    assert prompt == "cached prompt"
    assert agent._codex_session is session
    assert agent._codex_session_retirement_pending is True


def test_codex_app_server_compaction_holds_session_ownership_lock():
    from agent.codex_runtime import _codex_agent_session_lock

    agent = DummyAgent(
        TurnResult(thread_id="thread-1", turn_id="compact-turn-1")
    )
    compact_entered = threading.Event()
    allow_compact_return = threading.Event()
    close_entered = threading.Event()

    session = agent._codex_session

    def blocking_compact_thread(*, dispatch_gate=None):
        assert dispatch_gate is None or dispatch_gate()
        compact_entered.set()
        assert allow_compact_return.wait(timeout=2)
        return session.result

    session.compact_thread = blocking_compact_thread

    compact_worker = threading.Thread(
        target=compress_context,
        args=(agent, [{"role": "user", "content": "hi"}], "system"),
        kwargs={"approx_tokens": 100000, "task_id": "test", "force": True},
    )

    def close_under_ownership_lock():
        with _codex_agent_session_lock(agent):
            close_entered.set()
            setattr(agent, "_codex_session", None)

    compact_worker.start()
    assert compact_entered.wait(timeout=2)
    close_worker = threading.Thread(target=close_under_ownership_lock)
    close_worker.start()
    try:
        assert not close_entered.wait(timeout=0.1)
    finally:
        allow_compact_return.set()
        compact_worker.join(timeout=2)
        close_worker.join(timeout=2)

    assert not compact_worker.is_alive()
    assert not close_worker.is_alive()
    assert close_entered.is_set()
    assert agent._codex_session is None


def test_codex_app_server_native_compaction_notice_emits_status_and_event():
    agent = DummyAgent(
        TurnResult(thread_id="thread-1", turn_id="normal-turn-1")
    )
    turn = TurnResult(
        thread_id="thread-1",
        turn_id="normal-turn-1",
        compacted=True,
    )

    recorded = _record_codex_app_server_compaction(agent, turn)

    assert recorded is True
    assert agent.context_compressor.compression_count == 1
    assert agent.statuses == [COMPACTION_STATUS]
    assert agent.events == [
        (
            "session:compress",
            {
                "platform": "cli",
                "session_id": "hermes-session-1",
                "old_session_id": "",
                "in_place": False,
                "compression_count": 1,
                "runtime": "codex_app_server",
                "thread_id": "thread-1",
                "turn_id": "normal-turn-1",
            },
        )
    ]
