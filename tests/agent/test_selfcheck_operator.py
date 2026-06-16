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
    run_telegram_delivery_tick,
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
        sidecar_run_id="cron-sidecar-1",
        sidecar_path="/tmp/cron-sidecar-1.json",
        extra_evidence_refs=["proof:session-sidecar:run:cron-sidecar-1"],
    )

    assert decision.findings
    finding = decision.findings[0]
    assert finding.component == "cron"
    assert finding.severity == "P1"
    assert "scheduled_delivery" in finding.blocks_what
    assert "proof:session-sidecar:run:cron-sidecar-1" in finding.evidence_refs
    recent = read_recent_findings(limit=5)
    assert recent[-1]["component"] == "cron"
    assert "proof:session-sidecar:run:cron-sidecar-1" in recent[-1]["evidence_refs"]


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


def test_projection_dedupes_repeated_waiting_plan_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    event = {
        "event_type": "cron.job_failed",
        "trigger": "cron_scheduler",
        "component": "cron",
        "job_id": "job-1",
        "job_name": "Daily",
        "success": False,
        "summary": "RuntimeError: Connection error.",
        "error": "RuntimeError: Connection error.",
        "evidence_refs": ["cron_job:job-1", "file:/tmp/out.md"],
    }
    for _ in range(3):
        observe_event(event, debounce_seconds=0)

    root = tmp_path / "state" / "selfcheck"
    plans = _jsonl(root / "repair_plans.jsonl")
    assert len(plans) >= 3
    assert len({p["id"] for p in plans}) == 1
    assert read_health_summary()["waiting_for_judgement"] == 1
    emitted = run_sentinel_tick(source="test", debounce_seconds=0)
    judgement = [w for w in emitted if w.kind == "judgement_required"]
    assert judgement
    assert "1 repair plan" in judgement[-1].reason


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


def test_l1_auto_repair_rebuilds_missing_projection_from_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    root = tmp_path / "state" / "selfcheck"
    (root / "component_status.json").unlink()

    emitted = run_sentinel_tick(source="test", debounce_seconds=0)

    assert any(w.kind == "projection_incomplete" for w in emitted)
    attempts = _jsonl(root / "repair_attempts.jsonl")
    auto = [a for a in attempts if a.get("action_id") == "selfcheck.l1.rebuild_projections"]
    assert auto
    assert auto[-1]["status"] == "applied_verified"
    assert auto[-1]["external_send_executed"] is False
    assert auto[-1]["shell_executed"] is False
    assert auto[-1]["lifecycle_mutated"] is False
    assert (root / "component_status.json").exists()


def test_l1_auto_repair_archives_expired_authorization_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    decision = observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    plan = decision.repair_plans[0]
    auth = authorize_repair_plan(plan.id, request_digest=plan.request_digest, actor="operator", ttl_seconds=1)

    import agent.selfcheck_operator as sco

    monkeypatch.setattr(sco, "_now", lambda: 9999999999.0)
    first = run_sentinel_tick(source="test", debounce_seconds=0)
    second = run_sentinel_tick(source="test", debounce_seconds=0)

    assert any(w.kind == "authorization_expired" for w in first)
    assert any(w.kind == "authorization_expired" for w in second)
    archived = _jsonl(root := tmp_path / "state" / "selfcheck" / "archived_authorizations.jsonl")
    assert len([a for a in archived if a["authorization_id"] == auth.id]) == 1
    attempts = _jsonl((tmp_path / "state" / "selfcheck") / "repair_attempts.jsonl")
    auto = [a for a in attempts if a.get("action_id") == "selfcheck.l1.archive_expired_authorizations"]
    assert len(auto) == 1
    assert auto[0]["adapter_mutated"] is False


def test_l1_auto_repair_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_AUTO_REPAIR", "0")
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    root = tmp_path / "state" / "selfcheck"
    (root / "component_status.json").unlink()

    emitted = run_sentinel_tick(source="test", debounce_seconds=0)

    assert any(w.kind == "projection_incomplete" for w in emitted)
    attempts_path = root / "repair_attempts.jsonl"
    attempts = _jsonl(attempts_path) if attempts_path.exists() else []
    assert not [a for a in attempts if a.get("mode") == "l1_auto"]


def test_pending_alerts_auto_create_local_notification_outbox_and_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    root = tmp_path / "state" / "selfcheck"
    outbox = _jsonl(root / "notification_outbox.jsonl")
    authorizations = _jsonl(root / "delivery_authorizations.jsonl")
    dry_runs = _jsonl(root / "delivery_dry_runs.jsonl")
    assert len(outbox) >= 1
    assert outbox[0]["channel"] == "local"
    assert outbox[0]["target_id"] == "selfcheck-local"
    assert outbox[0]["payload_body_stored"] is False
    assert outbox[0]["external_send_executed"] is False
    assert len(authorizations) == len(outbox)
    assert authorizations[0]["decision"] == "approved"
    assert authorizations[0]["token_read"] is False
    assert len(dry_runs) == len(outbox)
    assert dry_runs[0]["status"] == "local_delivery_dry_run_verified"
    assert dry_runs[0]["send_executed"] is False
    assert dry_runs[0]["token_read"] is False
    assert read_sentinel_status()["notification_outbox_tail"] == len(outbox)
    assert read_sentinel_status()["local_delivery_dry_runs_tail"] == len(dry_runs)


def test_notification_outbox_is_idempotent_and_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    root = tmp_path / "state" / "selfcheck"
    before_outbox = len(_jsonl(root / "notification_outbox.jsonl"))
    before_dry_runs = len(_jsonl(root / "delivery_dry_runs.jsonl"))
    assert before_outbox >= 1
    assert before_dry_runs == before_outbox
    run_sentinel_tick(source="test")
    assert len(_jsonl(root / "notification_outbox.jsonl")) == before_outbox
    assert len(_jsonl(root / "delivery_dry_runs.jsonl")) == before_dry_runs

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "disabled"))
    monkeypatch.setenv("HERMES_SELFCHECK_NOTIFICATION_OUTBOX", "0")
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    disabled_root = tmp_path / "disabled" / "state" / "selfcheck"
    assert not (disabled_root / "notification_outbox.jsonl").exists()
    assert not (disabled_root / "delivery_dry_runs.jsonl").exists()


def test_repeated_fingerprint_wakeup_ignores_sentinel_debounce_bookkeeping(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    for _ in range(4):
        run_sentinel_tick(source="test", debounce_seconds=300)

    root = tmp_path / "state" / "selfcheck"
    coalesced = _jsonl(root / "coalesced.jsonl")
    assert any(c.get("reason") == "debounced_recent_wakeup" for c in coalesced)
    wakeups = _jsonl(root / "wakeups.jsonl")
    assert not [w for w in wakeups if w.get("kind") == "repeated_fingerprint"]


def test_telegram_delivery_tick_does_not_auto_retry_failed_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng")
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_LIVE_SEND", "1")
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    calls = []

    def failing_sender(payload):
        calls.append(payload)
        return '{"success": false, "error": "rate limited"}'

    first = run_telegram_delivery_tick(sender=failing_sender)
    second = run_telegram_delivery_tick(sender=failing_sender)

    assert first
    assert not second
    assert len(calls) == len(first)
    receipts = _jsonl(tmp_path / "state" / "selfcheck" / "delivery_receipts.jsonl")
    assert receipts[0]["status"] == "send_failed"
    assert receipts[0]["external_send_executed"] is False


def test_telegram_delivery_tick_respects_batch_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng")
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_LIVE_SEND", "1")
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_BATCH_LIMIT", "2")
    root = tmp_path / "state" / "selfcheck"
    root.mkdir(parents=True)
    for idx in range(5):
        payload_hash = f"payload-{idx}"
        outbox_id = f"outbox-{idx}"
        dry_id = f"delivery-dry-run-{idx}"
        with (root / "notification_outbox.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": outbox_id,
                "channel": "telegram",
                "target_id": "telegram:he cheng",
                "source_alert_id": f"wakeup-{idx}",
                "payload_hash": payload_hash,
                "payload_summary": f"alert {idx}",
            }) + "\n")
        with (root / "delivery_dry_runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": dry_id,
                "status": "telegram_delivery_dry_run_verified",
                "channel": "telegram",
                "target_id": "telegram:he cheng",
                "outbox_id": outbox_id,
                "payload_hash": payload_hash,
                "delivery_authorization_id": f"delivery-auth-{idx}",
            }) + "\n")

    calls = []
    emitted = run_telegram_delivery_tick(sender=lambda payload: calls.append(payload) or '{"success": true}')

    assert len(emitted) == 2
    assert len(calls) == 2
    receipts = _jsonl(root / "delivery_receipts.jsonl")
    assert len(receipts) == 2


def test_telegram_delivery_tick_sends_verified_dry_run_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng")
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_LIVE_SEND", "1")
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    calls = []

    def fake_sender(payload):
        calls.append(payload)
        return '{"success": true, "message_id": 42}'

    first = run_telegram_delivery_tick(sender=fake_sender)
    second = run_telegram_delivery_tick(sender=fake_sender)

    assert first
    assert not second
    assert len(calls) == len(first)
    assert calls[0]["target"] == "telegram:he cheng"
    root = tmp_path / "state" / "selfcheck"
    receipts = _jsonl(root / "delivery_receipts.jsonl")
    assert receipts[0]["status"] == "sent_verified"
    assert receipts[0]["external_send_executed"] is True
    assert receipts[0]["token_value_persisted"] is False
    assert receipts[0]["payload_body_persisted"] is False


def test_telegram_delivery_tick_is_default_off_for_live_platform_send(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng")
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")

    calls = []
    assert run_telegram_delivery_tick(sender=lambda payload: calls.append(payload) or '{"success": true}') == []
    assert calls == []


def test_telegram_delivery_tick_respects_capability_and_target_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_TARGET", "telegram:he cheng")
    observe_gateway_delivery(platform="telegram", success=False, error="send failed")
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_LIVE_SEND", "0")
    assert run_telegram_delivery_tick(sender=lambda payload: '{"success": true}') == []

    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_LIVE_SEND", "1")
    monkeypatch.setenv("HERMES_SELFCHECK_TELEGRAM_TARGET", "slack:general")
    try:
        run_telegram_delivery_tick(sender=lambda payload: '{"success": true}')
    except ValueError as exc:
        assert "Telegram target" in str(exc)
    else:
        raise AssertionError("expected invalid target to be rejected")
