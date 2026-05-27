"""Regression: chatgpt.com/backend-api/codex emits ``response.completed``
with ``response.output = null``.

The OpenAI SDK's streaming accumulator parses every event eagerly; on the
completed event it runs ``parse_response`` -> ``for output in
response.output`` and raises ``TypeError: 'NoneType' object is not iterable``.
The crash happens *after* all text/output-item events have already streamed,
so ``run_codex_stream`` must salvage the turn from the data it collected
rather than failing the whole call (every codex turn would otherwise abort
with "Non-retryable client error (HTTP None)").
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_codex_agent():
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="test-key",
        base_url="https://chatgpt.com/backend-api/codex",
        model="gpt-5.5",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    agent.api_mode = "codex_responses"
    agent.provider = "openai-codex"
    agent._interrupt_requested = False
    return agent


class _CrashingStream:
    """Mimics openai's ResponseStreamManager: yields a few events, then the
    SDK's own accumulator raises while parsing the null-output completed
    event."""

    def __init__(self, events, crash_exc):
        self._events = events
        self._crash_exc = crash_exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for ev in self._events:
            yield ev
        raise self._crash_exc

    def get_final_response(self):  # pragma: no cover - must not be reached
        raise AssertionError("get_final_response should not run after the crash")


def test_salvages_streamed_text_when_sdk_crashes_on_null_output():
    agent = _make_codex_agent()
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="收到"),
        SimpleNamespace(type="response.output_text.delta", delta="，测试正常。"),
    ]
    stream = _CrashingStream(events, TypeError("'NoneType' object is not iterable"))
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = stream

    # Fallback must NOT be used — salvage avoids a second (billed) request.
    with patch.object(
        agent, "_run_codex_create_stream_fallback",
        side_effect=AssertionError("fallback should not run"),
    ):
        result = agent._run_codex_stream({"model": "gpt-5.5"}, client=mock_client)

    assert result.status == "completed"
    assert len(result.output) == 1
    assert result.output[0].content[0].text == "收到，测试正常。"
    assert result.output_text == "收到，测试正常。"


def test_salvages_collected_output_items_over_text():
    agent = _make_codex_agent()
    real_item = SimpleNamespace(type="message", role="assistant", status="completed")
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="hi"),
        SimpleNamespace(type="response.output_item.done", item=real_item),
    ]
    stream = _CrashingStream(events, TypeError("'NoneType' object is not iterable"))
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = stream

    result = agent._run_codex_stream({"model": "gpt-5.5"}, client=mock_client)

    assert result.output == [real_item]


def test_no_recoverable_output_is_not_marked_completed():
    agent = _make_codex_agent()
    stream = _CrashingStream([], TypeError("'NoneType' object is not iterable"))
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = stream

    with pytest.raises(TypeError, match="is not iterable"):
        agent._run_codex_stream({"model": "gpt-5.5"}, client=mock_client)


def test_partial_tool_call_without_output_item_is_not_marked_completed():
    agent = _make_codex_agent()
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="calling tool"),
        SimpleNamespace(type="response.function_call_arguments.delta", delta='{"x"'),
    ]
    stream = _CrashingStream(events, TypeError("'NoneType' object is not iterable"))
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = stream

    with pytest.raises(TypeError, match="is not iterable"):
        agent._run_codex_stream({"model": "gpt-5.5"}, client=mock_client)


def test_unrelated_iterable_typeerror_after_text_is_not_swallowed():
    agent = _make_codex_agent()
    events = [SimpleNamespace(type="response.output_text.delta", delta="partial")]
    stream = _CrashingStream(events, TypeError("argument of type 'NoneType' is not iterable"))
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = stream

    with pytest.raises(TypeError, match="argument of type"):
        agent._run_codex_stream({"model": "gpt-5.5"}, client=mock_client)


def test_unrelated_typeerror_is_not_swallowed():
    agent = _make_codex_agent()
    stream = _CrashingStream([], TypeError("build_kwargs() got an unexpected keyword"))
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = stream

    with pytest.raises(TypeError, match="unexpected keyword"):
        agent._run_codex_stream({"model": "gpt-5.5"}, client=mock_client)
