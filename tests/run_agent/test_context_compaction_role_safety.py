"""Regression tests for context compaction handoff role safety."""

from agent.context_compressor import ContextCompressor, SUMMARY_PREFIX


def _compressor() -> ContextCompressor:
    compressor = ContextCompressor(
        model="test-model",
        threshold_percent=0.5,
        quiet_mode=True,
    )
    compressor._generate_summary = lambda *_args, **_kwargs: f"{SUMMARY_PREFIX}\n## Active Task\nstale task"  # type: ignore[method-assign]
    compressor._prune_old_tool_results = lambda messages, **_kwargs: (messages, 0)  # type: ignore[method-assign]
    compressor._protect_head_size = lambda _messages: 1  # type: ignore[method-assign]
    compressor._align_boundary_forward = lambda _messages, index: index  # type: ignore[method-assign]
    compressor._find_tail_cut_by_tokens = lambda messages, _start: len(messages)  # type: ignore[method-assign]
    compressor._sanitize_tool_pairs = lambda messages: messages  # type: ignore[method-assign]
    return compressor


def test_compaction_summary_never_becomes_latest_user_message():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "middle request"},
        {"role": "assistant", "content": "middle answer"},
        {"role": "user", "content": "latest real user request"},
    ]

    compressed = _compressor().compress(messages, current_tokens=100_000, force=True)

    assert compressed[-1]["role"] == "assistant"
    assert SUMMARY_PREFIX in compressed[-1]["content"]
    assert "latest real user request" not in compressed[-1]["content"]
    assert "latest real user message" in compressed[-1]["content"]


def test_compaction_summary_merges_into_assistant_tail_instead_of_user_turn():
    compressor = _compressor()
    compressor._find_tail_cut_by_tokens = lambda _messages, _start: 3  # type: ignore[method-assign]
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "assistant", "content": "live assistant tail"},
        {"role": "user", "content": "tail bridge"},
        {"role": "assistant", "content": "tail answer"},
        {"role": "user", "content": "latest real user request"},
    ]

    compressed = compressor.compress(messages, current_tokens=100_000, force=True)

    assert not any(msg["role"] == "user" and SUMMARY_PREFIX in msg.get("content", "") for msg in compressed)
    assert compressed[1]["role"] == "assistant"
    assert SUMMARY_PREFIX in compressed[1]["content"]
    assert "live assistant tail" in compressed[1]["content"]
    assert compressed[-1]["content"] == "latest real user request"
