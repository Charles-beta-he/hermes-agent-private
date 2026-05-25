from __future__ import annotations

import json

from agent.norm_governance import NormGovernanceStore, normalize_resource_key
from agent.norm_write_guard import durable_write_guard
from tools.memory_tool import MemoryStore, memory_tool
from tools.file_tools import write_file_tool
from tools.skill_manager_tool import skill_manage


def test_durable_write_guard_serializes_resource_by_lease(tmp_path, monkeypatch):
    db = tmp_path / "norms.db"
    monkeypatch.setenv("HERMES_NORM_GOVERNANCE_DB", str(db))
    monkeypatch.setenv("HERMES_SESSION_ID", "session-a")

    with durable_write_guard(kind="file", identifier="/tmp/shared.md", write_mode="patch"):
        monkeypatch.setenv("HERMES_SESSION_ID", "session-b")
        try:
            with durable_write_guard(kind="file", identifier="/tmp/shared.md", write_mode="patch"):
                raise AssertionError("second writer should not acquire active lease")
        except Exception as exc:
            assert "leased by session session-a" in str(exc)

    store = NormGovernanceStore(db)
    assert store.active_leases() == []


def test_memory_tool_fails_closed_when_another_session_holds_memory_lease(tmp_path, monkeypatch):
    db = tmp_path / "norms.db"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_NORM_GOVERNANCE_DB", str(db))
    store = NormGovernanceStore(db)
    store.acquire_lease(
        resource_key=normalize_resource_key("memory", "user"),
        owner_session_id="session-a",
        ttl_seconds=60,
    )

    monkeypatch.setenv("HERMES_SESSION_ID", "session-b")
    memory_store = MemoryStore(memory_char_limit=500, user_char_limit=500)
    result = json.loads(memory_tool("add", target="user", content="User prefers evidence-first.", store=memory_store))

    assert result["success"] is False
    assert result["norm_governance"]["error"] == "write_lease_conflict"
    assert result["norm_governance"]["resource_key"] == "memory:user"


def test_write_file_tool_fails_closed_when_another_session_holds_file_lease(tmp_path, monkeypatch):
    db = tmp_path / "norms.db"
    target = tmp_path / "shared.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setenv("HERMES_NORM_GOVERNANCE_DB", str(db))
    monkeypatch.setattr("tools.file_tools._check_sensitive_path", lambda *args, **kwargs: None)
    monkeypatch.setattr("tools.file_tools._check_cross_profile_path", lambda *args, **kwargs: None)
    store = NormGovernanceStore(db)
    store.acquire_lease(
        resource_key=normalize_resource_key("file", str(target.resolve())),
        owner_session_id="session-a",
        ttl_seconds=60,
    )

    monkeypatch.setenv("HERMES_SESSION_ID", "session-b")
    result = json.loads(write_file_tool(str(target), "new", task_id="norm-guard-test"))

    assert result["success"] is False
    assert result["norm_governance"]["error"] == "write_lease_conflict"
    assert result["norm_governance"]["resource_key"] == f"file:{target.resolve()}"
    assert target.read_text(encoding="utf-8") == "old"


def test_skill_manage_fails_closed_when_another_session_holds_skill_lease(tmp_path, monkeypatch):
    db = tmp_path / "norms.db"
    monkeypatch.setenv("HERMES_NORM_GOVERNANCE_DB", str(db))
    store = NormGovernanceStore(db)
    store.acquire_lease(
        resource_key=normalize_resource_key("skill", "example-skill"),
        owner_session_id="session-a",
        ttl_seconds=60,
    )

    monkeypatch.setenv("HERMES_SESSION_ID", "session-b")
    result = json.loads(skill_manage("delete", "example-skill"))

    assert result["success"] is False
    assert result["norm_governance"]["error"] == "write_lease_conflict"
    assert result["norm_governance"]["resource_key"] == "skill:example-skill"
