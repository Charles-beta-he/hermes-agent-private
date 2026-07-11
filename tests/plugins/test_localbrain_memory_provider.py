from __future__ import annotations

import json
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from agent.memory_manager import MemoryManager
from plugins.memory import load_memory_provider
from plugins.memory.localbrain import LocalBrainMemoryProvider


def _hit(**overrides):
    value = {
        "path": "05-project/note.md",
        "line_start": 10,
        "line_end": 12,
        "content_hash": "a" * 64,
        "excerpt": "canonical evidence",
    }
    value.update(overrides)
    return value


def test_provider_loads_and_is_read_only(tmp_path):
    script = tmp_path / "brainctl.py"
    script.write_text("# fixture", encoding="utf-8")
    provider = load_memory_provider("localbrain")
    assert isinstance(provider, LocalBrainMemoryProvider)
    provider._config = {"brainctl_path": str(script)}
    assert provider.is_available() is True
    assert provider.get_tool_schemas() == []
    assert provider.sync_turn("user", "assistant") is None


def test_prefetch_returns_bounded_authority_neutral_hash_bound_context(tmp_path):
    script = tmp_path / "brainctl.py"
    script.write_text("# fixture", encoding="utf-8")
    provider = LocalBrainMemoryProvider({"brainctl_path": str(script), "limit": 1})
    completed = CompletedProcess([], 0, json.dumps([_hit()]), "")
    with patch("plugins.memory.localbrain.subprocess.run", return_value=completed) as run:
        result = provider.prefetch(" canonical   hash ")
    assert "authority-neutral" in result
    assert "05-project/note.md:10-12" in result
    assert "sha256=" + "a" * 64 in result
    assert "canonical evidence" in result
    command = run.call_args.args[0]
    assert command[-3:] == ["canonical hash", "--limit", "1"]


def test_prefetch_fails_closed_for_bad_hash_range_and_runtime_errors(tmp_path):
    script = tmp_path / "brainctl.py"
    script.write_text("# fixture", encoding="utf-8")
    provider = LocalBrainMemoryProvider({"brainctl_path": str(script)})
    malformed = CompletedProcess([], 0, json.dumps([
        _hit(content_hash="bad"),
        _hit(line_start=0),
        _hit(line_start=20, line_end=10),
    ]), "")
    with patch("plugins.memory.localbrain.subprocess.run", return_value=malformed):
        assert provider.prefetch("query") == ""
    with patch("plugins.memory.localbrain.subprocess.run", side_effect=TimeoutExpired([], 1)):
        assert provider.prefetch("query") == ""


def test_memory_manager_prefetch_all_real_provider_contract(tmp_path):
    script = tmp_path / "brainctl.py"
    script.write_text("# fixture", encoding="utf-8")
    provider = LocalBrainMemoryProvider({"brainctl_path": str(script)})
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all("session-localbrain", platform="test", hermes_home=str(tmp_path))
    completed = CompletedProcess([], 0, json.dumps([_hit()]), "")
    with patch("plugins.memory.localbrain.subprocess.run", return_value=completed):
        result = manager.prefetch_all("canonical hash")
    assert "Local Brain read-only recall" in result
    assert "authority-neutral" in result
    assert "canonical evidence" in result
