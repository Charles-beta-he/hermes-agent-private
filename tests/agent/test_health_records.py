from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from agent import health_records
from agent.health_records import (
    ensure_health_schema,
    record_health_observation,
    read_health_summary,
)
from agent.selfcheck_operator import observe_cron_job_result
from agent.tool_executor import _observe_tool_health


def test_health_records_schema_decay_and_failure_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db_path = tmp_path / "state.db"
    now = 1_000_000.0

    ensure_health_schema(db_path)
    record_health_observation(
        chain_type="cron",
        target_id="job-success",
        status="success",
        success=True,
        observed_at=now,
        db_path=db_path,
    )
    failed = record_health_observation(
        chain_type="cron",
        target_id="job-degraded",
        status="degraded",
        success=False,
        error_message="delivery failed",
        observed_at=now,
        db_path=db_path,
    )

    assert failed["failure_count"] == 1
    assert failed["observation_count"] == 1
    assert failed["failure_rate"] == 1.0
    summary = read_health_summary(now=now + health_records.STALE_HALF_LIFE_SECONDS, db_path=db_path)
    stale = next(r for r in summary["records"] if r["target_id"] == "job-success")
    assert 49.0 <= stale["effective_health_score"] <= 51.0

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(health_records)")}
    assert {"chain_type", "target_id", "health_score", "failure_rate", "details_json"} <= cols


def test_cron_and_browser_automation_write_health_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    observe_cron_job_result(
        job={"id": "job-ok", "name": "Daily OK"},
        success=True,
        final_response="delivered",
    )
    observe_cron_job_result(
        job={"id": "job-bad", "name": "Daily Bad"},
        success=True,
        delivery_error="gateway unavailable",
        final_response="prepared",
    )
    _observe_tool_health(
        SimpleNamespace(session_id="sess-browser", _current_turn_id="turn-browser"),
        function_name="browser_navigate",
        result=json.dumps({"status": "ok"}),
        is_error=False,
        effective_task_id="task-browser",
        tool_call_id="call-success",
    )
    _observe_tool_health(
        SimpleNamespace(session_id="sess-browser", _current_turn_id="turn-browser"),
        function_name="browser_click",
        result="Error executing tool 'browser_click': timeout",
        is_error=True,
        effective_task_id="task-browser",
        tool_call_id="call-error",
    )

    summary = read_health_summary(now=1_000_000_000.0)
    by_key = {(r["chain_type"], r["target_id"]): r for r in summary["records"]}

    assert by_key[("cron", "job-ok")]["status"] == "success"
    assert by_key[("cron", "job-bad")]["status"] == "degraded"
    assert by_key[("browser_automation", "browser_navigate")]["status"] == "success"
    assert by_key[("browser_automation", "browser_click")]["status"] == "degraded"
    assert by_key[("browser_automation", "browser_click")]["failure_rate"] == 1.0
    assert summary["schema"] == "hermes_health_records_v1"
