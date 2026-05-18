from types import SimpleNamespace

from agent.chat_completion_helpers import build_api_kwargs


class DummyTransport:
    def build_kwargs(self, **kwargs):
        return kwargs


def _make_anthropic_agent(*, use_prompt_caching=True, use_native_cache_layout=True):
    tools = [
        {"name": "alpha", "description": "first"},
        {"name": "omega", "description": "last"},
    ]
    return SimpleNamespace(
        api_mode="anthropic_messages",
        model="claude-sonnet-4",
        tools=tools,
        max_tokens=1024,
        reasoning_config=None,
        _is_anthropic_oauth=False,
        _ephemeral_max_output_tokens=None,
        _use_prompt_caching=use_prompt_caching,
        _use_native_cache_layout=use_native_cache_layout,
        _cache_ttl="5m",
        context_compressor=None,
        request_overrides={},
        _anthropic_base_url=None,
        _oauth_1m_beta_disabled=False,
        _get_transport=lambda: DummyTransport(),
        _prepare_anthropic_messages_for_api=lambda messages: messages,
        _anthropic_preserve_dots=lambda: False,
    )


def test_native_anthropic_prompt_caching_marks_last_tool_without_mutating_agent_tools():
    agent = _make_anthropic_agent(use_prompt_caching=True, use_native_cache_layout=True)

    kwargs = build_api_kwargs(agent, [{"role": "user", "content": "hi"}])

    assert kwargs["tools"] is not agent.tools
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in agent.tools[-1]


def test_native_anthropic_tool_cache_marker_is_disabled_without_native_cache_layout():
    agent = _make_anthropic_agent(use_prompt_caching=True, use_native_cache_layout=False)

    kwargs = build_api_kwargs(agent, [{"role": "user", "content": "hi"}])

    assert kwargs["tools"] is agent.tools
    assert "cache_control" not in kwargs["tools"][-1]
