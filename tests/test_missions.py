from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
    assert doctor["next_step"] == f"hermes mission dispatch-events {mission.mission_id}"
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
    assert alert["issues"][0]["recommended_command"] == f"hermes mission dispatch-events {mission.mission_id} --run-due --json"
    assert len(alert["stale_flow_signals"]) == 1


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
