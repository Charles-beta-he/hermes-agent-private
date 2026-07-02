import json
import sys
from pathlib import Path

from agent.conductor_runtime import (
    AcceptanceGovernance,
    ConversationIntentRouter,
    FuguAdapter,
    IcebergComplexityScanner,
    MainloopCertificationQueue,
    MainloopVerificationGate,
    ModelCapabilityRouter,
    TaskLedger,
    conductor_defaults_enabled,
    default_conductor_config,
    normalize_fugu_response,
)


def test_complex_task_harness_defaults_are_opt_in_and_side_effect_closed():
    cfg = default_conductor_config()
    harness = cfg["conductor"]["complex_task_harness"]

    assert conductor_defaults_enabled(cfg) is False
    assert harness["enabled"] is False
    assert harness["ledger_enabled"] is False
    assert harness["runtime_mutation_authorized"] is False
    assert harness["output_schema"] == "hermes_iceberg_complexity_scan_v1"


def test_complex_task_harness_explicit_opt_in_counts_as_conductor_enabled():
    cfg = default_conductor_config()
    cfg["conductor"]["complex_task_harness"]["enabled"] = True

    assert conductor_defaults_enabled(cfg) is True


def test_complex_task_harness_ledger_opt_in_counts_as_conductor_enabled():
    cfg = default_conductor_config()
    cfg["conductor"]["complex_task_harness"]["ledger_enabled"] = True

    assert conductor_defaults_enabled(cfg) is True


def test_iceberg_complexity_scanner_classifies_lazycodex_core_replacement_as_complex():
    scanner = IcebergComplexityScanner()

    result = scanner.scan(
        "继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
        target_surface="hermes_core_runtime",
    )

    assert result["schema"] == "hermes_iceberg_complexity_scan_v1"
    assert result["classification"] == "complex"
    assert result["objective_gate_required"] is True
    assert result["ssot_required"] is True
    assert result["safe_next_boundary"] == "ssot_then_default_off_runtime_scaffold"
    assert "external_reference_absorption" in result["above_water_triggers"]
    assert "core_runtime_replacement" in result["above_water_triggers"]
    assert "authority_side_effect_ambiguity" in result["submerged_triggers"]
    assert result["side_effect_ceiling"] == "local_write_only_until_exact_runtime_gate"


def test_iceberg_complexity_scanner_keeps_simple_low_risk_requests_lightweight():
    scanner = IcebergComplexityScanner()

    result = scanner.scan("解释一下 LazyCodex 是什么", target_surface="answer")

    assert result["classification"] == "simple"
    assert result["objective_gate_required"] is False
    assert result["ssot_required"] is False
    assert result["safe_next_boundary"] == "answer_or_minimal_action"


def test_complex_task_harness_ledger_records_scan_and_evidence_without_authority(tmp_path):
    from agent.conductor_runtime import ComplexTaskHarnessLedger

    ledger = ComplexTaskHarnessLedger(tmp_path / "complex-task-harness.jsonl")
    scan = IcebergComplexityScanner().scan(
        "继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
        target_surface="hermes_core_runtime",
    )

    receipt = ledger.record_scan(scan=scan, user_message="继续推进", origin="unit-test")
    evidence = ledger.append_evidence(
        receipt["scan_id"],
        {"source": "pytest", "status": "pass", "summary": "41 passed"},
    )
    events = ledger.read_events(scan_id=receipt["scan_id"])

    assert receipt["schema"] == "hermes_complex_task_harness_receipt_v1"
    assert receipt["status"] == "recorded"
    assert receipt["external_side_effects_enabled"] is False
    assert receipt["allowed_to_mark_done"] is False
    assert evidence["event_type"] == "evidence"
    assert [event["event_type"] for event in events] == ["scan", "evidence"]


def test_complex_task_harness_ledger_rejects_authority_smuggling(tmp_path):
    from agent.conductor_runtime import ComplexTaskHarnessLedger

    ledger = ComplexTaskHarnessLedger(tmp_path / "complex-task-harness.jsonl")
    rejected = ledger.record_scan(
        scan={"classification": "complex", "mark_done": True},
        user_message="继续推进",
        origin="unit-test",
    )

    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "authority_smuggling_detected"


def test_complex_task_harness_ledger_records_checkpoint_receipt(tmp_path):
    from agent.conductor_runtime import ComplexTaskHarnessLedger

    ledger = ComplexTaskHarnessLedger(tmp_path / "complex-task-harness.jsonl")
    scan = IcebergComplexityScanner().scan(
        "继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
        target_surface="hermes_core_runtime",
    )
    receipt = ledger.record_scan(scan=scan, user_message="继续推进", origin="unit-test")

    checkpoint = ledger.append_checkpoint(
        receipt["scan_id"],
        objective="continue runtime replacement",
        completed_evidence=["pytest:28 passed"],
        pending_actions=["add replay CLI"],
        rollback="git reset --hard HEAD~1 in isolated worktree",
    )
    events = ledger.read_events(scan_id=receipt["scan_id"])

    assert checkpoint["event_type"] == "checkpoint"
    assert checkpoint["allowed_to_mark_done"] is False
    assert checkpoint["checkpoint"]["pending_actions"] == ["add replay CLI"]
    assert [event["event_type"] for event in events] == ["scan", "checkpoint"]


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


def test_model_capability_router_selects_specialist_and_excludes_implementer():
    profiles = {
        "role_separation": {
            "enabled": True,
            "forbid_self_certification": True,
            "same_provider_verification": "discouraged",
            "implementer_vote_policy": "self_report_only",
        },
        "overlap_voting": {
            "enabled": True,
            "trigger_when_surface_count_gte": 2,
            "trigger_when_risk_gte": "T2",
            "require_independent_verifier": True,
            "require_surface_owner_vote": True,
            "arbiter_model": "claude-opus-4-8",
        },
        "surfaces": {
            "ui_visual": {
                "implementers": ["gpt-5.5"],
                "verifiers": ["gemini-3.1-pro-preview", "gpt-5.5"],
                "fallback_verifiers": ["claude-opus-4-8"],
                "final_evidence": ["screenshot_matrix", "browser_snapshot"],
            },
            "code_correctness": {
                "implementers": ["gpt-5.5"],
                "verifiers": ["deepseek-v4-pro", "gpt-5.5"],
                "arbiters": ["claude-opus-4-8"],
                "final_evidence": ["test_output", "build_output"],
            },
        },
    }
    router = ModelCapabilityRouter(profiles=profiles)

    decision = router.route(
        surfaces=["ui_visual", "code_correctness"],
        risk_tier="T2",
        implementer_model="gpt-5.5",
        available_models=["gpt-5.5", "gemini-3.1-pro-preview", "deepseek-v4-pro", "claude-opus-4-8"],
    )

    assert decision["overlap_voting_required"] is True
    assert decision["implementer_model"] == "gpt-5.5"
    assert decision["surface_routes"]["ui_visual"]["primary"] == "gpt-5.5"
    assert decision["surface_routes"]["ui_visual"]["verifier"] == "gemini-3.1-pro-preview"
    assert decision["surface_routes"]["code_correctness"]["verifier"] == "deepseek-v4-pro"
    assert decision["surface_routes"]["ui_visual"]["implementer_vote_counts_for_certification"] is False
    assert "gpt-5.5" not in decision["certification_participants"]
    assert decision["required_evidence"] == ["screenshot_matrix", "browser_snapshot", "test_output", "build_output"]


def test_model_capability_router_can_make_gemini_primary_for_visual_work_and_claude_primary_for_review():
    profiles = {
        "role_separation": {"forbid_self_certification": True},
        "overlap_voting": {"enabled": True, "trigger_when_surface_count_gte": 2, "trigger_when_risk_gte": "T2", "arbiter_model": "claude-opus-4-8"},
        "surfaces": {
            "ui_visual": {
                "implementers": ["gemini-3.1-pro-preview", "gpt-5.5"],
                "verifiers": ["claude-opus-4-8"],
                "final_evidence": ["screenshot_matrix"],
            },
            "code_review": {
                "implementers": ["claude-opus-4-8"],
                "verifiers": ["deepseek-v4-pro"],
                "final_evidence": ["review_report", "diff_refs"],
            },
        },
    }
    router = ModelCapabilityRouter(profiles=profiles)

    visual = router.route(
        surfaces=["ui_visual"],
        risk_tier="T1",
        available_models=["gpt-5.5", "gemini-3.1-pro-preview", "claude-opus-4-8"],
    )
    assert visual["surface_routes"]["ui_visual"]["primary"] == "gemini-3.1-pro-preview"
    assert visual["surface_routes"]["ui_visual"]["verifier"] == "claude-opus-4-8"
    assert visual["primary_models"] == ["gemini-3.1-pro-preview"]

    review = router.route(
        surfaces=["code_review"],
        risk_tier="T1",
        available_models=["claude-opus-4-8", "deepseek-v4-pro"],
    )
    assert review["surface_routes"]["code_review"]["primary"] == "claude-opus-4-8"
    assert review["surface_routes"]["code_review"]["verifier"] == "deepseek-v4-pro"
    assert review["primary_models"] == ["claude-opus-4-8"]


def test_acceptance_governance_rejects_model_only_and_self_certification():
    governance = AcceptanceGovernance(
        require_objective_evidence=True,
        require_evidence_refs_for_votes=True,
        forbid_model_only_certification=True,
        forbid_self_certification=True,
        require_independent_verifier_vote=True,
    )

    model_only = governance.evaluate(
        claims=[{"id": "C-ui", "surface": "ui_visual", "text": "UI accepted"}],
        evidence=[],
        votes=[{"model": "gemini-3.1-pro-preview", "role": "ui_visual_verifier", "vote": "approve", "evidence_refs": []}],
        implementer_model="gpt-5.5",
    )
    assert model_only["status"] == "advisory_only"
    assert "objective_evidence_missing" in model_only["failures"]
    assert "vote_evidence_refs_missing:gemini-3.1-pro-preview" in model_only["failures"]

    self_cert = governance.evaluate(
        claims=[{"id": "C-code", "surface": "code_correctness", "text": "Code works"}],
        evidence=[{"id": "E-test", "supports": ["C-code"], "source": "pytest", "status": "pass"}],
        votes=[{"model": "gpt-5.5", "role": "implementer", "vote": "approve", "evidence_refs": ["E-test"]}],
        implementer_model="gpt-5.5",
    )
    assert self_cert["status"] == "not_certified"
    assert "self_certification_forbidden:gpt-5.5" in self_cert["failures"]

    no_verifier = governance.evaluate(
        claims=[{"id": "C-code", "surface": "code_correctness", "text": "Code works"}],
        evidence=[{"id": "E-test", "supports": ["C-code"], "source": "pytest", "status": "pass"}],
        votes=[],
        implementer_model="gpt-5.5",
    )
    assert no_verifier["status"] == "not_certified"
    assert "independent_verifier_vote_missing" in no_verifier["failures"]

    certified = governance.evaluate(
        claims=[{"id": "C-code", "surface": "code_correctness", "text": "Code works"}],
        evidence=[{"id": "E-test", "supports": ["C-code"], "source": "pytest", "status": "pass"}],
        votes=[{"model": "deepseek-v4-pro", "role": "code_reviewer", "vote": "approve", "evidence_refs": ["E-test"], "best_within_constraints": True}],
        implementer_model="gpt-5.5",
    )
    assert certified["status"] == "certified"
    assert certified["failures"] == []


def test_mainloop_verification_gate_promotes_complex_scan_completion_to_t3():
    profiles = {
        "mainloop_verification": {"enabled": True, "advisory_only": False, "blocking_tiers": ["T3"]},
        "acceptance_governance": {"require_objective_evidence": True, "require_independent_verifier_vote": True},
        "surfaces": {
            "completion_claim": {"final_evidence": ["fresh_tool_output"]},
            "complex_task_harness": {"verifiers": ["deepseek-v4-pro"], "final_evidence": ["focused_pytest", "git_status"]},
        },
    }
    gate = MainloopVerificationGate(profiles=profiles)
    scan = IcebergComplexityScanner().scan(
        "继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
        target_surface="hermes_core_runtime",
    )

    result = gate.evaluate_turn(
        user_message="继续推进",
        final_response="已完成。",
        messages=[],
        implementer_model="gpt-5.5",
        available_models=["gpt-5.5", "deepseek-v4-pro"],
        complexity_scan=scan,
    )

    assert result["risk_tier"] == "T3"
    assert "complex_task_harness" in result["surfaces"]
    assert result["complexity_scan"]["classification"] == "complex"
    assert result["verifier_packet"]["schema"] == "hermes_complex_task_verifier_packet_v1"
    assert result["verifier_packet"]["allowed_to_mark_done"] is False
    assert result["verifier_packet"]["required_evidence"] == ["fresh_tool_output", "focused_pytest", "git_status"]
    assert result["status"] == "not_certified"
    assert result["blocking"] is True
    assert "objective_evidence_missing" in result["acceptance"]["failures"]


def test_mainloop_verification_gate_degrades_completion_claim_without_objective_evidence():
    profiles = {
        "mainloop_verification": {"enabled": True, "advisory_only": False, "blocking_tiers": ["T3"]},
        "acceptance_governance": {
            "require_objective_evidence": True,
            "require_evidence_refs_for_votes": True,
            "forbid_model_only_certification": True,
            "forbid_self_certification": True,
            "require_independent_verifier_vote": True,
        },
        "role_separation": {"forbid_self_certification": True},
        "overlap_voting": {"enabled": True, "trigger_when_surface_count_gte": 2, "trigger_when_risk_gte": "T2", "arbiter_model": "claude-opus-4-8"},
        "surfaces": {
            "completion_claim": {"verifiers": ["hermes_evidence_gate"], "final_evidence": ["fresh_tool_output"]},
            "hermes_self_modification": {"verifiers": ["deepseek-v4-pro"], "arbiters": ["claude-opus-4-8"], "final_evidence": ["focused_pytest", "config_readback"]},
        },
    }
    gate = MainloopVerificationGate(profiles=profiles)

    result = gate.evaluate_turn(
        user_message="继续推进直至门禁认可完成",
        final_response="已完成并验收通过。",
        messages=[{"role": "user", "content": "继续推进"}],
        implementer_model="gpt-5.5",
        available_models=["gpt-5.5", "deepseek-v4-pro", "claude-opus-4-8"],
    )

    assert result["status"] == "not_certified"
    assert result["blocking"] is True
    assert "objective_evidence_missing" in result["acceptance"]["failures"]
    assert "independent_verifier_vote_missing" in result["acceptance"]["failures"]
    assert "不能声明已完成/已验收" in result["response_notice"]


def test_mainloop_verification_gate_reports_missing_route_required_evidence_classes():
    profiles = {
        "mainloop_verification": {"enabled": True, "advisory_only": False, "blocking_tiers": ["T3"]},
        "acceptance_governance": {
            "require_objective_evidence": True,
            "require_evidence_refs_for_votes": True,
            "require_independent_verifier_vote": True,
        },
        "surfaces": {
            "hermes_self_modification": {"verifiers": ["deepseek-v4-pro"], "final_evidence": ["focused_pytest", "config_readback", "git_status"]},
        },
    }
    gate = MainloopVerificationGate(profiles=profiles)
    result = gate.evaluate_turn(
        user_message="继续推进 Hermes 主循环并验收完成",
        final_response="已完成并验收通过。",
        messages=[
            {"role": "tool", "content": "18 passed", "name": "pytest"},
            {"role": "assistant", "content": "Verifier deepseek approve", "model": "deepseek-v4-pro", "evidence_refs": ["E-tool-1"], "vote": "approve"},
        ],
        implementer_model="gpt-5.5",
        available_models=["gpt-5.5", "deepseek-v4-pro", "claude-opus-4-8"],
    )
    assert result["status"] == "not_certified"
    assert result["blocking"] is True
    assert result["route_required_evidence"]["missing"]
    assert "config_readback" in result["route_required_evidence"]["missing"]
    assert any(f.startswith("route_required_evidence_missing:") for f in result["acceptance"]["failures"])


def test_mainloop_verification_gate_allows_low_risk_non_completion_chat():
    gate = MainloopVerificationGate(profiles={"mainloop_verification": {"enabled": True, "advisory_only": False}})
    result = gate.evaluate_turn(
        user_message="解释一下这个概念",
        final_response="这是一个普通解释。",
        messages=[],
        implementer_model="gpt-5.5",
    )
    assert result["status"] == "skipped_low_risk"
    assert result["blocking"] is False


def test_mainloop_verification_gate_skips_internal_contextcard_projection():
    gate = MainloopVerificationGate(profiles={"mainloop_verification": {"enabled": True, "advisory_only": False, "blocking_tiers": ["T3"]}})
    result = gate.evaluate_turn(
        user_message="You generate ONE richer Hermes ContextCard JSON object from source-bound evidence.\nSOURCE_CARD:\n{}\nEVIDENCE_SPANS:\n[]",
        final_response='{"id":"x","title":"UI 修复完成","cardVersion":1,"topics":["Hermes Context"]}',
        messages=[],
        implementer_model="gpt-5.5",
    )
    assert result["status"] == "skipped_internal_projection"
    assert result["blocking"] is False


def test_mainloop_verification_gate_skips_internal_multibrain_json_projection():
    gate = MainloopVerificationGate(profiles={"mainloop_verification": {"enabled": True, "advisory_only": False, "blocking_tiers": ["T3"]}})
    result = gate.evaluate_turn(
        user_message="[Auto-relay 1/12] Previous turn stopped because max_iterations_reached(1/1).\nContinue the task from that handoff.",
        final_response='{"meta":{"planner_model":"gpt-5.5"},"diagnosis":{"summary":"已完成"},"patches":[]}',
        messages=[],
        implementer_model="gpt-5.5",
    )
    assert result["status"] == "skipped_internal_projection"
    assert result["blocking"] is False


def test_mainloop_certification_queue_is_idempotent_and_blocks_authority_smuggling(tmp_path):
    queue = MainloopCertificationQueue(root=tmp_path)
    decision = {
        "schema": "hermes_mainloop_verification_gate_v1",
        "status": "not_certified",
        "blocking": True,
        "risk_tier": "T3",
        "claims": [{"id": "C-1", "surface": "completion_claim", "text": "已完成"}],
        "evidence": [{"id": "E-1", "source": "pytest", "status": "pass", "supports": ["C-1"]}],
    }

    first = queue.enqueue(
        decision=decision,
        user_message="继续推进直至无遗漏",
        final_response="已完成",
        implementer_model="gpt-5.5",
    )
    second = queue.enqueue(
        decision=decision,
        user_message="继续推进直至无遗漏",
        final_response="已完成",
        implementer_model="gpt-5.5",
    )

    assert first["job_id"] == second["job_id"]
    assert first["status"] == "queued"
    assert second["status"] == "queued"
    job_path = tmp_path / f"{first['job_id']}.json"
    assert job_path.exists()
    stored = json.loads(job_path.read_text())
    assert stored["external_side_effects_enabled"] is False
    assert stored["allowed_to_mark_done"] is False
    assert stored["request"]["implementer_model"] == "gpt-5.5"

    smuggled = dict(decision)
    smuggled["mark_done"] = True
    rejected = queue.enqueue(
        decision=smuggled,
        user_message="继续推进直至无遗漏",
        final_response="已完成",
        implementer_model="gpt-5.5",
    )
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "authority_smuggling_detected"


def test_mainloop_certification_queue_run_job_updates_status_with_fake_runner(tmp_path):
    queue = MainloopCertificationQueue(root=tmp_path)
    job = queue.enqueue(
        decision={"blocking": True, "claims": [{"id": "C-1", "text": "完成"}], "evidence": []},
        user_message="继续推进",
        final_response="已完成",
        implementer_model="gpt-5.5",
    )

    result = queue.run_job(job["job_id"], runner=lambda payload: {"status": "certified", "artifact": "/tmp/fake"})

    assert result["status"] == "completed"
    assert result["certification_result"]["status"] == "certified"
    stored = json.loads((tmp_path / f"{job['job_id']}.json").read_text())
    assert stored["status"] == "completed"
    assert stored["certification_result"]["artifact"] == "/tmp/fake"


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


def test_conversation_loop_conductor_router_records_advisory_route_when_enabled():
    from agent.conversation_loop import _run_conductor_intent_router

    class FakeAgent:
        _conductor_config = {"conductor": {"intent_router": {"enabled": True}}}
        model = "test-model"

    agent = FakeAgent()
    result = _run_conductor_intent_router(agent, user_message="多模型验收一下")

    assert result is not None
    assert result["intent"] == "multimodel_escalation"
    assert result["router_call"] is True
    assert result["participant_models"] == []
    assert getattr(agent, "_last_conductor_intent_route") == result


def test_conversation_loop_attaches_iceberg_scan_only_when_complex_harness_enabled():
    from agent.conversation_loop import _run_conductor_intent_router

    class DisabledHarnessAgent:
        _conductor_config = {
            "conductor": {
                "intent_router": {"enabled": True},
                "complex_task_harness": {"enabled": False},
            }
        }
        model = "test-model"

    disabled = _run_conductor_intent_router(
        DisabledHarnessAgent(),
        user_message="继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
    )
    assert disabled is not None
    assert "iceberg_complexity_scan" not in disabled

    class EnabledHarnessAgent:
        _conductor_config = {
            "conductor": {
                "intent_router": {"enabled": True},
                "complex_task_harness": {"enabled": True},
            }
        }
        model = "test-model"

    enabled = _run_conductor_intent_router(
        EnabledHarnessAgent(),
        user_message="继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
    )
    assert enabled is not None
    assert enabled["iceberg_complexity_scan"]["classification"] == "complex"
    assert enabled["iceberg_complexity_scan"]["runtime_mutation_authorized"] is False


def test_conversation_loop_records_complex_harness_receipt_when_ledger_enabled(tmp_path):
    from agent.conversation_loop import _run_conductor_intent_router

    ledger_path = tmp_path / "harness.jsonl"

    class EnabledLedgerAgent:
        _conductor_config = {
            "conductor": {
                "intent_router": {"enabled": True},
                "complex_task_harness": {
                    "enabled": True,
                    "ledger_enabled": True,
                    "ledger_path": str(ledger_path),
                },
            }
        }
        model = "test-model"

    result = _run_conductor_intent_router(
        EnabledLedgerAgent(),
        user_message="继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
    )

    assert result is not None
    receipt = result["complex_task_harness_receipt"]
    assert receipt["status"] == "recorded"
    assert receipt["allowed_to_mark_done"] is False
    assert ledger_path.exists()


def test_conversation_loop_mainloop_gate_appends_blocking_notice_for_uncertified_claim(tmp_path, monkeypatch):
    from agent.conversation_loop import _run_mainloop_verification_gate

    monkeypatch.setenv("HERMES_MAINLOOP_CERT_QUEUE_DIR", str(tmp_path))

    class FakeAgent:
        model = "gpt-5.5"
        _mainloop_verifier_votes = []

    messages = [{"role": "assistant", "content": "Hermes 主循环门禁已完成并验收通过。"}]
    result = _run_mainloop_verification_gate(
        FakeAgent(),
        user_message="继续推进直至门禁认可完成",
        final_response="Hermes 主循环门禁已完成并验收通过。",
        messages=messages,
    )

    assert result is not None
    assert result["blocking"] is True
    assert result["status"] == "not_certified"
    assert "Hermes verification gate" in result["final_response_override"]
    assert messages[-1]["content"] == result["final_response_override"]
    assert result["certification_job"]["status"] == "queued"
    assert (tmp_path / f"{result['certification_job']['job_id']}.json").exists()
