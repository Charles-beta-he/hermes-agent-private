from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace


class DummyAgent:
    def __init__(self, *, session_id: str = "sess-proof", model: str = "gpt-test", provider: str = "openai"):
        self.session_id = session_id
        self.model = model
        self.provider = provider
        self.platform = "cli"
        self._current_turn_id = "turn-proof"


def _proof_rows(db_path: Path):
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("select link_type, status, entry_json from proof_logs order by created_at")]


def test_browser_turn_emits_enabled_proof_log_entry(tmp_path, monkeypatch):
    db_path = tmp_path / "proof_logs.db"
    monkeypatch.setenv("HERMES_PROOF_LOG", "1")
    monkeypatch.setenv("HERMES_PROOF_LOG_DB", str(db_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar.runtime import emit_turn_sidecar, observe_tool_call

    agent = DummyAgent(session_id="sess-browser-proof")
    observe_tool_call(agent, function_name="browser_navigate", function_args={"url": "https://example.com"}, result={"url": "https://example.com"})
    observe_tool_call(agent, function_name="browser_click", function_args={"ref": "@e1"}, result={"ok": True})

    run_id = emit_turn_sidecar(agent, turn_id="turn-browser-proof", completed=True, platform="cli")

    assert run_id
    rows = _proof_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["link_type"] == "browser"
    assert rows[0]["status"] == "succeeded"
    assert "browser_navigate" in rows[0]["entry_json"]
    assert "browser_click" in rows[0]["entry_json"]
    assert "https://example.com" in rows[0]["entry_json"]


def test_script_tool_turn_emits_failed_proof_log_entry_with_error(tmp_path, monkeypatch):
    db_path = tmp_path / "proof_logs.db"
    monkeypatch.setenv("HERMES_PROOF_LOG", "true")
    monkeypatch.setenv("HERMES_PROOF_LOG_DB", str(db_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar.runtime import emit_turn_sidecar, observe_tool_call

    agent = DummyAgent(session_id="sess-script-proof")
    observe_tool_call(
        agent,
        function_name="terminal",
        function_args={"command": "python fail.py", "workdir": str(tmp_path)},
        result={"exit_code": 2, "output": "Traceback: boom"},
    )

    run_id = emit_turn_sidecar(agent, turn_id="turn-script-proof", completed=False, interrupted=True, platform="cli", reason="tool_failed")

    assert run_id
    rows = _proof_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["link_type"] == "script"
    assert rows[0]["status"] == "failed"
    assert "tool_failed" in rows[0]["entry_json"]
    assert "terminal" in rows[0]["entry_json"]
    assert "Traceback" in rows[0]["entry_json"]


def test_cron_helper_emits_proof_log_when_enabled_and_continues_on_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "proof_logs.db"
    monkeypatch.setenv("HERMES_PROOF_LOG", "1")
    monkeypatch.setenv("HERMES_PROOF_LOG_DB", str(db_path))

    import cron.scheduler as sched

    sched = importlib.reload(sched)
    output_file = tmp_path / "cron-output.txt"
    output_file.write_text("cron says hi", encoding="utf-8")

    meta = sched._emit_cron_session_sidecar(
        job={"id": "job-proof", "name": "Proof Job", "schedule": "* * * * *", "deliver": "local", "no_agent": True, "script": "proof.sh"},
        run_success=False,
        final_success=False,
        output="cron failed",
        final_response="",
        error="boom",
        delivery_error=None,
        output_file=output_file,
        duration_s=0.5,
    )

    assert meta is not None
    rows = _proof_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["link_type"] == "cron"
    assert rows[0]["status"] == "failed"
    assert "job-proof" in rows[0]["entry_json"]
    assert "boom" in rows[0]["entry_json"]

    monkeypatch.setenv("HERMES_PROOF_LOG_DB", str(tmp_path / "missing" / "proof_logs.db"))
    monkeypatch.setattr("proof_log.write_proof_log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    meta_after_failure = sched._emit_cron_session_sidecar(
        job={"id": "job-proof-2", "name": "Proof Job 2", "schedule": "* * * * *"},
        run_success=True,
        final_success=True,
        output="ok",
        final_response="ok",
        error=None,
        delivery_error=None,
        output_file=None,
        duration_s=0.1,
    )
    assert meta_after_failure is not None


def test_proof_log_disabled_by_default(tmp_path, monkeypatch):
    db_path = tmp_path / "proof_logs.db"
    monkeypatch.delenv("HERMES_PROOF_LOG", raising=False)
    monkeypatch.setenv("HERMES_PROOF_LOG_DB", str(db_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)

    from session_sidecar.runtime import emit_turn_sidecar, observe_tool_call

    agent = DummyAgent(session_id="sess-disabled-proof")
    observe_tool_call(agent, function_name="browser_navigate", function_args={"url": "https://example.com"}, result={"url": "https://example.com"})
    assert emit_turn_sidecar(agent, turn_id="turn-disabled-proof", completed=True, platform="cli")
    assert not db_path.exists()
