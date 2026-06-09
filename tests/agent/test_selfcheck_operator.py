from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.selfcheck_operator import (
    authorize_repair_plan,
    observe_cron_job_result,
    observe_event,
    observe_gateway_delivery,
    observe_provider_error,
    observe_taskpool_event,
    observe_turn_result,
    read_health_summary,
    read_recent_findings,
    read_sentinel_status,
    run_authorized_repair_plan,
    run_repair_plan_dry_run,
    run_sentinel_tick,
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


def test_projections_are_rebuilt_after_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    observe_cron_job_result(
        job={"id": "job-1", "name": "Daily"},
        success=True,
        delivery_error="gateway unavailable",
        output_file="/tmp/out.md",
        final_response="hello",
    )

    root = tmp_path / "state" / "selfcheck"
    summary = json.loads((root / "health_summary.json").read_text(encoding="utf-8"))
    latest = json.loads((root / "latest_findings.json").read_text(encoding="utf-8"))
    components = json.loads((root / "component_status.json").read_text(encoding="utf-8"))

    assert summary["overall_status"] == "yellow"
    assert summary["waiting_for_judgement"] == 1
    assert latest["findings"][-1]["component"] == "cron"
    assert components["components"]["cron"]["status"] == "yellow"
    assert read_health_summary()["overall_status"] == "yellow"


def test_provider_error_creates_provider_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_provider_error(
        provider="openai-codex",
        model="gpt-5.5",
        error=RuntimeError("OpenAI client closed"),
        session_id="sess-provider",
        turn_id="turn-provider",
        api_call_count=2,
    )

    assert decision.findings
    finding = decision.findings[0]
    assert finding.component == "provider"
    assert finding.severity == "P1"
    assert "model_call" in finding.blocks_what
    summary = read_health_summary()
    assert "provider" in summary["components"]


def test_gateway_delivery_failure_creates_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_gateway_delivery(
        platform="telegram",
        chat_id="123",
        thread_id="456",
        success=False,
        error="platform send failed",
    )

    assert decision.findings
    finding = decision.findings[0]
    assert finding.component == "gateway"
    assert finding.severity == "P1"
    assert "external_delivery" in finding.blocks_what
    assert "gateway" in read_health_summary()["components"]


def test_taskpool_event_and_dry_run_repair_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_taskpool_event(
        event_type="transition_rejected",
        summary="transition rejected: done without proof",
        task_id="task-123",
        status="review",
        evidence_refs=["task:task-123"],
    )

    assert decision.repair_plans
    plan = decision.repair_plans[0]
    attempt = run_repair_plan_dry_run(plan.id, request_digest=plan.request_digest)

    assert attempt.status == "dry_run_verified"
    assert attempt.plan_id == plan.id
    root = tmp_path / "state" / "selfcheck"
    attempts = _jsonl(root / "repair_attempts.jsonl")
    assert attempts[-1]["status"] == "dry_run_verified"
    summary = read_health_summary()
    assert summary["waiting_for_judgement"] == 0


def test_authorized_repair_plan_requires_digest_and_records_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_gateway_delivery(
        platform="telegram",
        chat_id="123",
        success=False,
        error="adapter send failed",
    )
    plan = decision.repair_plans[0]

    auth = authorize_repair_plan(
        plan.id,
        request_digest=plan.request_digest,
        actor="operator-policy-test",
        ttl_seconds=60,
        evidence_refs=["test:selfcheck_authorization"],
        note="bounded readback approval",
    )
    attempt1 = run_authorized_repair_plan(
        auth.id,
        plan_id=plan.id,
        request_digest=plan.request_digest,
    )
    attempt2 = run_authorized_repair_plan(
        auth.id,
        plan_id=plan.id,
        request_digest=plan.request_digest,
    )

    assert auth.decision == "approved"
    assert attempt1.status == "authorized_readback_verified"
    assert attempt1.id == attempt2.id
    assert attempt1.authorization_id == auth.id
    root = tmp_path / "state" / "selfcheck"
    authorizations = _jsonl(root / "repair_authorizations.jsonl")
    attempts = _jsonl(root / "repair_attempts.jsonl")
    assert len(authorizations) == 1
    assert len(attempts) == 1
    assert read_health_summary()["waiting_for_judgement"] == 0


def test_authorization_rejects_digest_drift_and_secret_like_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    plan = decision.repair_plans[0]

    try:
        authorize_repair_plan(plan.id, request_digest="sha256:wrong", actor="operator")
    except ValueError as exc:
        assert "digest mismatch" in str(exc)
    else:
        raise AssertionError("expected digest mismatch rejection")

    try:
        authorize_repair_plan(
            plan.id,
            request_digest=plan.request_digest,
            actor="operator",
            evidence_refs=["token=abc123"],
        )
    except ValueError as exc:
        assert "secret-like" in str(exc)
    else:
        raise AssertionError("expected secret-like evidence rejection")


def test_authorized_repair_plan_rejects_expired_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    decision = observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    plan = decision.repair_plans[0]
    auth = authorize_repair_plan(
        plan.id,
        request_digest=plan.request_digest,
        actor="operator",
        ttl_seconds=1,
    )

    import agent.selfcheck_operator as sco

    monkeypatch.setattr(sco, "_now", lambda: 9999999999.0)
    try:
        run_authorized_repair_plan(auth.id, plan_id=plan.id, request_digest=plan.request_digest)
    except ValueError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("expected expired authorization rejection")


def test_sentinel_auto_wakes_after_degraded_event_without_sending_or_repairing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    root = tmp_path / "state" / "selfcheck"
    wakeups = _jsonl(root / "wakeups.jsonl")
    diagnosis = _jsonl(root / "diagnosis_packets.jsonl")
    alerts = _jsonl(root / "pending_alerts.jsonl")
    status = read_sentinel_status()

    assert any(w["kind"] == "health_degraded" for w in wakeups)
    assert all(w["side_effects_executed"] is False for w in wakeups)
    assert diagnosis[-1]["side_effects_executed"] is False
    assert alerts[-1]["delivery"] == "local_only_no_platform_send"
    assert status["pending_local_alerts"] >= 1


def test_sentinel_manual_tick_is_idempotent_and_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    observe_taskpool_event(event_type="worker_failed", summary="worker failed", task_id="task-1")
    root = tmp_path / "state" / "selfcheck"
    before_attempts = root / "repair_attempts.jsonl"
    before_attempt_count = len(_jsonl(before_attempts)) if before_attempts.exists() else 0

    first = run_sentinel_tick(source="test", debounce_seconds=0)
    second = run_sentinel_tick(source="test", debounce_seconds=300)

    assert first
    assert second == []
    assert read_sentinel_status()["wakeup_count_tail"] >= 1
    after_attempt_count = len(_jsonl(before_attempts)) if before_attempts.exists() else 0
    assert after_attempt_count == before_attempt_count


def test_sentinel_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_SENTINEL", "0")

    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    root = tmp_path / "state" / "selfcheck"
    assert not (root / "wakeups.jsonl").exists()
    assert run_sentinel_tick(source="test") == []
