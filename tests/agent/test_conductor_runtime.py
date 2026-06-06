import json
import sys
from pathlib import Path

from agent.conductor_runtime import (
    ConversationIntentRouter,
    FuguAdapter,
    TaskLedger,
    conductor_defaults_enabled,
    default_conductor_config,
    normalize_fugu_response,
)


def test_task_ledger_creates_updates_and_queries_active_tasks(tmp_path):
    ledger = TaskLedger(tmp_path / "ledger.db")

    task = ledger.create_task(
        title="研究 Sakana Fugu",
        current_goal="查官网证据并输出 Hermes 接入方案",
        origin_session_id="session-1",
        constraints=["evidence-first"],
    )
    ledger.update_task_directive(task["task_id"], "不要写代码，只出方案", action="append_context")
    ledger.pause_task(task["task_id"], reason="user requested pause")
    paused = ledger.get_task(task["task_id"])

    assert paused["status"] == "paused"
    assert paused["latest_user_directive"] == "不要写代码，只出方案"
    assert paused["constraints"] == ["evidence-first"]
    assert paused["history"][-1]["action"] == "pause"

    ledger.resume_task(task["task_id"], reason="continue")
    active = ledger.get_active_tasks()
    assert [item["task_id"] for item in active] == [task["task_id"]]


def test_intent_router_rules_route_new_task_status_and_current_task_without_model_call(tmp_path):
    ledger = TaskLedger(tmp_path / "ledger.db")
    task = ledger.create_task(title="Sakana 方案", current_goal="落地 conductor", origin_session_id="s1")
    router = ConversationIntentRouter(default_model_name="main-model")

    status = router.classify("它现在做到哪了？", active_tasks=[task])
    assert status["intent"] == "status_query"
    assert status["target_task_id"] == task["task_id"]
    assert status["router_call"] is True
    assert status["router_model"] == "main-model"
    assert status["participant_models"] == []

    new_task = router.classify("另外帮我查一下 Fugu API", active_tasks=[task])
    assert new_task["intent"] == "new_background_task"
    assert new_task["target_task_id"] is None

    current = router.classify("刚才那个不要写代码，只出方案", active_tasks=[task])
    assert current["intent"] == "current_task_control"
    assert current["action"] == "append_context"
    assert current["target_task_id"] == task["task_id"]


def test_intent_router_default_model_fallback_keeps_router_isolated_from_participants():
    def fake_default_model(payload):
        assert payload["output_contract"] == "hermes_intent_decision_v1"
        return {
            "intent": "multimodel_escalation",
            "action": "escalate",
            "target_task_id": "bg_1",
            "confidence": 0.76,
            "reason": "user asked for real multi-model validation",
            "participant_models": ["SHOULD_BE_DROPPED"],
        }

    router = ConversationIntentRouter(default_model_name="gpt-5.5", model_decider=fake_default_model)
    decision = router.classify("这个用多模型真实验证", active_tasks=[{"task_id": "bg_1", "status": "running"}])

    assert decision["intent"] == "multimodel_escalation"
    assert decision["router_model"] == "gpt-5.5"
    assert decision["router_call"] is True
    assert decision["participant_models"] == []
    assert decision["trace"]["router_model"] == "gpt-5.5"
    assert decision["trace"]["participant_models"] == []


def test_conductor_defaults_are_disabled_and_fugu_requires_explicit_config():
    cfg = default_conductor_config()
    assert cfg["conductor"]["enabled"] is False
    assert cfg["orchestration_providers"]["sakana_fugu"]["enabled"] is False
    assert conductor_defaults_enabled(cfg) is False

    adapter = FuguAdapter(cfg["orchestration_providers"]["sakana_fugu"])
    result = adapter.plan({"question": "test"})
    assert result["provider"] == "sakana_fugu"
    assert result["status"] == "unavailable"
    assert result["fail_soft"] is True


def test_fugu_response_normalizes_to_hermes_schema_without_truth_certification():
    raw = {
        "workflow": [{"role": "Thinker", "task": "plan"}],
        "risks": ["missing evidence"],
        "confidence": 0.82,
        "extra": "allowed but ignored",
    }
    normalized = normalize_fugu_response(raw)

    assert normalized["schema"] == "hermes_conductor_plan_v1"
    assert normalized["provider"] == "sakana_fugu"
    assert normalized["workflow"] == raw["workflow"]
    assert normalized["risks"] == ["missing evidence"]
    assert normalized["truth_certified"] is False
    assert normalized["evidence_requests"] == []


def test_multi_model_conductor_builds_role_plan_with_router_isolation():
    from agent.conductor_runtime import MultiModelConductor

    profiles = {
        "version": 1,
        "conductor": {"enabled": True},
        "profiles": {
            "gpt-5.5": {"roles": ["planner", "implementer"], "cost_tier": "high"},
            "claude-opus-4-7": {"roles": ["architect", "arbiter"], "cost_tier": "high"},
            "deepseek-v4-pro": {"roles": ["critic", "verifier"], "cost_tier": "medium"},
            "mimo-v2.5-pro": {"roles": ["scout"], "cost_tier": "low"},
            "router-model": {"roles": ["planner", "implementer", "scout"], "cost_tier": "low"},
        },
    }
    conductor = MultiModelConductor(profiles=profiles)
    result = conductor.build_role_plan(
        task_context={"goal": "research X", "risk_level": "high", "multimodel_mode": "advisory"},
        router_model="router-model",
        exclude_router_from_participants=True,
    )

    assert result["schema"] == "hermes_conductor_role_plan_v1"
    assert result["conductor_enabled"] is True
    # router-model should be excluded from participants
    assert "router-model" not in result["participant_models"]
    # each role should have a model assigned
    roles = {r["role"] for r in result["role_plan"]}
    assert "scout" in roles
    assert "planner" in roles
    assert "critic" in roles
    # conductor_trace separates router from participants
    trace = result["conductor_trace"]
    assert trace["router_model"] == "router-model"
    assert trace["router_call"] is True
    assert "router-model" not in trace["participant_models"]


def test_multi_model_conductor_generates_role_specific_instructions():
    from agent.conductor_runtime import MultiModelConductor

    profiles = {
        "version": 1,
        "conductor": {"enabled": True},
        "profiles": {
            "gpt-5.5": {"roles": ["planner"], "cost_tier": "high", "guidance": "Focus on implementation."},
        },
    }
    conductor = MultiModelConductor(profiles=profiles)
    instruction = conductor.build_model_specific_instruction(
        "gpt-5.5", "planner", {"goal": "Refactor auth", "constraints": ["no breaking changes"]}
    )
    assert "planner" in instruction.lower()
    assert "Refactor auth" in instruction
    assert "no breaking changes" in instruction
    assert "Focus on implementation" in instruction


def test_background_worker_probe_and_dry_run(tmp_path):
    from agent.conductor_runtime import TaskLedger

    sys_path_backup = sys.path[:]
    scripts = str(Path.home() / ".hermes" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from hermes_background_task_worker import BackgroundTaskWorker
    sys.path[:] = sys_path_backup

    ledger_path = tmp_path / "ledger.db"
    ledger = TaskLedger(ledger_path)
    task = ledger.create_task(title="Test task", current_goal="Verify worker", origin_session_id="s1")

    worker = BackgroundTaskWorker(task["task_id"], ledger_path)

    # Probe
    probe = worker.probe()
    assert probe["task_id"] == task["task_id"]
    assert probe["status"] == "queued"

    # Dry run
    result = worker.run(dry_run=True)
    assert result["status"] == "done"
    assert result["task_id"] == task["task_id"]
    assert len(result["checkpoints"]) == 1
    assert len(result["directives_received"]) == 1

    # Verify task is now done
    final = ledger.get_task(task["task_id"])
    assert final["status"] == "done"


def test_conversation_loop_conductor_router_integration(tmp_path):
    """Verify _run_conductor_intent_router is wired into conversation_loop module."""
    # Just verify the function exists and is importable
    from agent.conversation_loop import _run_conductor_intent_router
    assert callable(_run_conductor_intent_router)

    # Verify it returns None when conductor is not enabled (default config)
    class FakeAgent:
        _conductor_config = {"conductor": {"intent_router": {"enabled": False}}}
        model = "test-model"

    result = _run_conductor_intent_router(FakeAgent(), user_message="test")
    assert result is None
