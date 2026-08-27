"""Tests for gateway /fast support and Priority Processing routing."""

import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import gateway.run as gateway_run
from agent.task_runtime import TaskRuntimeStore
from agent.task_runtime_ingress import MAX_CONTEXT_BYTES, hydrate_bound_task_context
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
from gateway.session import SessionSource


class _CapturingAgent:
    last_init = None
    last_run = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(
        self,
        user_message,
        conversation_history=None,
        task_id=None,
        persist_user_message=None,
        persist_user_timestamp=None,
    ):
        type(self).last_run = {
            "user_message": user_message,
            "conversation_history": conversation_history,
            "task_id": task_id,
            "persist_user_message": persist_user_message,
            "persist_user_timestamp": persist_user_timestamp,
        }
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(streaming=None)
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda source: SimpleNamespace(session_id="session-1"),
        load_transcript=lambda session_id: [],
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._enrich_message_with_vision = AsyncMock(return_value="ENRICHED")
    return runner


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )


def _make_discord_auto_thread_source() -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="999",
        chat_type="thread",
        user_id="user-1",
        thread_id="999",
        parent_chat_id="100",
        auto_thread_created=True,
        auto_thread_initial_name="raw user prompt",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def test_turn_route_injects_priority_processing_without_changing_runtime():
    runner = _make_runner()
    runner._service_tier = "priority"
    runtime_kwargs = {
        "api_key": "***",
        "base_url": "https://openrouter.ai/api/v1",
        "provider": "openrouter",
        "api_mode": "chat_completions",
        "command": None,
        "args": [],
        "credential_pool": None,
    }

    route = gateway_run.GatewayRunner._resolve_turn_agent_config(runner, "hi", "gpt-5.4", runtime_kwargs)

    assert route["runtime"]["provider"] == "openrouter"
    assert route["runtime"]["api_mode"] == "chat_completions"
    assert route["request_overrides"] == {"service_tier": "priority"}


def test_turn_route_skips_priority_processing_for_unsupported_models():
    runner = _make_runner()
    runner._service_tier = "priority"
    runtime_kwargs = {
        "api_key": "***",
        "base_url": "https://openrouter.ai/api/v1",
        "provider": "openrouter",
        "api_mode": "chat_completions",
        "command": None,
        "args": [],
        "credential_pool": None,
    }

    route = gateway_run.GatewayRunner._resolve_turn_agent_config(runner, "hi", "gpt-5.3-codex", runtime_kwargs)

    assert route["request_overrides"] == {}


@pytest.mark.asyncio
async def test_handle_fast_command_persists_config(monkeypatch, tmp_path):
    runner = _make_runner()

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")

    response = await runner._handle_fast_command(_make_event("/fast fast"))

    assert "FAST" in response
    assert runner._service_tier == "priority"

    saved = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert saved["agent"]["service_tier"] == "fast"


@pytest.mark.asyncio
async def test_run_agent_passes_priority_processing_to_gateway_agent(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    runner = _make_runner()

    (tmp_path / "config.yaml").write_text("agent:\n  service_tier: fast\n", encoding="utf-8")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    # ``_load_service_tier`` was refactored to call ``_load_gateway_runtime_config``
    # (which wraps ``_load_gateway_config`` plus env-expansion).  Since the test
    # stubs ``_load_gateway_config`` to ``{}``, also stub the runtime wrapper
    # directly so the priority routing assertions still exercise the live tier.
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_runtime_config",
        lambda: {"agent": {"service_tier": "fast"}},
    )
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "***",
        },
    )

    import hermes_cli.tools_config as tools_config
    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"})

    _CapturingAgent.last_init = None
    result = await runner._run_agent(
        message="hi",
        context_prompt="",
        history=[],
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:telegram:dm:12345",
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_init["service_tier"] == "priority"
    assert _CapturingAgent.last_init["request_overrides"] == {"service_tier": "priority"}


@pytest.mark.asyncio
async def test_telegram_gateway_agent_path_hydrates_bound_task_without_adapter_send(
    monkeypatch, tmp_path
):
    class _TaskIngressAgent(_CapturingAgent):
        task_context = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.platform = kwargs.get("platform")
            self.session_id = kwargs.get("session_id")

        def run_conversation(
            self,
            user_message,
            conversation_history=None,
            task_id=None,
            persist_user_message=None,
            persist_user_timestamp=None,
        ):
            type(self).task_context = hydrate_bound_task_context(
                self,
                user_message=user_message,
                turn_id="gateway-turn-1",
            )
            return super().run_conversation(
                user_message,
                conversation_history=conversation_history,
                task_id=task_id,
                persist_user_message=persist_user_message,
                persist_user_timestamp=persist_user_timestamp,
            )

    fake_run_agent = types.ModuleType("run_agent")
    setattr(fake_run_agent, "AIAgent", _TaskIngressAgent)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store = TaskRuntimeStore(TaskRuntimeStore.default_path())
    task = store.create_task(
        title="gateway task",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    store.bind_entrypoint(
        task["task_id"],
        entrypoint="telegram",
        session_id="gateway-session-1",
    )

    runner = _make_runner()
    no_send_adapter = MagicMock()
    no_send_adapter.SUPPORTS_MESSAGE_EDITING = False
    no_send_adapter.send = AsyncMock()
    no_send_adapter.get_pending_message.return_value = None
    runner.adapters = {Platform.TELEGRAM: no_send_adapter}
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "test-model")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "test", "api_key": "not-a-real-key"},
    )
    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(
        tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"}
    )

    result = await runner._run_agent(
        message="exact telegram ingress",
        context_prompt="",
        history=[],
        source=_make_source(),
        session_id="gateway-session-1",
        session_key="agent:main:telegram:dm:12345",
    )

    assert result["final_response"] == "ok"
    assert _TaskIngressAgent.last_init is not None
    agent_init = _TaskIngressAgent.last_init
    assert agent_init["platform"] == "telegram"
    assert agent_init["session_id"] == "gateway-session-1"
    assert _TaskIngressAgent.task_context is not None
    assert task["task_id"] in _TaskIngressAgent.task_context
    assert len(_TaskIngressAgent.task_context.encode("utf-8")) <= MAX_CONTEXT_BYTES
    persisted = store.get_task(task["task_id"])
    assert persisted["source_events"][-1]["content"] == "exact telegram ingress"
    assert persisted["source_events"][-1]["entrypoint"] == "telegram"
    no_send_adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_base_delivery_rail_runs_bound_task_without_network(
    monkeypatch, tmp_path
):
    class _DeliveryAgent(_CapturingAgent):
        task_context = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.platform = kwargs.get("platform")
            self.session_id = kwargs.get("session_id")

        def run_conversation(
            self,
            user_message,
            conversation_history=None,
            task_id=None,
            persist_user_message=None,
            persist_user_timestamp=None,
        ):
            type(self).task_context = hydrate_bound_task_context(
                self, user_message=user_message, turn_id="delivery-turn-1"
            )
            return super().run_conversation(
                user_message,
                conversation_history=conversation_history,
                task_id=task_id,
                persist_user_message=persist_user_message,
                persist_user_timestamp=persist_user_timestamp,
            )

    class _NoNetworkTelegramAdapter(BasePlatformAdapter):
        def __init__(self):
            super().__init__(
                PlatformConfig(enabled=True, token="not-a-real-token", typing_indicator=False),
                Platform.TELEGRAM,
            )
            self.deliveries = []
            self.network_send_count = 0

        async def connect(self, *, is_reconnect: bool = False) -> bool:
            return True

        async def disconnect(self):
            return None

        async def send(
            self, chat_id, content="", reply_to=None, metadata=None
        ):
            self.deliveries.append(
                {
                    "chat_id": chat_id,
                    "content": content,
                    "kwargs": {"reply_to": reply_to, "metadata": metadata},
                }
            )
            return SendResult(success=True, message_id="fake-delivery-1")

        async def get_chat_info(self, chat_id):
            return {}

    fake_run_agent = types.ModuleType("run_agent")
    setattr(fake_run_agent, "AIAgent", _DeliveryAgent)
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store = TaskRuntimeStore(TaskRuntimeStore.default_path())
    task = store.create_task(
        title="delivery rail task",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    store.bind_entrypoint(
        task["task_id"],
        entrypoint="telegram",
        session_id="gateway-session-delivery",
    )

    runner = _make_runner()
    adapter = _NoNetworkTelegramAdapter()
    runner.adapters = {Platform.TELEGRAM: adapter}
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "test-model")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {"provider": "test", "api_key": "not-a-real-key"},
    )
    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(
        tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"}
    )

    async def handler(event):
        result = await runner._run_agent(
            message=event.text,
            context_prompt="",
            history=[],
            source=event.source,
            session_id="gateway-session-delivery",
            session_key="agent:main:telegram:dm:12345",
        )
        return result["final_response"]

    adapter.set_message_handler(handler)
    event = _make_event("delivery rail ingress")
    await adapter._process_message_background(
        event, "agent:main:telegram:dm:12345"
    )

    assert adapter.deliveries == [
        {
            "chat_id": "12345",
            "content": "ok",
            "kwargs": {
                "reply_to": "m1",
                "metadata": {"notify": True},
            },
        }
    ]
    assert _DeliveryAgent.task_context is not None
    assert task["task_id"] in _DeliveryAgent.task_context
    persisted = store.get_task(task["task_id"])
    assert persisted["source_events"][-1]["content"] == "delivery rail ingress"
    assert adapter.network_send_count == 0
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / "cron" / "jobs.json").exists()
    assert not (tmp_path / "gateway.json").exists()


@pytest.mark.asyncio
async def test_run_agent_passes_discord_auto_thread_title_callback(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    runner = _make_runner()
    runner._session_db = SimpleNamespace(_db=MagicMock())  # type: ignore[assignment]

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "***",
        },
    )

    import hermes_cli.tools_config as tools_config
    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"})

    with patch("agent.title_generator.maybe_auto_title") as mock_title:
        await runner._run_agent(
            message="raw user prompt",
            context_prompt="",
            history=[],
            source=_make_discord_auto_thread_source(),
            session_id="session-1",
            session_key="agent:main:discord:thread:999",
        )

    mock_title.assert_called_once()
    callback = mock_title.call_args.kwargs["title_callback"]
    with patch.object(runner, "_schedule_discord_semantic_thread_rename") as mock_schedule:
        callback("Semantic Session Title")
    mock_schedule.assert_called_once()
    assert mock_schedule.call_args.args[1] == "session-1"
    assert mock_schedule.call_args.args[2] == "Semantic Session Title"


def test_session_source_preserves_discord_auto_thread_metadata():
    source = _make_discord_auto_thread_source()

    restored = SessionSource.from_dict(source.to_dict())

    assert restored.auto_thread_created is True
    assert restored.auto_thread_initial_name == "raw user prompt"
