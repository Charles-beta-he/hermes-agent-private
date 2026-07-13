"""Regression tests for context compaction handoff role safety."""

from agent.context_compressor import (
    COMPRESSED_SUMMARY_METADATA_KEY,
    ContextCompressor,
    SUMMARY_PREFIX,
)


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


def test_compaction_does_not_replay_latest_user_message_when_no_tail_survives():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "middle request"},
        {"role": "assistant", "content": "middle answer"},
        {"role": "user", "content": "latest real user request"},
    ]

    compressed = _compressor().compress(messages, current_tokens=100_000, force=True)

    assert not any("latest real user request" in msg.get("content", "") for msg in compressed)
    assert compressed[0]["role"] == "system"


def test_user_role_summary_is_safe_when_latest_real_user_tail_survives():
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

    summaries = [msg for msg in compressed if msg.get(COMPRESSED_SUMMARY_METADATA_KEY)]
    assert len(summaries) == 1
    assert summaries[0]["role"] == "user"
    assert SUMMARY_PREFIX in summaries[0]["content"]
    assert "respond to the message below" in summaries[0]["content"]
    assert compressed[-1] == {"role": "user", "content": "latest real user request"}
