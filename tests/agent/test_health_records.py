from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from agent import health_records
from agent.health_records import (
    ensure_health_schema,
    record_health_observation,
    read_health_summary,
)
from agent.selfcheck_operator import observe_cron_job_result
from agent.tool_executor import _observe_tool_health
from tools.send_message_tool import send_message_tool
from tools.skills_tool import skill_view
from hermes_cli.skills_hub import _resolve_source_meta_and_bundle
from tools.skills_hub import SkillBundle, SkillMeta, SkillSource, parallel_search_sources


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


def test_skill_connector_search_and_resolution_write_health_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FailingSource(SkillSource):
        def source_id(self):
            return "broken-source"

        def search(self, query, limit=10):
            raise RuntimeError("search boom")

        def inspect(self, identifier):
            raise RuntimeError("inspect boom")

        def fetch(self, identifier):
            raise RuntimeError("fetch boom")

    class HealthySource(SkillSource):
        def source_id(self):
            return "healthy-source"

        def search(self, query, limit=10):
            return [
                SkillMeta(
                    name="demo-skill",
                    description="Demo",
                    source="github",
                    identifier="owner/repo/demo-skill",
                    trust_level="community",
                )
            ]

        def inspect(self, identifier):
            return SkillMeta(
                name="demo-skill",
                description="Demo",
                source="github",
                identifier=identifier,
                trust_level="community",
            )

        def fetch(self, identifier):
            return SkillBundle(
                name="demo-skill",
                files={"SKILL.md": "---\nname: demo-skill\n---\n"},
                source="github",
                identifier=identifier,
                trust_level="community",
            )

    failing = FailingSource()
    healthy = HealthySource()

    results, _, _ = parallel_search_sources([failing, healthy], query="demo", overall_timeout=1)
    meta, bundle, matched = _resolve_source_meta_and_bundle("owner/repo/demo-skill", [failing, healthy])

    assert len(results) == 1
    assert meta is not None
    assert bundle is not None
    assert matched is healthy

    summary = read_health_summary(chain_type="skill_connector", now=1_000_000_000.0)
    by_target = {r["target_id"]: r for r in summary["records"]}

    assert by_target["healthy-source"]["status"] == "success"
    assert by_target["healthy-source"]["failure_count"] == 0
    assert by_target["healthy-source"]["observation_count"] >= 3
    assert by_target["broken-source"]["status"] == "degraded"
    assert by_target["broken-source"]["failure_count"] >= 1
    assert by_target["broken-source"]["failure_rate"] == 1.0


def test_skill_view_and_send_message_write_health_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import tools.skills_tool as skills_tool_module
    from gateway.config import Platform, PlatformConfig

    skills_dir = tmp_path / "skills"
    demo_dir = skills_dir / "demo-skill"
    demo_dir.mkdir(parents=True)
    (demo_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_tool_module, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(skills_tool_module, "SKILLS_DIR", skills_dir)

    loaded = json.loads(skill_view("demo-skill"))
    missing = json.loads(skill_view("missing-skill"))

    assert loaded["success"] is True
    assert missing["success"] is False

    class FakeGatewayConfig:
        def __init__(self):
            self.platforms = {
                Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token", extra={})
            }

        def get_home_channel(self, platform):
            return SimpleNamespace(chat_id="123456")

    outcomes = [
        {"success": True, "platform": "telegram", "chat_id": "123456", "message_id": "m-1"},
        {"error": "gateway unavailable"},
    ]

    async def fake_send_to_platform(*args, **kwargs):
        return outcomes.pop(0)

    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: FakeGatewayConfig())
    monkeypatch.setattr("tools.send_message_tool._send_to_platform", fake_send_to_platform)
    monkeypatch.setattr("tools.send_message_tool._maybe_skip_cron_duplicate_send", lambda *a, **k: None)

    send_ok = json.loads(send_message_tool({"target": "telegram", "message": "hello"}))
    send_bad = json.loads(send_message_tool({"target": "telegram", "message": "hello again"}))

    assert send_ok["success"] is True
    assert send_bad["error"] == "gateway unavailable"

    summary = read_health_summary(now=1_000_000_000.0)
    by_key = {(r["chain_type"], r["target_id"]): r for r in summary["records"]}

    assert by_key[("skill", "demo-skill")]["status"] == "success"
    assert by_key[("skill", "missing-skill")]["status"] == "degraded"
    assert by_key[("connector", "telegram")]["status"] == "degraded"
    assert by_key[("connector", "telegram")]["observation_count"] == 2
    assert by_key[("connector", "telegram")]["failure_count"] == 1
    assert by_key[("connector", "telegram")]["failure_rate"] == 0.5


@pytest.mark.asyncio
async def test_api_health_summary_route_returns_latest_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    record_health_observation(
        chain_type="cron",
        target_id="job-ok",
        target_label="Daily OK",
        status="success",
        success=True,
    )
    record_health_observation(
        chain_type="browser_automation",
        target_id="browser_click",
        target_label="Browser Click",
        status="degraded",
        success=False,
        error_message="timeout",
    )

    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter, cors_middleware

    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={}))
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/health/summary", adapter._handle_health_summary)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/health/summary?limit=10")
        assert resp.status == 200
        payload = await resp.json()

    assert payload["schema"] == "hermes_health_records_v1"
    assert payload["status"] == "degraded"
    assert payload["record_count"] == 2
    assert {(r["chain_type"], r["target_id"], r["status"]) for r in payload["records"]} == {
        ("cron", "job-ok", "success"),
        ("browser_automation", "browser_click", "degraded"),
    }


def test_selfcheck_health_cli_reads_same_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    record_health_observation(
        chain_type="cron",
        target_id="job-ok",
        status="success",
        success=True,
    )
    record_health_observation(
        chain_type="browser_automation",
        target_id="browser_click",
        status="degraded",
        success=False,
        error_message="timeout",
    )

    from hermes_cli.main import cmd_selfcheck

    rc = cmd_selfcheck(SimpleNamespace(selfcheck_command="health", json=True, chain_type=None, limit=10))
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["schema"] == "hermes_health_records_v1"
    assert payload["status"] == "degraded"
    assert {r["chain_type"] for r in payload["records"]} == {"cron", "browser_automation"}
