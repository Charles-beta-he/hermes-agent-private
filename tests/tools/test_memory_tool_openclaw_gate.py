import json
from types import SimpleNamespace

from agent.cognitive_state import CognitiveState, PolicyState
from tools import memory_tool as memory_tool_module


class TestMemoryToolOpenClawGate:
    def _store(self, tmp_path, monkeypatch):
        memory_dir = tmp_path / "memories"
        monkeypatch.setattr(memory_tool_module, "get_memory_dir", lambda: memory_dir)
        return memory_tool_module.MemoryStore()

    def test_blocks_wip_semantic_memory_when_gate_enabled(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        monkeypatch.setattr(
            memory_tool_module,
            "load_experimental_config",
            lambda: {"openclaw_control_plane": {"memory_write_gate": True}},
        )

        result = json.loads(
            memory_tool_module.memory_tool(
                action="add",
                target="memory",
                content="WIP: temporary todo for this session",
                store=store,
            )
        )

        assert result["success"] is False
        assert "control-plane gate" in result["error"]
        assert result["gate"]["ok"] is False
        assert any("WIP" in err or "temporary" in err for err in result["gate"]["errors"])

    def test_allows_durable_semantic_memory_when_gate_enabled(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        monkeypatch.setattr(
            memory_tool_module,
            "load_experimental_config",
            lambda: {"openclaw_control_plane": {"memory_write_gate": True}},
        )

        result = json.loads(
            memory_tool_module.memory_tool(
                action="add",
                target="user",
                content="User prefers concise responses.",
                store=store,
            )
        )

        assert result["success"] is True
        assert "Entry added" in result["message"]

    def test_prefers_cognitive_state_gate_over_direct_config_probing(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        monkeypatch.setattr(
            memory_tool_module,
            "load_experimental_config",
            lambda: {"openclaw_control_plane": {"memory_write_gate": False}},
        )
        state = CognitiveState(policy=PolicyState(memory_gate={"enabled": True}))

        result = json.loads(
            memory_tool_module.memory_tool(
                action="add",
                target="memory",
                content="temporary TODO WIP",
                store=store,
                cognitive_state=state,
            )
        )

        assert result["success"] is False
        assert result["gate"]["ok"] is False
        assert "control-plane gate" in result["error"]
