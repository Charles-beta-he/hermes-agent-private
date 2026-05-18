from types import SimpleNamespace

from agent.system_prompt import build_system_prompt_content_blocks, invalidate_system_prompt


def test_system_prompt_content_blocks_preserve_cacheable_prefix_and_ephemeral_suffix():
    agent = SimpleNamespace(
        _cached_system_prompt_parts={
            "stable": "stable identity",
            "context": "cwd context",
            "volatile": "timestamp and memory",
        },
        _cached_system_prompt="stable identity\n\ncwd context\n\ntimestamp and memory",
    )

    blocks = build_system_prompt_content_blocks(
        agent,
        system_prompt=agent._cached_system_prompt,
        extra_blocks=["one-turn hint"],
    )

    assert blocks == [
        {"type": "text", "text": "stable identity", "_hermes_cacheable": True},
        {"type": "text", "text": "cwd context", "_hermes_cacheable": True},
        {"type": "text", "text": "timestamp and memory", "_hermes_cacheable": False},
        {"type": "text", "text": "one-turn hint", "_hermes_cacheable": False},
    ]


def test_system_prompt_content_blocks_falls_back_to_string_when_cached_parts_do_not_match():
    agent = SimpleNamespace(
        _cached_system_prompt_parts={"stable": "stable", "context": "", "volatile": "volatile"},
        _cached_system_prompt="stable\n\nvolatile",
    )

    content = build_system_prompt_content_blocks(
        agent,
        system_prompt="opaque continuation prompt",
        extra_blocks=["one-turn hint"],
    )

    assert content == "opaque continuation prompt\n\none-turn hint"


def test_invalidate_system_prompt_clears_cached_parts():
    memory_store = SimpleNamespace(load_from_disk=lambda: None)
    agent = SimpleNamespace(
        _cached_system_prompt="prompt",
        _cached_system_prompt_parts={"stable": "stable"},
        _memory_store=memory_store,
    )

    invalidate_system_prompt(agent)

    assert agent._cached_system_prompt is None
    assert agent._cached_system_prompt_parts is None
