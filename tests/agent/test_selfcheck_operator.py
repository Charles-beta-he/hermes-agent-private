from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.selfcheck_operator import (
    observe_cron_job_result,
    observe_event,
    observe_turn_result,
    read_recent_findings,
)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_observe_turn_result_records_abnormal_finding_and_waiting_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    agent = SimpleNamespace(
        session_id="sess-1",
        task_id="task-1",
        model="m",
        provider="p",
        max_iterations=3,
    )
    result = {
        "final_response": "",
        "messages": [{"role": "user", "content": "自检一下"}, {"role": "tool", "content": "ok"}],
        "api_calls": 3,
        "completed": False,
        "failed": False,
        "interrupted": False,
        "turn_exit_reason": "max_iterations_reached(3/3)",
        "session_id": "sess-1",
        "model": "m",
        "provider": "p",
    }

    decision = observe_turn_result(
        agent=agent,
        result=result,
        messages=result["messages"],
        original_user_message="请自检一下为什么没执行",
        effective_task_id="task-1",
        turn_id="turn-1",
    )

    assert decision.accepted is True
    assert any(f.component == "conversation" for f in decision.findings)
    assert any(f.severity == "P1" for f in decision.findings)
    assert any(p.status == "waiting_for_judgement" for p in decision.repair_plans)

    root = tmp_path / "state" / "selfcheck"
    assert (root / "events.jsonl").exists()
    findings = _jsonl(root / "findings.jsonl")
    plans = _jsonl(root / "repair_plans.jsonl")
    assert any(r["fingerprint"] == "conversation.pending_tool_result" for r in findings)
    assert all(r["status"] == "waiting_for_judgement" for r in plans)


def test_observe_event_debounces_repeated_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    event = {
        "event_type": "tool.failed",
        "trigger": "tool_result",
        "component": "tool",
        "tool_name": "terminal",
        "summary": "Error executing tool 'terminal': boom",
    }

    first = observe_event(event, debounce_seconds=300)
    second = observe_event(event, debounce_seconds=300)

    assert first.findings
    assert not second.findings
    root = tmp_path / "state" / "selfcheck"
    assert (root / "coalesced.jsonl").exists()
    coalesced = _jsonl(root / "coalesced.jsonl")
    assert coalesced[-1]["reason"] == "debounced_recent_fingerprint"


def test_cron_delivery_failure_creates_p1_finding(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_cron_job_result(
        job={"id": "job-1", "name": "Daily"},
        success=True,
        delivery_error="gateway unavailable",
        output_file="/tmp/out.md",
        final_response="hello",
    )

    assert decision.findings
    finding = decision.findings[0]
    assert finding.component == "cron"
    assert finding.severity == "P1"
    assert "scheduled_delivery" in finding.blocks_what
    recent = read_recent_findings(limit=5)
    assert recent[-1]["component"] == "cron"
