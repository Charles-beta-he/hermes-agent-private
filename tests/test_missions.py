from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.missions import (
    MissionBudget,
    MissionStore,
    install_mission_tick_script,
    mission_tick_script_name,
    render_decision_next_command,
)


def test_mission_cron_script_emits_prompt_and_consumes_budget(tmp_path: Path) -> None:
    db = tmp_path / "missions.db"
    scripts = tmp_path / "scripts"
    store = MissionStore(db)
    mission = store.create_mission(
        direction="持续推进一个高抽象方向",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=1, max_turns_per_tick=3),
        success_criteria=["输出证据化阶段报告"],
        enable=True,
    )
    script = install_mission_tick_script(mission.mission_id, scripts_dir=scripts)
    assert script.name == mission_tick_script_name(mission.mission_id)
    assert script.exists()

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**__import__("os").environ, "HERMES_HOME": str(tmp_path)},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "[Hermes Mission Tick]" in result.stdout
    assert mission.mission_id in result.stdout

    exhausted = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**__import__("os").environ, "HERMES_HOME": str(tmp_path)},
        timeout=30,
    )
    assert exhausted.returncode == 0, exhausted.stderr
    assert "budget exhausted" in exhausted.stdout.lower()


def test_install_cron_creates_agent_job_with_dynamic_tick_script(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Import after HERMES_HOME is set because cron.jobs stores its home at import time.
    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "HERMES_DIR", tmp_path)
    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Hermes 自进化长期方向",
        cadence="every 2h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )

    result = store.install_cron(
        mission.mission_id,
        schedule="every 3h",
        deliver="local",
        enabled_toolsets=["terminal", "file"],
    )

    script = Path(result["script"])
    assert script.exists()
    job = result["job"]
    assert job["script"] == script.name
    assert job["no_agent"] is False
    assert job["schedule_display"] == "every 180m"
    assert job["enabled_toolsets"] == ["terminal", "file"]
    assert result["event_dispatch"]["status"] == "dispatched"
    jobs = json.loads((tmp_path / "cron" / "jobs.json").read_text())
    assert jobs["jobs"][0]["id"] == job["id"]
    assert store.count_events(mission.mission_id, {"mission_cron_installed"}) == 1

    blocked = store.install_cron
    try:
        blocked(mission.mission_id, profile="other")
    except ValueError as exc:
        assert "--profile" in str(exc)
    else:
        raise AssertionError("profile-specific install should fail closed until profile script home is supported")


def test_stage_report_and_handoff_are_durable_ledger_gates(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="长期方向",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )

    event = store.append_stage_report(
        mission.mission_id,
        summary="完成一轮真实验证",
        evidence=["uv run pytest -q tests/test_missions.py => 2 passed"],
        risks=["仍需端到端 cron runner 测试"],
        next_step="补齐自动回写",
    )
    assert event["event_type"] == "stage_report"
    assert store.count_events(mission.mission_id, {"stage_report"}) == 1
    report = store.report(mission.mission_id)
    assert "Evidence:" in report
    assert "uv run pytest -q tests/test_missions.py => 2 passed" in report
    assert "Risks:" in report
    assert "仍需端到端 cron runner 测试" in report
    assert "Next step: 补齐自动回写" in report

    decision = store.request_decision(
        mission.mission_id,
        "是否允许安装默认本地 cron？",
        ["允许", "暂不"],
        "允许",
    )
    assert decision.status == "open"
    current = store.get_mission(mission.mission_id)
    assert current is not None
    assert current.status == "blocked_on_human"
    tick = store.tick(mission.mission_id, dry_run=False)
    assert tick["status"] == "blocked_on_human"
    assert tick["prompt"] == ""


def test_mission_cron_script_does_not_wake_agent_while_blocked_on_human(tmp_path: Path) -> None:
    db = tmp_path / "missions.db"
    scripts = tmp_path / "scripts"
    store = MissionStore(db)
    mission = store.create_mission(
        direction="等待人工选择后继续",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    decision = store.request_decision(
        mission.mission_id,
        "选择后续路线？",
        ["A", "B"],
        "A",
    )
    script = install_mission_tick_script(mission.mission_id, scripts_dir=scripts)

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**__import__("os").environ, "HERMES_HOME": str(tmp_path)},
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "decision_ids": [decision.decision_id],
        "status": "blocked_on_human",
        "wakeAgent": False,
    }


def test_mission_inbox_board_and_doctor_surface_next_actions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MissionStore(tmp_path / "missions.db")
    active = store.create_mission(
        direction="Active long direction for board",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    blocked = store.create_mission(
        direction="Blocked long direction for inbox",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    decision = store.request_decision(
        blocked.mission_id,
        "Choose route?",
        ["A", "B"],
        "A",
    )

    inbox = store.inbox()
    assert len(inbox) == 1
    assert inbox[0]["mission_id"] == blocked.mission_id
    assert inbox[0]["decide_command"] == render_decision_next_command(decision)

    board = {row["mission_id"]: row for row in store.board()}
    assert board[blocked.mission_id]["needs_user"] is True
    assert board[blocked.mission_id]["open_decision_id"] == decision.decision_id
    assert board[active.mission_id]["next_step"] == f"hermes mission install-cron {active.mission_id}"

    doctor = store.doctor(active.mission_id)
    assert doctor["next_step"] == f"hermes mission install-cron {active.mission_id}"
    assert any(check["name"] == "tick script" and check["status"] == "warn" for check in doctor["checks"])


def test_mission_doctor_surfaces_stale_flow_signal_watchdog_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Event-driven mission",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    event = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "enqueue flow signal",
        {"source": "unit", "task_id": "task-stale", "to_status": "review"},
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE mission_flow_signals SET created_at = created_at - 3600 WHERE source_event_id = ?",
            (event["event_id"],),
        )
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "job-stale",
                        "name": f"Mission {mission.mission_id}",
                        "enabled": True,
                        "state": "idle",
                        "next_run_at": None,
                        "script": mission_tick_script_name(mission.mission_id),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    doctor = store.doctor(mission.mission_id)

    assert len(doctor["pending_flow_signals"]) == 2
    assert len(doctor["stale_flow_signals"]) == 1
    assert doctor["next_step"] == f"hermes mission recover-signals {mission.mission_id} --json"
    assert any(check["name"] == "stale flow signals" and check["status"] == "warn" for check in doctor["checks"])


def test_mission_watchdog_is_silent_when_healthy_and_alerts_on_stale_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Event-driven mission watchdog",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )

    healthy = store.watchdog(mission.mission_id)
    assert healthy["status"] == "ok"
    assert healthy["silent"] is True
    assert healthy["issues"] == []

    event = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "watchdog stale fixture",
        {"source": "unit", "task_id": "task-stale", "to_status": "review"},
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE mission_flow_signals SET created_at = created_at - 3600 WHERE source_event_id = ?",
            (event["event_id"],),
        )
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "job-watchdog",
                        "name": f"Mission {mission.mission_id}",
                        "enabled": True,
                        "state": "idle",
                        "next_run_at": None,
                        "script": mission_tick_script_name(mission.mission_id),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    alert = store.watchdog(mission.mission_id)
    assert alert["status"] == "alert"
    assert alert["silent"] is False
    assert alert["issues"][0]["code"] == "mission_flow_runner_stalled"
    assert alert["issues"][0]["recommended_command"] == f"hermes mission recover-signals {mission.mission_id} --older-than-seconds 900 --max-signals 200 --json"
    assert len(alert["stale_flow_signals"]) == 1


def test_mission_health_surfaces_active_mission_with_paused_cron(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Active mission with accidentally paused writer",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "job-paused",
                        "name": "paused mission writer",
                        "enabled": True,
                        "state": "paused",
                        "next_run_at": None,
                        "script": mission_tick_script_name(mission.mission_id),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    doctor = store.doctor(mission.mission_id)
    cron_state = next(check for check in doctor["checks"] if check["name"] == "cron runnable")
    assert cron_state == {"name": "cron runnable", "status": "warn", "detail": "0/1 enabled matching job(s)"}

    watchdog = store.watchdog(mission.mission_id)
    assert watchdog["status"] == "alert"
    assert watchdog["silent"] is False
    assert watchdog["issues"] == [
        {
            "code": "mission_flow_scheduler_paused",
            "message": "mission is active, but every matching mission cron job is disabled or paused",
            "recommended_command": f"hermes mission doctor {mission.mission_id} --json",
        }
    ]

    import cron.jobs as cron_jobs

    trigger_calls: list[str] = []
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or {"id": job_id})
    dispatch = store.dispatch_pending_flow_signals(mission.mission_id)
    assert dispatch["status"] == "pending_no_cron"
    assert trigger_calls == []
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 0


def test_mission_watchdog_alerts_on_abandoned_executable_tick(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Detect a scheduled session that ended before review completion",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    tick = store.tick(mission.mission_id, dry_run=False)
    with store._connect() as conn:
        conn.execute(
            "UPDATE mission_events SET created_at = created_at - 3600 WHERE event_type = 'tick_prompt_created'"
        )

    watchdog = store.watchdog(mission.mission_id, older_than_seconds=900)
    assert tick["continuation_token"]
    assert watchdog["status"] == "alert"
    assert any(issue["code"] == "mission_tick_stalled" for issue in watchdog["issues"])
    doctor = store.doctor(mission.mission_id)
    stalled = next(check for check in doctor["checks"] if check["name"] == "stalled executable tick")
    assert stalled["status"] == "warn"

    store.append_stage_report(
        mission.mission_id,
        summary="durable closure arrived",
        evidence=["review result receipt"],
        next_step="continue safely",
    )
    assert store.watchdog(mission.mission_id, older_than_seconds=900)["status"] == "ok"


def test_scheduler_rejection_releases_dispatch_claim_for_safe_retry(tmp_path: Path, monkeypatch) -> None:
    import cron.jobs as cron_jobs

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Do not poison event flow when scheduler rejects before any effect",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-rejected", "enabled": True, "state": "idle"}],
    )
    trigger_calls: list[str] = []
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or False)

    first = store.dispatch_pending_flow_signals(mission.mission_id)
    second = store.dispatch_pending_flow_signals(mission.mission_id)

    assert first["reason"] == "scheduler_trigger_rejected"
    assert second["reason"] == "scheduler_trigger_rejected"
    assert trigger_calls == ["job-rejected", "job-rejected"]
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_started"}) == 0
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_rejected"}) == 2
    assert store._pending_flow_dispatch_outcome_unknown(mission.mission_id) is None


def test_recover_flow_signal_backlog_is_dry_run_then_audited_local_apply(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Governed recovery fixture",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    fresh = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "fresh signal should stay pending",
        {"source": "unit", "task_id": "fresh"},
    )
    stale = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "stale signal should be recovered",
        {"source": "unit", "task_id": "stale"},
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE mission_flow_signals SET created_at = created_at - 3600 WHERE source_event_id = ?",
            (stale["event_id"],),
        )

    dry_run = store.recover_flow_signal_backlog(mission.mission_id, older_than_seconds=900, max_signals=5)
    assert dry_run["status"] == "dry_run"
    assert dry_run["selected_count"] == 1
    assert dry_run["after_pending"] == dry_run["before_pending"]
    assert dry_run["runner"] == {"status": "not_requested", "why": "recovery is local-ledger only and non-recursive"}
    assert store.count_events(mission.mission_id, {"flow_signal_recovered"}) == 0

    applied = store.recover_flow_signal_backlog(
        mission.mission_id,
        older_than_seconds=900,
        max_signals=5,
        apply=True,
        reason="unit bounded recovery",
    )
    assert applied["status"] == "recovered"
    assert applied["selected_count"] == 1
    assert applied["after_pending"] == applied["before_pending"] - 1
    remaining_source_ids = {signal.source_event_id for signal in store.pending_flow_signals(mission.mission_id, limit=10)}
    assert fresh["event_id"] in remaining_source_ids
    assert stale["event_id"] not in remaining_source_ids
    recovered_events = store.list_events_by_type(mission.mission_id, "flow_signal_recovered", limit=1)
    assert recovered_events[0]["payload"]["reason"] == "unit bounded recovery"
    assert recovered_events[0]["payload"]["runner"]["why"] == "recovery is local-ledger only and non-recursive"


def test_review_completed_event_is_actionable_flow_signal(tmp_path: Path, monkeypatch) -> None:
    import cron.jobs as cron_jobs

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Resume immediately after exact-candidate review",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    store.tick(mission.mission_id, dry_run=False)
    trigger_calls: list[str] = []
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-review", "enabled": True}],
    )
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or True)

    event = store.append_review_completion(
        mission.mission_id,
        verdict="APPROVE",
        candidate_digest="sha256:abc123",
        evidence=["/tmp/review-verdict.json"],
        reviewer="isolated-read-only-reviewer",
    )

    assert event["event_type"] == "review_completed"
    assert event["payload"] == {
        "candidate_digest": "sha256:abc123",
        "evidence": ["/tmp/review-verdict.json"],
        "reviewer": "isolated-read-only-reviewer",
        "verdict": "APPROVE",
    }
    pending = store.pending_flow_signals(mission.mission_id)
    assert len(pending) == 1
    assert pending[0].source_event_id == event["event_id"]
    assert pending[0].signal_type == "review_completed"

    duplicate = store.append_review_completion(
        mission.mission_id,
        verdict="APPROVE",
        candidate_digest="sha256:abc123",
        evidence=["/tmp/review-verdict.json"],
        reviewer="isolated-read-only-reviewer",
    )
    assert duplicate["event_id"] == event["event_id"]
    assert store.count_events(mission.mission_id, {"review_completed"}) == 1
    assert len(store.pending_flow_signals(mission.mission_id)) == 1
    assert trigger_calls == ["job-review"]
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 1
    refreshed = store.get_mission(mission.mission_id)
    assert refreshed is not None
    assert refreshed.status == "active"


def test_review_completed_exact_duplicates_are_atomic_under_concurrency(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Deduplicate concurrent exact-candidate review retries",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    store.tick(mission.mission_id, dry_run=False)
    barrier = threading.Barrier(8)

    def submit() -> dict[str, object]:
        barrier.wait()
        return store.append_review_completion(
            mission.mission_id,
            verdict="APPROVE",
            candidate_digest="sha256:concurrent",
            evidence=["/tmp/review-verdict.json"],
            reviewer="isolated-read-only-reviewer",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(lambda _index: submit(), range(8)))

    assert len({str(event["event_id"]) for event in events}) == 1
    assert store.count_events(mission.mission_id, {"review_completed"}) == 1
    assert len(store.pending_flow_signals(mission.mission_id)) == 1


def test_tick_bound_continuation_is_actionable_but_stage_report_is_not(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Advance successful slices without cadence polling",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=2),
        enable=True,
    )

    tick = store.tick(mission.mission_id, dry_run=False)

    token = tick["continuation_token"]
    assert token.startswith("ctn_")
    stage_command = f"hermes mission stage-report {mission.mission_id}"
    continue_command = f"hermes mission continue {mission.mission_id} --continuation-token {token}"
    assert stage_command in tick["prompt"]
    assert continue_command in tick["prompt"]
    assert '--evidence "stage_report:<event_id>"' in tick["prompt"]
    assert "Do not leave a required review in ephemeral background delegation" in tick["prompt"]
    assert "Complete it synchronously or through a durable external process" in tick["prompt"]
    assert tick["prompt"].index(stage_command) < tick["prompt"].index(continue_command)
    report = store.append_stage_report(
        mission.mission_id,
        summary="slice closed with fresh evidence",
        evidence=["pytest => passed"],
        next_step="select the next dependency-safe slice",
    )
    assert store.pending_flow_signals(mission.mission_id) == []
    continuation_evidence = [f"stage_report:{report['event_id']}", "pytest => passed"]

    event = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step="select the next dependency-safe slice",
        evidence=continuation_evidence,
    )

    assert event["event_type"] == "continuation_requested"
    assert event["payload"] == {
        "continuation_token": token,
        "evidence": continuation_evidence,
        "next_step": "select the next dependency-safe slice",
    }
    pending = store.pending_flow_signals(mission.mission_id)
    assert [signal.signal_type for signal in pending] == ["continuation_requested"]
    resumed = store.tick(mission.mission_id, dry_run=False)
    assert resumed["mode"] == "event"
    assert resumed["signal"]["source_event_id"] == event["event_id"]
    assert store.pending_flow_signals(mission.mission_id) == []
    late_replay = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step="select the next dependency-safe slice",
        evidence=continuation_evidence,
    )
    assert late_replay["event_id"] == event["event_id"]


def test_continuation_rejects_stale_tick_and_semantic_conflict(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Fence stale Mission writers",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    stale = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    current = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    report = store.append_stage_report(
        mission.mission_id,
        summary="current tick closed",
        evidence=["current writer receipt"],
        next_step="continue current slice chain",
    )
    evidence = [f"stage_report:{report['event_id']}"]

    with pytest.raises(ValueError, match="stale continuation token"):
        store.append_continuation_request(
            mission.mission_id,
            continuation_token=stale,
            next_step="must not wake from the stale writer",
            evidence=["stale writer receipt"],
        )

    first = store.append_continuation_request(
        mission.mission_id,
        continuation_token=current,
        next_step="continue current slice chain",
        evidence=evidence,
    )
    duplicate = store.append_continuation_request(
        mission.mission_id,
        continuation_token=current,
        next_step="continue current slice chain",
        evidence=evidence,
    )
    assert duplicate["event_id"] == first["event_id"]

    with pytest.raises(ValueError, match="continuation idempotency conflict"):
        store.append_continuation_request(
            mission.mission_id,
            continuation_token=current,
            next_step="silently replace the selected next step",
            evidence=["different receipt"],
        )


def test_continuation_exact_replay_is_atomic_under_concurrency(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Deduplicate concurrent continuation callbacks",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    report = store.append_stage_report(
        mission.mission_id,
        summary="concurrent source tick closed",
        evidence=["concurrent callback receipt"],
        next_step="one canonical successor",
    )
    barrier = threading.Barrier(8)

    def submit() -> dict[str, object]:
        barrier.wait()
        return store.append_continuation_request(
            mission.mission_id,
            continuation_token=token,
            next_step="one canonical successor",
            evidence=[f"stage_report:{report['event_id']}"],
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(lambda _index: submit(), range(8)))

    assert len({str(event["event_id"]) for event in events}) == 1
    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 1
    assert len(store.pending_flow_signals(mission.mission_id)) == 1


def test_exact_continuation_replay_does_not_redispatch_scheduler(tmp_path: Path, monkeypatch) -> None:
    import cron.jobs as cron_jobs

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Replay is a receipt read, not a new scheduler side effect",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    report = store.append_stage_report(
        mission.mission_id,
        summary="source tick closed",
        evidence=["stable receipt"],
        next_step="one successor",
    )
    evidence = [f"stage_report:{report['event_id']}"]
    trigger_calls: list[str] = []
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-exact", "enabled": True}],
    )
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or True)

    first = store.append_continuation_request(
        mission.mission_id, continuation_token=token, next_step="one successor", evidence=evidence
    )
    replay = store.append_continuation_request(
        mission.mission_id, continuation_token=token, next_step="one successor", evidence=evidence
    )

    assert replay["event_id"] == first["event_id"]
    assert trigger_calls == ["job-exact"]
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 1


@pytest.mark.parametrize("blocked_state", ["paused", "blocked_on_human"])
def test_continuation_rejects_non_active_or_human_blocked_mission(tmp_path: Path, blocked_state: str) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Stop at governance gates",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    if blocked_state == "paused":
        store.set_status(mission.mission_id, "paused", reason="unit boundary")
    else:
        store.request_decision(mission.mission_id, "Need authority?", ["stop"], "stop")

    with pytest.raises(ValueError, match="continuation requires an active mission with no open decisions"):
        store.append_continuation_request(
            mission.mission_id,
            continuation_token=token,
            next_step="must stay closed",
            evidence=["boundary receipt"],
        )
    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 0


def test_continuation_rejects_exhausted_tick_budget_before_status_projection(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Never wake beyond the durable tick budget",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=1),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    active = store.get_mission(mission.mission_id)
    assert active is not None
    assert active.status == "active"
    assert store.count_events(mission.mission_id, {"tick_prompt_created"}) == 1

    with pytest.raises(ValueError, match="continuation requires remaining tick budget"):
        store.append_continuation_request(
            mission.mission_id,
            continuation_token=token,
            next_step="must not schedule beyond the final tick",
            evidence=["final tick receipt"],
        )

    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 0
    assert store.pending_flow_signals(mission.mission_id) == []


def test_actionable_event_and_signal_commit_atomically(tmp_path: Path, monkeypatch) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Do not strand actionable events across a process crash",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )

    def fail_transaction_external_enqueue(_event):
        raise AssertionError("new actionable events must not depend on transaction-external enqueue")

    monkeypatch.setattr(store, "enqueue_flow_signal", fail_transaction_external_enqueue)
    event = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "atomic event and signal",
        {"task_id": "task-atomic", "to_status": "review"},
    )

    pending = [
        signal
        for signal in store.pending_flow_signals(mission.mission_id)
        if signal.source_event_id == event["event_id"]
    ]
    assert len(pending) == 1
    enqueued = [
        item
        for item in store.list_events(mission.mission_id, limit=20)
        if item["event_type"] == "flow_signal_enqueued"
        and item["payload"]["source_event_id"] == event["event_id"]
    ]
    assert len(enqueued) == 1
    assert enqueued[0]["payload"]["source_event_id"] == event["event_id"]


def test_actionable_event_rolls_back_if_atomic_signal_write_fails(tmp_path: Path, monkeypatch) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Rollback the source event when its signal cannot commit",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )

    def fail_atomic_signal_write(_conn, _event):
        raise RuntimeError("injected atomic signal failure")

    monkeypatch.setattr(store, "_enqueue_flow_signal_in_transaction", fail_atomic_signal_write)
    with pytest.raises(RuntimeError, match="injected atomic signal failure"):
        store.append_event(
            mission.mission_id,
            "taskpool_status_changed",
            "must roll back",
            {"task_id": "task-rollback", "to_status": "review"},
        )

    assert store.count_events(mission.mission_id, {"taskpool_status_changed"}) == 0
    assert all(signal.signal_type != "taskpool_status_changed" for signal in store.pending_flow_signals(mission.mission_id))


def test_exact_continuation_replay_repairs_legacy_missing_signal(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Repair a pre-atomic crash gap on exact replay",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    report = store.append_stage_report(
        mission.mission_id,
        summary="legacy source tick closed",
        evidence=["stable receipt"],
        next_step="repair missing signal",
    )
    evidence = [f"stage_report:{report['event_id']}"]
    original = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step="repair missing signal",
        evidence=evidence,
    )
    original_signal = store.pending_flow_signals(mission.mission_id)[0]
    original_enqueue_audits = store.count_events(mission.mission_id, {"flow_signal_enqueued"})
    with store._connect() as conn:
        conn.execute("DELETE FROM mission_flow_signals WHERE source_event_id = ?", (original["event_id"],))
    assert store.pending_flow_signals(mission.mission_id) == []

    replay = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step="repair missing signal",
        evidence=evidence,
    )

    assert replay["event_id"] == original["event_id"]
    repaired = store.pending_flow_signals(mission.mission_id)
    assert len(repaired) == 1
    assert repaired[0].source_event_id == original["event_id"]
    assert repaired[0].signal_id == original_signal.signal_id
    assert store.count_events(mission.mission_id, {"flow_signal_enqueued"}) == original_enqueue_audits


def test_legacy_schema_migration_is_serialized_across_concurrent_initializers(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "missions.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE mission_events (
                event_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mission_events (
                event_id, mission_id, event_type, summary, payload_json, created_at
            ) VALUES ('evt_legacy', 'msn_legacy', 'stage_report', 'preserve me', '{}', 1.0)
            """
        )

    barrier = threading.Barrier(2)

    class ConnectionProbe:
        def __init__(self) -> None:
            self.connection = sqlite3.connect(str(db))
            self.connection.row_factory = sqlite3.Row
            self.serialized = False

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, sql, parameters=()):
            if sql.strip().upper() == "BEGIN IMMEDIATE":
                self.serialized = True
            if sql.strip().upper().startswith("PRAGMA TABLE_INFO") and not self.serialized:
                barrier.wait(timeout=5)
            return self.connection.execute(sql, parameters)

    monkeypatch.setattr(MissionStore, "_connect", lambda _self: ConnectionProbe())
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(MissionStore, db) for _ in range(2)]
    errors = [future.exception() for future in futures if future.exception() is not None]

    assert errors == []
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(mission_events)")}
        preserved = conn.execute(
            "SELECT summary, idempotency_key FROM mission_events WHERE event_id = 'evt_legacy'"
        ).fetchone()
    assert "idempotency_key" in columns
    assert preserved == ("preserve me", None)


def test_dispatch_crash_after_scheduler_trigger_is_durable_unknown_not_retriggered(
    tmp_path: Path, monkeypatch
) -> None:
    import cron.jobs as cron_jobs

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Fail closed across the scheduler/audit crash window",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    trigger_calls: list[str] = []
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-unknown", "enabled": True}],
    )
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or {"id": job_id})

    def crash_before_success_audit(mission_id, event_type, *args, **kwargs):
        if event_type == "flow_signal_dispatched":
            raise RuntimeError("injected post-trigger crash")
        return MissionStore.append_event(store, mission_id, event_type, *args, **kwargs)

    monkeypatch.setattr(store, "append_event", crash_before_success_audit)
    with pytest.raises(RuntimeError, match="injected post-trigger crash"):
        MissionStore.append_event(
            store,
            mission.mission_id,
            "taskpool_status_changed",
            "scheduler crash-window probe",
            {"task_id": "task-unknown", "to_status": "review"},
            idempotency_key="task-unknown-review",
        )

    assert trigger_calls == ["job-unknown"]
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_started"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 0

    monkeypatch.undo()
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-unknown", "enabled": True}],
    )
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or {"id": job_id})
    with pytest.raises(RuntimeError, match="scheduler dispatch outcome unknown"):
        MissionStore.append_event(
            store,
            mission.mission_id,
            "taskpool_status_changed",
            "scheduler crash-window probe",
            {"task_id": "task-unknown", "to_status": "review"},
            idempotency_key="task-unknown-review",
        )
    result = store.dispatch_pending_flow_signals(mission.mission_id)

    assert result["status"] == "pending_blocked"
    assert result["reason"] == "dispatch_outcome_unknown"
    assert trigger_calls == ["job-unknown"]
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_started"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 0
    watchdog = store.watchdog(mission.mission_id)
    assert any(issue["code"] == "mission_flow_dispatch_outcome_unknown" for issue in watchdog["issues"])
    doctor = store.doctor(mission.mission_id)
    unknown = next(check for check in doctor["checks"] if check["name"] == "dispatch outcome unknown")
    assert unknown["status"] == "warn"


def test_typed_continuation_replay_surfaces_dispatch_outcome_unknown(tmp_path: Path, monkeypatch) -> None:
    import cron.jobs as cron_jobs

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Surface typed continuation scheduler ambiguity",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    next_step = "one bounded successor"
    report = store.append_stage_report(
        mission.mission_id,
        summary="typed unknown source closed",
        evidence=["temporary SQLite receipt"],
        next_step=next_step,
    )
    evidence = [f"stage_report:{report['event_id']}"]
    trigger_calls: list[str] = []
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-typed-unknown", "enabled": True}],
    )
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or {"id": job_id})

    def crash_before_success_audit(mission_id, event_type, *args, **kwargs):
        if event_type == "flow_signal_dispatched":
            raise RuntimeError("injected typed post-trigger crash")
        return MissionStore.append_event(store, mission_id, event_type, *args, **kwargs)

    monkeypatch.setattr(store, "append_event", crash_before_success_audit)
    with pytest.raises(RuntimeError, match="injected typed post-trigger crash"):
        MissionStore.append_continuation_request(
            store,
            mission.mission_id,
            continuation_token=token,
            next_step=next_step,
            evidence=evidence,
        )

    monkeypatch.undo()
    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-typed-unknown", "enabled": True}],
    )
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: trigger_calls.append(job_id) or {"id": job_id})
    with pytest.raises(RuntimeError, match="scheduler dispatch outcome unknown"):
        MissionStore.append_continuation_request(
            store,
            mission.mission_id,
            continuation_token=token,
            next_step=next_step,
            evidence=evidence,
        )

    store.set_status(mission.mission_id, "paused", reason="revoke executable authority")
    receipt = MissionStore.append_continuation_request(
        store,
        mission.mission_id,
        continuation_token=token,
        next_step=next_step,
        evidence=evidence,
    )

    assert receipt["event_type"] == "continuation_requested"
    assert trigger_calls == ["job-typed-unknown"]
    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_started"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 0


def test_concurrent_dispatch_claim_triggers_scheduler_once(tmp_path: Path, monkeypatch) -> None:
    import cron.jobs as cron_jobs

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Serialize scheduler claims for one pending signal",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    event = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "concurrent scheduler claim probe",
        {"task_id": "task-concurrent-dispatch", "to_status": "review"},
    )
    assert any(signal.source_event_id == event["event_id"] for signal in store.pending_flow_signals(mission.mission_id))
    trigger_calls: list[str] = []
    trigger_lock = threading.Lock()
    barrier = threading.Barrier(8)

    monkeypatch.setattr(
        store,
        "_find_cron_jobs_for_mission",
        lambda _mission_id: [{"id": "job-concurrent", "enabled": True}],
    )

    def trigger(job_id: str):
        with trigger_lock:
            trigger_calls.append(job_id)
        return {"id": job_id}

    monkeypatch.setattr(cron_jobs, "trigger_job", trigger)

    def dispatch(_index: int) -> dict[str, object]:
        barrier.wait()
        return store.dispatch_pending_flow_signals(mission.mission_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(dispatch, range(8)))

    assert trigger_calls == ["job-concurrent"]
    assert sum(result["status"] == "dispatched" for result in results) == 1
    assert {result["status"] for result in results} <= {
        "dispatched",
        "already_dispatched",
        "pending_blocked",
    }
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_started"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 1


def test_taskpool_status_event_is_actionable_flow_signal(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Hermes self-architecture mission",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )

    event = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "taskpool task moved to review",
        {
            "source": "taskpool",
            "task_id": "task-1",
            "from_status": "running",
            "to_status": "review",
            "lane": "evolution",
            "architecture_target": "local-brain",
        },
    )

    pending = [signal for signal in store.pending_flow_signals(mission.mission_id) if signal.source_event_id == event["event_id"]]
    assert len(pending) == 1
    assert pending[0].source_event_id == event["event_id"]
    assert pending[0].signal_type == "taskpool_status_changed"
    assert pending[0].payload["event_payload"]["task_id"] == "task-1"


def test_dispatch_events_run_due_invokes_governed_cron_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import cron.jobs as cron_jobs
    import cron.scheduler as cron_scheduler

    monkeypatch.setattr(cron_jobs, "HERMES_DIR", tmp_path)
    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")

    ran: list[dict[str, object]] = []

    def fake_tick(*, verbose: bool = True, sync: bool = True, **_kwargs) -> int:
        ran.append({"verbose": verbose, "sync": sync})
        return 1

    monkeypatch.setattr(cron_scheduler, "tick", fake_tick)

    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Event-driven mission closes through real runner",
        cadence="every 1h",
        deliver="local",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    installed = store.install_cron(mission.mission_id, schedule="every 1h", deliver="local")
    event = store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "real runner smoke",
        {"evidence": "unit fixture", "task_id": "task-run-due", "to_status": "review"},
    )

    result = store.dispatch_pending_flow_signals(mission.mission_id, run_due=True)

    assert result["status"] == "dispatched"
    assert installed["job"]["id"] in result["triggered_jobs"]
    assert result["runner"] == {"status": "ran", "executed_jobs": 1}
    assert ran == [{"verbose": False, "sync": True}]
    events = store.list_events(mission.mission_id, limit=5)
    dispatched = [item for item in events if item["event_type"] == "flow_signal_dispatched"]
    assert dispatched
    assert dispatched[0]["payload"]["run_due"] is True
    assert dispatched[0]["payload"]["runner"] == {"status": "ran", "executed_jobs": 1}
    assert any(signal.source_event_id == event["event_id"] for signal in store.pending_flow_signals(mission.mission_id))


def test_generic_event_sink_rejects_typed_continuation_bypass(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Typed continuation is the only admission sink",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=1),
        enable=True,
    )
    store.tick(mission.mission_id, dry_run=False)

    with pytest.raises(ValueError, match="append_continuation_request"):
        store.append_event(
            mission.mission_id,
            "continuation_requested",
            "forged generic continuation",
            {},
        )

    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 0
    assert all(signal.signal_type != "continuation_requested" for signal in store.pending_flow_signals(mission.mission_id))


@pytest.mark.parametrize("invalid_kind", ["missing", "old", "cross_mission", "needs_user", "next_step_drift"])
def test_continuation_requires_current_tick_bound_stage_report(tmp_path: Path, invalid_kind: str) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Bind continuation to real current-tick evidence",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    old_report = store.append_stage_report(
        mission.mission_id,
        summary="old report",
        evidence=["old evidence"],
        next_step="next safe slice",
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    other = store.create_mission(direction="other mission", cadence="every 1h", budget=MissionBudget(max_ticks=2), enable=True)
    if invalid_kind == "missing":
        evidence = ["stage_report:evt-does-not-exist"]
    elif invalid_kind == "old":
        evidence = [f"stage_report:{old_report['event_id']}"]
    elif invalid_kind == "cross_mission":
        other_report = store.append_stage_report(
            other.mission_id,
            summary="cross mission report",
            evidence=["other evidence"],
            next_step="next safe slice",
        )
        evidence = [f"stage_report:{other_report['event_id']}"]
    else:
        report = store.append_stage_report(
            mission.mission_id,
            summary="current report",
            evidence=["current evidence"],
            next_step="different step" if invalid_kind == "next_step_drift" else "next safe slice",
            needs_user=invalid_kind == "needs_user",
        )
        evidence = [f"stage_report:{report['event_id']}"]

    with pytest.raises(ValueError, match="current tick stage report"):
        store.append_continuation_request(
            mission.mission_id,
            continuation_token=token,
            next_step="next safe slice",
            evidence=evidence,
        )

    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 0


def test_tick_budget_allocation_is_atomic_under_concurrency(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Never overallocate the hard tick budget",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=1),
        enable=True,
    )
    barrier = threading.Barrier(16)

    def allocate(_index: int) -> str:
        barrier.wait()
        return str(store.tick(mission.mission_id, dry_run=False)["status"])

    with ThreadPoolExecutor(max_workers=16) as executor:
        statuses = list(executor.map(allocate, range(16)))

    assert statuses.count("prompt_created") == 1
    assert store.count_events(mission.mission_id, {"tick_prompt_created"}) == 1


@pytest.mark.parametrize("blocked_after_accept", ["paused", "budget_exhausted"])
def test_exact_replay_does_not_repair_executable_signal_after_revocation(tmp_path: Path, blocked_after_accept: str) -> None:
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Replay receipts without reviving revoked execution",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=2),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    report = store.append_stage_report(
        mission.mission_id,
        summary="accepted source report",
        evidence=["fresh readback"],
        next_step="bounded successor",
    )
    evidence = [f"stage_report:{report['event_id']}"]
    original = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step="bounded successor",
        evidence=evidence,
    )
    with store._connect() as conn:
        conn.execute("DELETE FROM mission_flow_signals WHERE source_event_id = ?", (original["event_id"],))
    if blocked_after_accept == "paused":
        store.set_status(mission.mission_id, "paused", reason="operator revoked continuation")
    else:
        assert store.tick(mission.mission_id, dry_run=False)["status"] == "prompt_created"

    replay = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step="bounded successor",
        evidence=evidence,
    )

    assert replay["event_id"] == original["event_id"]
    assert all(signal.source_event_id != original["event_id"] for signal in store.pending_flow_signals(mission.mission_id))


def test_dispatch_matches_only_enabled_exact_mission_script(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "HERMES_DIR", tmp_path)
    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="Wake only the exact installed runner",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    installed = store.install_cron(mission.mission_id, schedule="every 1h", deliver="local")
    for signal in store.pending_flow_signals(mission.mission_id):
        store.consume_flow_signal(signal.signal_id)
    jobs_path = tmp_path / "cron" / "jobs.json"
    raw = json.loads(jobs_path.read_text())
    raw["jobs"].append(
        {
            "id": "disabled-decoy",
            "name": f"unrelated {mission.mission_id}",
            "prompt": f"mentions {mission.mission_id}",
            "script": "evil.py",
            "enabled": False,
            "state": "paused",
        }
    )
    jobs_path.write_text(json.dumps(raw))
    triggered: list[str] = []
    monkeypatch.setattr(cron_jobs, "trigger_job", lambda job_id: triggered.append(job_id) or True)

    store.append_event(
        mission.mission_id,
        "taskpool_status_changed",
        "wake exact job",
        {"task_id": "exact-job", "to_status": "review"},
    )

    assert triggered == [installed["job"]["id"]]

    raw = json.loads(jobs_path.read_text())
    duplicate = dict(raw["jobs"][0])
    duplicate["id"] = "duplicate-enabled-exact-script"
    duplicate["enabled"] = True
    raw["jobs"].append(duplicate)
    jobs_path.write_text(json.dumps(raw))
    triggered.clear()

    blocked = store.dispatch_pending_flow_signals(mission.mission_id)

    assert blocked["status"] == "pending_blocked"
    assert blocked["reason"] == "ambiguous_enabled_mission_jobs"
    assert triggered == []


def test_real_cli_process_propagates_mission_error_exit_code(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "mission",
            "--db",
            str(tmp_path / "missions.db"),
            "continue",
            "missing",
            "--continuation-token",
            "ctn_missing",
            "--next-step",
            "must fail",
            "--evidence",
            "stage_report:evt_missing",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Error:" in result.stdout


def test_real_cli_process_maps_dispatch_unknown_to_semantic_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "missions.db"
    store = MissionStore(db_path)
    mission = store.create_mission(
        direction="CLI reports durable dispatch ambiguity without traceback",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    token = store.tick(mission.mission_id, dry_run=False)["continuation_token"]
    next_step = "bounded CLI successor"
    report = store.append_stage_report(
        mission.mission_id,
        summary="CLI source closed",
        evidence=["temporary SQLite readback"],
        next_step=next_step,
    )
    evidence = f"stage_report:{report['event_id']}"
    event = store.append_continuation_request(
        mission.mission_id,
        continuation_token=token,
        next_step=next_step,
        evidence=[evidence],
    )
    signal = next(
        item for item in store.pending_flow_signals(mission.mission_id) if item.source_event_id == event["event_id"]
    )
    assert store._claim_flow_signal_dispatch(signal, job_id="job-cli-unknown", run_due=False) == "claimed"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "mission",
            "--db",
            str(db_path),
            "continue",
            mission.mission_id,
            "--continuation-token",
            token,
            "--next-step",
            next_step,
            "--evidence",
            evidence,
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Error: scheduler dispatch outcome unknown" in result.stdout
    assert "Traceback" not in result.stderr
    assert store.count_events(mission.mission_id, {"continuation_requested"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatch_started"}) == 1
    assert store.count_events(mission.mission_id, {"flow_signal_dispatched"}) == 0
