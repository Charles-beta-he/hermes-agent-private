"""Behavior tests for the built-in memory -> external provider bridge."""

import json

import pytest

from agent.memory_manager import MemoryManager
from agent.memory_provider import MemoryProvider


class _RecordingProvider(MemoryProvider):
    def __init__(self) -> None:
        self.calls = []

    @property
    def name(self) -> str:
        return "recording"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        pass

    def get_tool_schemas(self):
        return []

    def shutdown(self) -> None:
        pass

    def on_memory_write(self, action, target, content, metadata=None):
        self.calls.append({
            "action": action,
            "target": target,
            "content": content,
            "metadata": dict(metadata or {}),
        })


def _manager_with_provider():
    manager = MemoryManager()
    provider = _RecordingProvider()
    manager.add_provider(provider)
    return manager, provider


def test_notifies_only_after_committed_write():
    manager, provider = _manager_with_provider()
    manager.notify_memory_tool_write(
        json.dumps({"success": True, "changed": True}),
        {"action": "add", "target": "user", "content": "new fact"},
        build_metadata=lambda: {"session_id": "s1"},
    )
    assert provider.calls == [{
        "action": "add",
        "target": "user",
        "content": "new fact",
        "metadata": {"session_id": "s1"},
    }]


@pytest.mark.parametrize(
    "tool_result",
    [
        json.dumps({"success": False, "changed": True}),
        json.dumps({"success": True}),
        json.dumps({"success": True, "changed": False}),
        json.dumps({"success": True, "staged": True}),
        json.dumps({"success": {"changed": True}}),
        json.dumps({"success": True, "result": {"changed": True}}),
        "not-json",
        None,
        [],
    ],
)
def test_skips_uncommitted_or_unrecognized_write(tool_result):
    manager, provider = _manager_with_provider()
    manager.notify_memory_tool_write(
        tool_result,
        {"action": "add", "target": "memory", "content": "must not mirror"},
    )
    assert provider.calls == []


def test_mirrors_only_top_level_operation_and_ignores_nested_request_fields():
    manager, provider = _manager_with_provider()
    manager.notify_memory_tool_write(
        {"success": True, "changed": True},
        {
            "action": "add",
            "target": "memory",
            "content": "actually added",
            "operations": [{"action": "remove", "old_text": "never removed"}],
        },
        build_metadata=lambda: {"write_origin": "memory_tool"},
    )
    assert provider.calls == [{
        "action": "add",
        "target": "memory",
        "content": "actually added",
        "metadata": {"write_origin": "memory_tool"},
    }]


def test_preserves_old_text_for_top_level_replace():
    manager, provider = _manager_with_provider()
    manager.notify_memory_tool_write(
        {"success": True, "changed": True},
        {"action": "replace", "target": "user", "old_text": "old", "content": "new"},
    )
    assert provider.calls == [{
        "action": "replace", "target": "user", "content": "new",
        "metadata": {"old_text": "old"},
    }]


def test_non_mutating_actions_are_not_mirrored():
    manager, provider = _manager_with_provider()
    manager.notify_memory_tool_write(
        {"success": True, "changed": True},
        {"action": "read", "target": "memory"},
    )
    assert provider.calls == []


def test_builtin_receipts_distinguish_mutation_from_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools.memory_tool import MemoryStore

    store = MemoryStore()
    store.load_from_disk()
    first = store.add("memory", "same fact")
    duplicate = store.add("memory", "same fact")
    same_replace = store.replace("memory", "same fact", "same fact")
    duplicate_batch = store.apply_batch(
        "memory", [{"action": "add", "content": "same fact"}]
    )

    assert first["success"] is True and first["changed"] is True
    assert duplicate["success"] is True and duplicate["changed"] is False
    assert same_replace["success"] is True and same_replace["changed"] is False
    assert duplicate_batch["success"] is True and duplicate_batch["changed"] is False

    manager, provider = _manager_with_provider()
    args = {"action": "add", "target": "memory", "content": "same fact"}
    for result in (first, duplicate, same_replace, duplicate_batch):
        manager.notify_memory_tool_write(result, args)
    assert [call["content"] for call in provider.calls] == ["same fact"]
