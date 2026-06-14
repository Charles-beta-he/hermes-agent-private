from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import hermes_constants as hc

_TEST_HOME = Path(tempfile.mkdtemp(prefix="sidecar_runtime_test_"))


def _patch_home() -> None:
    import hermes_constants
    import session_sidecar as ss

    hermes_constants.get_hermes_home = lambda: _TEST_HOME
    ss.get_hermes_home = lambda: _TEST_HOME


_patched = False


def _ensure_patched() -> None:
    global _patched
    if _patched:
        return
    _patch_home()
    _patched = True


class DummyAgent:
    def __init__(self, *, session_id: str = "sess-1", model: str = "gpt-test", provider: str = "openai"):
        self.session_id = session_id
        self.model = model
        self.provider = provider
        self.platform = "cli"
        self._current_turn_id = "turn-1"


def test_emit_turn_sidecar_for_browser_run(monkeypatch):
    _ensure_patched()
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar import read_sidecar
    from session_sidecar.consumer import main
    from session_sidecar.runtime import emit_turn_sidecar, observe_tool_call

    screenshot = _TEST_HOME / "browser-shot.png"
    screenshot.write_bytes(b"png")

    agent = DummyAgent(session_id="sess-browser")
    observe_tool_call(agent, function_name="browser_navigate", function_args={"url": "https://example.com"}, result='{"url":"https://example.com"}')
    observe_tool_call(agent, function_name="browser_click", function_args={"ref": "@e1"}, result='{"ok":true}')
    observe_tool_call(agent, function_name="browser_vision", function_args={"question": "what is here?"}, result=json.dumps({"screenshot_path": str(screenshot)}))

    run_id = emit_turn_sidecar(
        agent,
        task_id="",
        turn_id="turn-browser",
        completed=True,
        interrupted=False,
        model="gpt-test",
        platform="cli",
    )

    assert run_id
    sc = read_sidecar(run_id)
    assert sc is not None
    assert sc.entrypoint == "automation"
    assert sc.extra["url_count"] == 1
    assert sc.extra["click_count"] == 1
    assert sc.extra["screenshot_count"] >= 1
    assert sc.artifacts == []
    assert f"proof:session-sidecar:run:{run_id}" in sc.evidence_refs

    rc = main(["proof-log", f"date_prefix={sc.created_at[:10]}", "entrypoint=automation", "limit=20"])
    assert rc == 0


def test_emit_turn_sidecar_resets_browser_state_between_reused_agent_turns(monkeypatch):
    _ensure_patched()
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar import read_sidecar
    from session_sidecar.runtime import emit_turn_sidecar, observe_tool_call

    agent = DummyAgent(session_id="sess-reused")
    observe_tool_call(agent, function_name="browser_navigate", function_args={"url": "https://example.com"}, result={"ok": True})
    first_run_id = emit_turn_sidecar(
        agent,
        task_id="",
        turn_id="turn-browser-first",
        completed=True,
        interrupted=False,
        model="gpt-test",
        platform="cli",
    )
    second_run_id = emit_turn_sidecar(
        agent,
        task_id="",
        turn_id="turn-cli-second",
        completed=True,
        interrupted=False,
        model="gpt-test",
        platform="cli",
    )

    assert first_run_id
    assert second_run_id
    first = read_sidecar(first_run_id)
    second = read_sidecar(second_run_id)
    assert first is not None and first.entrypoint == "automation"
    assert second is not None and second.entrypoint == "cli"


def test_emit_turn_sidecar_for_kanban_run_records_proof_artifact(monkeypatch):
    _ensure_patched()
    workspace = _TEST_HOME / "kanban-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    proof_path = workspace / "proof.md"
    proof_path.write_text("# Proof Log\n\n- runtime bridge check\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t-sidecar")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(workspace))
    monkeypatch.setenv("HERMES_PROFILE", "brain-mirror")

    from session_sidecar import read_sidecar
    from session_sidecar.runtime import emit_turn_sidecar

    agent = DummyAgent(session_id="sess-kanban")
    run_id = emit_turn_sidecar(
        agent,
        task_id="t-sidecar",
        turn_id="turn-kanban",
        completed=True,
        interrupted=False,
        model="gpt-test",
        platform="cli",
    )

    assert run_id
    sc = read_sidecar(run_id)
    assert sc is not None
    assert sc.entrypoint == "kanban_worker"
    assert sc.task_id == "t-sidecar"
    assert sc.extra["workspace_path"] == str(workspace)
    assert sc.extra["assignee"] == "brain-mirror"
    assert f"proof:session-sidecar:run:{run_id}" in sc.evidence_refs
    assert any(str(proof_path) in ref for ref in sc.evidence_refs)


def test_emit_turn_sidecar_for_acp_run_records_adapter_metadata(monkeypatch):
    _ensure_patched()
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar import read_sidecar
    from session_sidecar.runtime import emit_turn_sidecar

    agent = DummyAgent(session_id="sess-acp")
    setattr(agent, "_session_sidecar_client_name", "zed")
    setattr(agent, "_session_sidecar_command", "copilot --acp --stdio")
    run_id = emit_turn_sidecar(
        agent,
        task_id="acp-session-1",
        turn_id="turn-acp",
        completed=True,
        interrupted=False,
        model="gpt-test",
        platform="acp",
    )

    assert run_id
    sc = read_sidecar(run_id)
    assert sc is not None
    assert sc.entrypoint == "acp_adapter"
    assert sc.extra["client_name"] == "zed"
    assert sc.extra["command"] == "copilot --acp --stdio"
    assert f"proof:session-sidecar:run:{run_id}" in sc.evidence_refs
    assert "session:sess-acp" in sc.evidence_refs


def test_emit_turn_sidecar_skips_cron_agent_runs(monkeypatch):
    _ensure_patched()
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar.runtime import emit_turn_sidecar

    agent = DummyAgent(session_id="sess-cron")
    run_id = emit_turn_sidecar(
        agent,
        task_id="job-1",
        turn_id="turn-cron",
        completed=True,
        interrupted=False,
        model="gpt-test",
        platform="cron",
    )

    assert run_id is None
