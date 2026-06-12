import argparse
import json

import pytest

from hermes_cli.missions import MissionBudget, MissionStore, build_tick_prompt


def test_mission_create_is_default_off_and_budget_required(tmp_path):
    store = MissionStore(tmp_path / "missions.db")

    with pytest.raises(ValueError, match="budget"):
        store.create_mission(direction="长期推进 Hermes 自推进", cadence="every 6h", budget=MissionBudget())

    mission = store.create_mission(
        direction="长期推进 Hermes 自推进",
        cadence="every 6h",
        budget=MissionBudget(max_ticks=3, max_turns_per_tick=5),
        success_criteria=["阶段性证据回报"],
    )

    assert mission.status == "paused"
    assert mission.budget.max_ticks == 3
    assert "external_message_send" in mission.blocked_actions
    events = store.list_events(mission.mission_id)
    assert events[0]["event_type"] == "mission_created"
    assert events[0]["payload"]["default_off"] is True


def test_mission_tick_blocks_on_open_decision_and_decide_resumes(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="持续研究 RWA 商业机会",
        cadence="daily",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    decision = store.request_decision(
        mission.mission_id,
        "先做研究流还是产品原型？",
        ["A 研究流", "B 产品原型"],
        "A",
    )

    blocked = store.tick(mission.mission_id)
    assert blocked["status"] == "blocked_on_human"
    assert blocked["open_decisions"][0]["decision_id"] == decision.decision_id

    answered = store.answer_decision(decision.decision_id, "选 A，先研究流")
    assert answered.status == "answered"
    resumed = store.get_mission(mission.mission_id)
    assert resumed is not None
    assert resumed.status == "active"

    tick = store.tick(mission.mission_id)
    assert tick["status"] == "dry_run"
    assert "持续研究 RWA 商业机会" in tick["prompt"]
    assert "选 A，先研究流" in tick["prompt"]


def test_mission_tick_enforces_max_ticks_budget(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="长期推进 Hermes 自推进",
        cadence="hourly",
        budget=MissionBudget(max_ticks=1),
        enable=True,
    )

    first = store.tick(mission.mission_id, dry_run=False)
    assert first["status"] == "prompt_created"

    second = store.tick(mission.mission_id, dry_run=False)
    assert second["status"] == "budget_exhausted"
    paused = store.get_mission(mission.mission_id)
    assert paused is not None
    assert paused.status == "paused"
    assert any(e["event_type"] == "budget_exhausted" for e in store.list_events(mission.mission_id))


def test_terminal_missions_cannot_be_reanimated_by_resume_or_decision(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="长期推进但可取消",
        cadence="daily",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    store.set_status(mission.mission_id, "cancelled", reason="stop")

    with pytest.raises(ValueError, match="illegal mission transition"):
        store.set_status(mission.mission_id, "active", reason="revive")
    with pytest.raises(ValueError, match="terminal state"):
        store.request_decision(mission.mission_id, "是否继续？", ["A"])


def test_answer_one_of_multiple_decisions_keeps_mission_blocked(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="多阶段长期推进",
        cadence="daily",
        budget=MissionBudget(max_ticks=5),
        enable=True,
    )
    first = store.request_decision(mission.mission_id, "选路线？", ["A", "B"])
    second = store.request_decision(mission.mission_id, "选预算？", ["低", "高"])

    store.answer_decision(first.decision_id, "A")
    still_blocked = store.get_mission(mission.mission_id)
    assert still_blocked is not None
    assert still_blocked.status == "blocked_on_human"
    assert [d.decision_id for d in store.open_decisions(mission.mission_id)] == [second.decision_id]

    store.answer_decision(second.decision_id, "低")
    resumed = store.get_mission(mission.mission_id)
    assert resumed is not None
    assert resumed.status == "active"


def test_dry_run_tick_does_not_consume_executable_tick_budget(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="预览不烧预算",
        cadence="daily",
        budget=MissionBudget(max_ticks=1),
        enable=True,
    )

    assert store.tick(mission.mission_id)["status"] == "dry_run"
    assert store.tick(mission.mission_id)["status"] == "dry_run"
    assert store.tick(mission.mission_id, dry_run=False)["status"] == "prompt_created"
    assert store.tick(mission.mission_id, dry_run=False)["status"] == "budget_exhausted"


def test_tick_prompt_contains_human_gate_and_evidence_rules(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="任意高抽象方向长期自推进",
        cadence="weekly",
        budget=MissionBudget(max_ticks=2, max_turns_per_tick=7),
        constraints=["证据优先"],
        non_goals=["不要静默外部副作用"],
        success_criteria=["阶段性回报"],
        enable=True,
    )
    prompt = build_tick_prompt(mission, [], [])

    assert "Mission ID" in prompt
    assert "within 7 tool-calling turns" in prompt
    assert "Human Intervention Packet" in prompt
    assert "Do not claim completion without real evidence" in prompt
    assert "不要静默外部副作用" in prompt
    assert "periodic maintenance watchdog" in prompt


def test_event_driven_flow_signal_consumed_before_maintenance_tick(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="事件驱动推进 Hermes 架构",
        cadence="every 6h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )

    signals = store.pending_flow_signals(mission.mission_id)
    assert len(signals) == 1
    assert signals[0].signal_type == "mission_enabled"

    event_tick = store.tick(mission.mission_id, dry_run=False)
    assert event_tick["mode"] == "event"
    assert "event-driven (mission_enabled)" in event_tick["prompt"]
    assert store.pending_flow_signals(mission.mission_id) == []

    maintenance_tick = store.tick(mission.mission_id, dry_run=False)
    assert maintenance_tick["mode"] == "maintenance"
    assert "periodic maintenance watchdog" in maintenance_tick["prompt"]
    assert store.count_events(mission.mission_id, {"maintenance_tick"}) == 1


def test_event_tick_coalesces_pending_flow_signal_backlog(tmp_path):
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="事件驱动推进 Hermes 架构",
        cadence="every 6h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    store.append_event(mission.mission_id, "taskpool_status_changed", "task one", {"source": "taskpool"})
    store.append_event(mission.mission_id, "taskpool_status_changed", "task two", {"source": "taskpool"})

    signals = store.pending_flow_signals(mission.mission_id)
    assert [signal.signal_type for signal in signals] == [
        "mission_enabled",
        "taskpool_status_changed",
        "taskpool_status_changed",
    ]

    event_tick = store.tick(mission.mission_id, dry_run=False)

    assert event_tick["mode"] == "event"
    assert "coalesced 3 pending flow signals" in event_tick["prompt"]
    assert store.pending_flow_signals(mission.mission_id) == []


def test_cli_handoff_decide_resumes_and_enqueues_flow_signal(tmp_path, capsys):
    from hermes_cli.missions import cmd_mission

    db = str(tmp_path / "missions.db")
    store = MissionStore(tmp_path / "missions.db")
    mission = store.create_mission(
        direction="验证控制台短时能力会话处理真实 mission decision",
        cadence="every 1h",
        budget=MissionBudget(max_ticks=3),
        enable=True,
    )
    store.tick(mission.mission_id, dry_run=False)

    handoff_args = argparse.Namespace(
        db=db,
        mission_command="handoff",
        mission_id=mission.mission_id,
        question="是否继续端到端短时能力会话？",
        option=["继续", "暂停"],
        recommend="继续",
        json=True,
    )
    assert cmd_mission(handoff_args) == 0
    decision_payload = json.loads(capsys.readouterr().out)
    assert decision_payload["status"] == "open"
    assert store.tick(mission.mission_id, dry_run=False)["status"] == "blocked_on_human"

    decide_args = argparse.Namespace(
        db=db,
        mission_command="decide",
        decision_id=decision_payload["decision_id"],
        answer=["继续推进短时能力会话端到端测试"],
        json=True,
    )
    assert cmd_mission(decide_args) == 0
    answered_payload = json.loads(capsys.readouterr().out)
    assert answered_payload["status"] == "answered"
    resumed = store.get_mission(mission.mission_id)
    assert resumed is not None
    assert resumed.status == "active"

    signals = store.pending_flow_signals(mission.mission_id)
    assert [signal.signal_type for signal in signals] == ["decision_recorded"]
    resumed_tick = store.tick(mission.mission_id, dry_run=False)
    assert resumed_tick["mode"] == "event"
    assert "event-driven (decision_recorded)" in resumed_tick["prompt"]
    assert "继续推进短时能力会话端到端测试" in resumed_tick["prompt"]
    assert store.pending_flow_signals(mission.mission_id) == []


def test_cli_json_shape_roundtrip(tmp_path, capsys):
    from hermes_cli.missions import cmd_mission

    db = str(tmp_path / "missions.db")
    args = argparse.Namespace(
        db=db,
        mission_command="create",
        direction="长期推进 Agent 控制面",
        cadence="every 12h",
        deliver="local",
        max_ticks=2,
        max_turns_per_tick=None,
        max_total_usd=None,
        constraint=None,
        non_goal=None,
        success=None,
        allow_action=None,
        block_action=None,
        enable=False,
        json=True,
    )
    assert cmd_mission(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "paused"
    assert payload["direction"] == "长期推进 Agent 控制面"
