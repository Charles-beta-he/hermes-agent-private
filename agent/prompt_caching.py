"""Anthropic prompt caching strategy.

Default layout: ``system_and_3``. 4 cache_control breakpoints — system
prompt + last 3 non-system messages, all at the same TTL (5m or 1h).
When Hermes supplies a layered system prompt as text blocks, the system
breakpoint is placed on the last cacheable prefix block (stable/context),
not on volatile per-turn/session blocks. This keeps Anthropic's prefix cache
warm across sessions and sub-agents without letting timestamps, memory
snapshots, or ephemeral hints poison the cache key.

Pure functions -- no class state, no AIAgent dependency.
"""

import copy
from typing import Any, Dict, List, Optional


def _apply_cache_marker(msg: dict, cache_marker: dict, native_anthropic: bool = False) -> None:
    """Add cache_control to a single message, handling all format variations."""
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        if native_anthropic:
            msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_marker


def _build_marker(ttl: str) -> Dict[str, str]:
    """Build a cache_control marker dict for the given TTL ('5m' or '1h')."""
    marker: Dict[str, str] = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    return marker


def _apply_layered_system_cache_marker(msg: dict, cache_marker: dict) -> bool:
    """Mark the last explicitly-cacheable system block and strip internals.

    Conversation assembly can represent the system prompt as ordered text
    blocks with a private ``_hermes_cacheable`` flag. Anthropic caches the
    prefix up to the block carrying ``cache_control``. Marking the final
    volatile/ephemeral block would make timestamp and one-off hints part of
    the cache key, defeating prefix reuse. Instead, mark the last block whose
    private flag is true and remove the private flag before the request leaves
    Hermes.

    Returns True when this function handled a layered system content list.
    """
    if msg.get("role") != "system":
        return False
    content = msg.get("content")
    if not isinstance(content, list) or not content:
        return False
    saw_private_flag = any(
        isinstance(block, dict) and "_hermes_cacheable" in block
        for block in content
    )
    if not saw_private_flag:
        return False

    cacheable_idx = None
    for idx, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if block.get("_hermes_cacheable") is True:
            cacheable_idx = idx
        block.pop("_hermes_cacheable", None)

    if cacheable_idx is not None and isinstance(content[cacheable_idx], dict):
        content[cacheable_idx]["cache_control"] = cache_marker
    return True


def apply_anthropic_cache_control(
    api_messages: List[Dict[str, Any]],
    cache_ttl: str = "5m",
    native_anthropic: bool = False,
) -> List[Dict[str, Any]]:
    """Apply system_and_3 caching strategy to messages for Anthropic models.

    Places up to 4 cache_control breakpoints: system prompt + last 3 non-system
    messages, all at the same TTL. If the system message is layered using
    Hermes' private ``_hermes_cacheable`` flags, the system breakpoint is placed
    on the last cacheable prefix block instead of the final volatile block.

    Returns:
        Deep copy of messages with cache_control breakpoints injected.
    """
    messages = copy.deepcopy(api_messages)
    if not messages:
        return messages

    marker = _build_marker(cache_ttl)

    breakpoints_used = 0

    if messages[0].get("role") == "system":
        if not _apply_layered_system_cache_marker(messages[0], marker):
            _apply_cache_marker(messages[0], marker, native_anthropic=native_anthropic)
        breakpoints_used += 1

    remaining = 4 - breakpoints_used
    non_sys = [i for i in range(len(messages)) if messages[i].get("role") != "system"]
    for idx in non_sys[-remaining:]:
        _apply_cache_marker(messages[idx], marker, native_anthropic=native_anthropic)

    return messages


def apply_anthropic_tool_cache_control(
    tools: Optional[List[Dict[str, Any]]],
    cache_ttl: str = "5m",
) -> List[Dict[str, Any]]:
    """Return a copy of *tools* with a cache breakpoint on the last tool.

    Native Anthropic accepts ``cache_control`` on tool definitions. Hermes'
    tool registry already returns definitions in deterministic name order;
    marking the final schema caches the whole stable tools array while avoiding
    mutation of ``agent.tools`` (which is reused across turns/retries).
    """
    if not tools:
        return []
    copied = copy.deepcopy(tools)
    copied[-1]["cache_control"] = _build_marker(cache_ttl)
    return copied
