from __future__ import annotations

from pathlib import Path

import pytest

from cron.jobs import get_job
from hermes_cli import kanban_db as kb


@pytest.fixture()
def isolated_control_planes(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    kb.init_db()
    return tmp_path


def test_cron_job_run_controller_normalizes_launch_pause_resume_status_cancel(isolated_control_planes):
    from agent.run_controller import CronJobRunController, RunRequest, RunState

    controller = CronJobRunController()
    launched = controller.launch(
        RunRequest(
            trigger="cron.test",
            objective="collect daily digest",
            payload={"schedule": "every 1h", "name": "digest"},
        )
    )

    assert launched.surface == "cron"
    assert launched.state is RunState.SCHEDULED
    assert launched.run_id.startswith("cron:")
    assert get_job(launched.native_id)["name"] == "digest"

    paused = controller.pause(launched.run_id, reason="operator hold")
    assert paused.state is RunState.PAUSED
    assert get_job(launched.native_id)["paused_reason"] == "operator hold"

    resumed = controller.resume(launched.run_id)
    assert resumed.state is RunState.SCHEDULED

    triggered = controller.status(launched.run_id)
    assert triggered.native_id == launched.native_id
    assert triggered.trace["native_state"] == "scheduled"

    cancelled = controller.cancel(launched.run_id, reason="not needed")
    assert cancelled.state is RunState.CANCELLED
    assert get_job(launched.native_id) is None


def test_kanban_run_controller_uses_existing_lifecycle_and_keeps_run_mapping(isolated_control_planes):
    from agent.run_controller import KanbanTaskRunController, RunRequest, RunState

    controller = KanbanTaskRunController(assignee="worker-a")
    launched = controller.launch(RunRequest(trigger="gateway", objective="ship a patch"))
    assert launched.surface == "kanban"
    assert launched.state is RunState.READY
    assert launched.run_id == f"kanban:{launched.native_id}"

    with kb.connect() as conn:
        claimed = kb.claim_task(conn, launched.native_id, claimer="test-worker")
        assert claimed is not None
        native_run_id = kb.get_task(conn, launched.native_id).current_run_id

    running = controller.status(launched.run_id)
    assert running.state is RunState.RUNNING
    assert running.run_id == f"kanban:{launched.native_id}:{native_run_id}"
    assert running.trace["task_id"] == launched.native_id
    assert running.trace["native_run_id"] == native_run_id

    paused = controller.pause(running.run_id, reason="review required")
    assert paused.state is RunState.PAUSED
    with kb.connect() as conn:
        task = kb.get_task(conn, launched.native_id)
        run = kb.get_run(conn, native_run_id)
    assert task.status == "blocked"
    assert run.outcome == "blocked"

    resumed = controller.resume(paused.run_id)
    assert resumed.state is RunState.READY
    with kb.connect() as conn:
        assert kb.get_task(conn, launched.native_id).status == "ready"


def test_kanban_run_controller_status_honors_stale_native_run_id(isolated_control_planes):
    from agent.run_controller import KanbanTaskRunController, RunRequest, RunState

    controller = KanbanTaskRunController(assignee="worker-a")
    launched = controller.launch(RunRequest(trigger="gateway", objective="ship a patch"))
    with kb.connect() as conn:
        kb.claim_task(conn, launched.native_id, claimer="test-worker")
        first_run_id = kb.get_task(conn, launched.native_id).current_run_id
        assert first_run_id is not None
        kb.block_task(conn, launched.native_id, reason="first pause", expected_run_id=first_run_id)
        kb.unblock_task(conn, launched.native_id)
        kb.claim_task(conn, launched.native_id, claimer="test-worker-2")
        second_run_id = kb.get_task(conn, launched.native_id).current_run_id

    stale = controller.status(f"kanban:{launched.native_id}:{first_run_id}")
    current = controller.status(f"kanban:{launched.native_id}:{second_run_id}")

    assert stale.state is RunState.PAUSED
    assert stale.trace["native_run_id"] == first_run_id
    assert stale.trace["requested_run_id"] == first_run_id
    assert current.state is RunState.RUNNING
    assert current.trace["native_run_id"] == second_run_id


def test_kanban_run_controller_cancel_fails_closed_on_stale_run_id(isolated_control_planes):
    from agent.run_controller import KanbanTaskRunController, RunRequest, RunState

    controller = KanbanTaskRunController(assignee="worker-a")
    launched = controller.launch(RunRequest(trigger="gateway", objective="ship a patch"))
    with kb.connect() as conn:
        kb.claim_task(conn, launched.native_id, claimer="test-worker")
        active_run_id = kb.get_task(conn, launched.native_id).current_run_id
        assert active_run_id is not None

    stale_cancel = controller.cancel(f"kanban:{launched.native_id}:{active_run_id + 99}", reason="stale")

    assert stale_cancel.state is RunState.RUNNING
    assert stale_cancel.trace["cancel_failed"] is True
    with kb.connect() as conn:
        task = kb.get_task(conn, launched.native_id)
        run = kb.get_run(conn, active_run_id)
    assert task.status == "running"
    assert run.status == "running"


def test_run_controller_handoff_adapters_expose_trace_and_audit(isolated_control_planes):
    from agent.run_controller import CronJobRunController, KanbanTaskRunController, RunRequest

    cron = CronJobRunController()
    cron_handle = cron.launch(
        RunRequest(trigger="cron.test", objective="summary", payload={"schedule": "every 1h"})
    )
    cron_handoff = cron.handoff(cron_handle.run_id, "kanban", note="create review task")
    assert cron_handoff.trace["handoff_target"] == "kanban"
    assert cron_handoff.trace["handoff_note"] == "create review task"

    kanban = KanbanTaskRunController(assignee="worker-a")
    kanban_handle = kanban.launch(RunRequest(trigger="gateway", objective="ship a patch"))
    kanban_handoff = kanban.handoff(kanban_handle.run_id, "cron", note="schedule summary")
    assert kanban_handoff.trace["handoff_target"] == "cron"
    with kb.connect() as conn:
        comments = kb.list_comments(conn, kanban_handle.native_id)
    assert comments[-1].author == "run_controller"
    assert "schedule summary" in comments[-1].body


def test_run_trace_ledger_links_gateway_entry_to_kanban_handoff_and_cron_followup(isolated_control_planes):
    from agent.run_controller import (
        CronJobRunController,
        InMemoryRunLedger,
        KanbanTaskRunController,
        RunRequest,
    )

    ledger = InMemoryRunLedger()
    gateway = ledger.record_external_run(
        surface="gateway",
        native_id="session-1",
        state="running",
        trace={"entrypoint": "api_server"},
    )
    kanban = ledger.launch_child(
        parent_run_id=gateway.run_id,
        controller=KanbanTaskRunController(assignee="worker-a"),
        request=RunRequest(trigger="gateway.handoff", objective="write implementation handoff"),
        handoff_ref="task created from gateway session",
    )
    cron = ledger.launch_child(
        parent_run_id=kanban.run_id,
        controller=CronJobRunController(),
        request=RunRequest(
            trigger="kanban.followup",
            objective="summarize handoff tomorrow",
            payload={"schedule": "every 24h", "name": "handoff summary"},
        ),
        handoff_ref="cron summary scheduled after kanban handoff",
    )

    trace = ledger.trace(gateway.run_id)
    assert [node.surface for node in trace] == ["gateway", "kanban", "cron"]
    assert trace[0].handoff_refs == [kanban.run_id]
    assert trace[1].handoff_refs == [cron.run_id]
    assert trace[-1].trace["trigger"] == "kanban.followup"
