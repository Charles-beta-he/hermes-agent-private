"""Tests for hermes_cli/goals.py — persistent cross-turn goals."""

from __future__ import annotations

import json
import time
from unittest.mock import patch, MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so SessionDB.state_meta writes don't clobber the real one."""
    from pathlib import Path

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Bust the goal-module's DB cache for each test so it re-resolves HERMES_HOME.
    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────
# _parse_judge_response
# ──────────────────────────────────────────────────────────────────────


class TestParseJudgeResponse:
    def test_clean_json_done(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, _pf, wait = _parse_judge_response('{"done": true, "reason": "all good"}')
        assert verdict == "done"
        assert reason == "all good"
        assert wait is None

    def test_clean_json_continue(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, _pf, wait = _parse_judge_response('{"done": false, "reason": "more work needed"}')
        assert verdict == "continue"
        assert reason == "more work needed"
        assert wait is None

    def test_json_in_markdown_fence(self):
        from hermes_cli.goals import _parse_judge_response

        raw = '```json\n{"done": true, "reason": "done"}\n```'
        verdict, reason, _pf, _w = _parse_judge_response(raw)
        assert verdict == "done"
        assert "done" in reason

    def test_json_embedded_in_prose(self):
        """Some models prefix reasoning before emitting JSON — we extract it."""
        from hermes_cli.goals import _parse_judge_response

        raw = 'Looking at this... the agent says X. Verdict: {"done": false, "reason": "partial"}'
        verdict, reason, _pf, _w = _parse_judge_response(raw)
        assert verdict == "continue"
        assert reason == "partial"

    def test_string_done_values(self):
        from hermes_cli.goals import _parse_judge_response

        for s in ("true", "yes", "done", "1"):
            verdict, _, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert verdict == "done"
        for s in ("false", "no", "not yet"):
            verdict, _, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert verdict == "continue"

    def test_new_verdict_shape(self):
        """The explicit {"verdict": ...} shape is honored."""
        from hermes_cli.goals import _parse_judge_response

        v, _, _, _ = _parse_judge_response('{"verdict": "done", "reason": "r"}')
        assert v == "done"
        v, _, _, _ = _parse_judge_response('{"verdict": "continue", "reason": "r"}')
        assert v == "continue"

    def test_wait_verdict_with_pid(self):
        from hermes_cli.goals import _parse_judge_response

        v, reason, pf, wait = _parse_judge_response(
            '{"verdict": "wait", "wait_on_pid": 4242, "reason": "CI running"}'
        )
        assert v == "wait"
        assert pf is False
        assert wait == {"pid": 4242}
        assert reason == "CI running"

    def test_wait_verdict_with_seconds(self):
        from hermes_cli.goals import _parse_judge_response

        v, _, _, wait = _parse_judge_response(
            '{"verdict": "wait", "wait_for_seconds": 90, "reason": "rate limited"}'
        )
        assert v == "wait"
        assert wait == {"seconds": 90}

    def test_wait_verdict_without_target_downgrades_to_continue(self):
        """A wait verdict with no pid/seconds can't park on anything → continue."""
        from hermes_cli.goals import _parse_judge_response

        v, _, pf, wait = _parse_judge_response('{"verdict": "wait", "reason": "vague"}')
        assert v == "continue"
        assert wait is None
        assert pf is False

    def test_unknown_verdict_falls_back_to_continue(self):
        from hermes_cli.goals import _parse_judge_response

        v, _, _, _ = _parse_judge_response('{"verdict": "maybe", "reason": "r"}')
        assert v == "continue"

    def test_malformed_json_fails_open(self):
        """Non-JSON → continue + parse_failed, with error-ish reason."""
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response("this is not json at all")
        assert verdict == "continue"
        assert parse_failed is True
        assert reason  # non-empty

    def test_empty_response(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response("")
        assert verdict == "continue"
        assert parse_failed is True
        assert reason


# ──────────────────────────────────────────────────────────────────────
# judge_goal — fail-open semantics
# ──────────────────────────────────────────────────────────────────────


class TestJudgeGoal:
    def test_empty_goal_skipped(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _, _wd = judge_goal("", "some response")
        assert verdict == "skipped"

    def test_empty_response_continues(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _, _wd = judge_goal("ship the thing", "")
        assert verdict == "continue"

    def test_no_aux_client_continues(self):
        """Fail-open: if no aux client, we must return continue, not skipped/done."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(None, None),
        ):
            verdict, _, _, _wd = goals.judge_goal("my goal", "my response")
        assert verdict == "continue"

    def test_api_error_continues(self):
        """Judge exception → fail-open continue (don't wedge progress on judge bugs)."""
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert "judge error" in reason.lower()

    def test_judge_says_done(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "achieved"}')
                )
            ]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd = goals.judge_goal(
                "goal",
                "Done. Tests: command: ./venv/bin/python -m pytest -q -> 1 passed",
            )
        assert verdict == "done"
        assert reason == "achieved"

    def test_judge_says_continue(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "not yet"}')
                )
            ]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd = goals.judge_goal("goal", "agent response")
        assert verdict == "continue"
        assert reason == "not yet"

    def test_done_claim_without_proof_is_downgraded_to_needs_evidence(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "agent claimed completion"}')
                )
            ]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd = goals.judge_goal("ship a tested patch", "Done — the patch is finished.")
        assert verdict == "continue"
        assert "needs_evidence" in reason

    def test_done_claim_with_command_file_or_proof_refs_can_be_verified(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "proof bundle verifies completion"}')
                )
            ]
        )
        last_response = """
        Changed files: /Users/charles/.hermes/hermes-agent/hermes_cli/goals.py
        Tests: command: ./venv/bin/python -m pytest -o addopts= tests/hermes_cli/test_goals.py -q -> 12 passed
        Proof: proof_log:goal-judge-evidence-bundle
        """
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd = goals.judge_goal("ship a tested patch", last_response)
        assert verdict == "done"
        assert reason == "proof bundle verifies completion"

    def test_blocked_or_needs_human_response_is_classified_separately(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "blocked by human decision"}')
                )
            ]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _, _wd = goals.judge_goal(
                "deploy the change",
                "I am blocked and need human input before deploying to production.",
            )
        assert verdict == "blocked"
        assert "needs_human" in reason


# ──────────────────────────────────────────────────────────────────────
# GoalManager lifecycle + persistence
# ──────────────────────────────────────────────────────────────────────


class TestGoalManager:
    def test_no_goal_initial(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-1")
        assert mgr.state is None
        assert not mgr.is_active()
        assert not mgr.has_goal()
        assert "No active goal" in mgr.status_line()

    def test_set_then_status(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-2", default_max_turns=5)
        state = mgr.set("port the thing")
        assert state.goal == "port the thing"
        assert state.status == "active"
        assert state.max_turns == 5
        assert state.turns_used == 0
        assert mgr.is_active()
        assert "active" in mgr.status_line().lower()
        assert "port the thing" in mgr.status_line()

    def test_set_persists_goal_spec_and_route_decision(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="goal-spec-sid", default_max_turns=7)
        state = mgr.set(
            "全盘推进 Hermes /goal dynamic workflow hooks 落地",
            success_criteria=["GoalSpec schema exists", "route dry-run is verified"],
            non_goals=["do not mutate taskpool lifecycle automatically"],
        )

        assert state.spec is not None
        assert state.spec["schema"] == "goal_spec_v1"
        assert state.spec["user_goal"] == state.goal
        assert state.spec["success_criteria"] == [
            "GoalSpec schema exists",
            "route dry-run is verified",
        ]
        assert state.spec["non_goals"] == ["do not mutate taskpool lifecycle automatically"]
        assert state.spec["requires_evidence"] is True
        assert state.route is not None
        assert state.route["schema"] == "goal_route_decision_v1"
        assert state.route["target_layer"] == "taskpool"
        assert state.route["side_effect"] == "dry_run_only"

        reloaded = GoalManager(session_id="goal-spec-sid")
        assert reloaded.state.spec == state.spec
        assert reloaded.state.route == state.route
        status = reloaded.status_line()
        assert "route=taskpool" in status
        assert "criteria=2" in status

    def test_goal_route_decision_classifies_session_taskpool_mission_cron_and_human_gate(self):
        from hermes_cli.goals import goal_route_decision

        assert goal_route_decision("summarize this paragraph").target_layer == "session"
        assert goal_route_decision("修改 Hermes 代码并运行测试").target_layer == "taskpool"
        assert goal_route_decision("长期推进 Hermes 自身架构 mission").target_layer == "mission"
        assert goal_route_decision("每天早上 9 点发日报").target_layer == "cron"
        assert goal_route_decision("部署到生产并付款购买服务").target_layer == "human_gate"

    def test_compile_goal_promotion_dry_run_routes_fixtures_to_expected_candidate_layers(self):
        from hermes_cli.goals import build_goal_spec, compile_goal_promotion_dry_run

        fixtures = [
            (
                "修改 Hermes /goal 代码并运行测试",
                "taskpool",
                "taskpool_task_candidate",
                "deterministic_test_or_dry_run_readback",
            ),
            (
                "长期推进 Hermes 自身架构 mission，并设置阶段报告",
                "mission",
                "mission_candidate",
                "stage_report_readback",
            ),
            (
                "每天早上 9 点生成日报",
                "cron",
                "cron_candidate",
                "schedule_expression",
            ),
            (
                "部署到生产并付款购买服务",
                "human_gate",
                "human_gate_packet_candidate",
                "explicit_user_authority",
            ),
        ]

        for goal, target_layer, candidate_kind, evidence_requirement in fixtures:
            spec = build_goal_spec(
                goal,
                max_turns=20,
                success_criteria=["fixture route is inspectable"],
                non_goals=["do not write taskpool/mission/cron state"],
            )

            dry_run = compile_goal_promotion_dry_run(spec)

            assert dry_run["schema"] == "goal_promotion_dry_run_v1"
            assert dry_run["dry_run"] is True
            assert dry_run["execution_enabled"] is False
            assert dry_run["mutation_allowed"] is False
            assert dry_run["state_mutations"] == []
            assert dry_run["route"]["schema"] == "goal_route_decision_v1"
            assert dry_run["route"]["target_layer"] == target_layer
            assert dry_run["candidate"]["kind"] == candidate_kind
            assert dry_run["candidate"]["target_layer"] == target_layer
            assert dry_run["candidate"]["execute"] is False
            assert dry_run["candidate"]["title"] == goal[:120]
            assert evidence_requirement in dry_run["candidate"]["evidence_requirements"]
            assert dry_run["candidate"]["acceptance_criteria"] == ["fixture route is inspectable"]
            assert dry_run["candidate"]["non_goals"] == ["do not write taskpool/mission/cron state"]
            assert "no taskpool, mission, cron" in dry_run["safety_boundary"]

    def test_goal_manager_promote_dry_run_does_not_mutate_persisted_goal_state(self, hermes_home):
        from hermes_cli.goals import GoalManager, load_goal

        mgr = GoalManager(session_id="promote-dry-run-sid", default_max_turns=20)
        state = mgr.set(
            "修改 Hermes /goal promote dry-run 并运行测试",
            success_criteria=["candidate JSON includes evidence requirements"],
        )
        before = state.to_json()

        dry_run = mgr.promote_dry_run()
        after = load_goal("promote-dry-run-sid")

        assert dry_run["schema"] == "goal_promotion_dry_run_v1"
        assert dry_run["candidate"]["kind"] == "taskpool_task_candidate"
        assert dry_run["mutation_allowed"] is False
        assert after is not None
        assert after.to_json() == before

    def test_compile_workflow_plan_v1_keeps_social_pain_fixtures_dry_run_and_evidence_bound(self):
        from hermes_cli.goals import build_goal_spec, compile_workflow_plan_v1

        spec = build_goal_spec(
            "把 developer Agent 工具痛点接入 workflow_plan_v1",
            max_turns=20,
            success_criteria=["cost/context/reliability fixtures are inspectable"],
        )
        clusters = [
            {
                "id": "developer-cost-control",
                "title": "成本不可预测、token/配额消耗过快",
                "state_object": "agent_task_budget",
                "failure_mode": "unbounded_retry_or_context_bloat",
                "permission_boundary": "budget_hard_stop",
                "source_evidence": [
                    {"url": "https://news.ycombinator.com/item?id=47623434", "excerpt": "quota is finished"},
                    {"url": "https://github.com/cline/cline/pull/10329", "excerpt": "spend limits"},
                ],
                "opportunity_score": 90,
            },
            {
                "id": "browser-anti-bot-boundary",
                "title": "CAPTCHA / Cloudflare 阻断",
                "state_object": "browser_session",
                "failure_mode": "access_control_block",
                "permission_boundary": "human_gate_no_bypass",
                "source_evidence": [
                    {"url": "https://github.com/browser-use/browser-use/issues/4209", "excerpt": "CAPTCHA solving only works"},
                ],
                "opportunity_score": 88,
            },
        ]

        plan = compile_workflow_plan_v1(spec, pain_clusters=clusters)

        assert plan["schema"] == "workflow_plan_v1"
        assert plan["dry_run"] is True
        assert plan["execution_enabled"] is False
        assert plan["target_layer"] == "taskpool"
        assert [node["id"] for node in plan["nodes"]] == [
            "pain:developer-cost-control",
            "pain:browser-anti-bot-boundary",
        ]
        for node in plan["nodes"]:
            assert node["evidence_required"] is True
            assert node["execute"] is False
            assert node["source_evidence"]
        assert plan["nodes"][1]["human_gate_required"] is True
        assert "no external side effects" in plan["safety_boundary"].lower()

    def test_builtin_social_pain_fixtures_compile_for_developer_browser_and_stablecoin(self):
        from hermes_cli.goals import (
            build_goal_spec,
            compile_workflow_plan_v1,
            get_social_pain_insight_fixture,
        )

        categories = ["developer_agent", "browser_automation", "stablecoin_payment"]
        for category in categories:
            fixture = get_social_pain_insight_fixture(category)
            assert fixture["category"] == category
            assert fixture["source_report"].startswith("artifact:")
            assert len(fixture["pain_clusters"]) >= 3
            assert all(cluster["source_evidence"] for cluster in fixture["pain_clusters"])

            spec = build_goal_spec(f"compile {category} fixture", max_turns=20)
            plan = compile_workflow_plan_v1(spec, pain_clusters=fixture["pain_clusters"])
            assert plan["schema"] == "workflow_plan_v1"
            assert plan["execution_enabled"] is False
            assert len(plan["nodes"]) == len(fixture["pain_clusters"])

        browser_plan = compile_workflow_plan_v1(
            build_goal_spec("browser harness safety fixture", max_turns=20),
            pain_clusters=get_social_pain_insight_fixture("browser_automation")["pain_clusters"],
        )
        assert any(node["human_gate_required"] for node in browser_plan["nodes"])

        stablecoin_plan = compile_workflow_plan_v1(
            build_goal_spec("stablecoin preflight fixture", max_turns=20),
            pain_clusters=get_social_pain_insight_fixture("stablecoin_payment")["pain_clusters"],
        )
        assert any("custody" in node["permission_boundary"] for node in stablecoin_plan["nodes"])

    def test_agent_role_topology_fixture_compiles_to_dag_without_deploy_execution(self):
        from hermes_cli.goals import (
            build_goal_spec,
            compile_workflow_plan_v1,
            get_agent_role_topology_fixture,
        )

        fixture = get_agent_role_topology_fixture("openclaw_video_v1")
        spec = build_goal_spec("self-refactor Hermes workflow kernel with evidence", max_turns=20)
        plan = compile_workflow_plan_v1(spec, role_topology=fixture)

        assert plan["schema"] == "workflow_plan_v1"
        assert plan["execution_enabled"] is False
        role_nodes = [node for node in plan["nodes"] if node["kind"] == "agent_role"]
        assert [node["role"] for node in role_nodes] == ["planner", "coder", "tester", "reviewer", "deploy"]
        deploy = role_nodes[-1]
        assert deploy["execute"] is False
        assert deploy["human_gate_required"] is True
        assert deploy["permission_boundary"] == "external_side_effect_human_gate"
        assert "role:reviewer" in deploy["depends_on"]
        assert all(node["evidence_required"] for node in role_nodes)
        assert fixture["source_verification"]["openclaw_repo_verified"] is True
        assert fixture["source_verification"]["niuma_public_source"] == "not_verified"

    def test_compile_workflow_plan_v1_renders_complex_goal_spec_as_acyclic_dry_run_dag(self, tmp_path):
        from hermes_cli.goals import build_goal_spec, compile_workflow_plan_v1

        marker = tmp_path / "should_not_exist"
        spec = build_goal_spec(
            "全盘推进 workflow kernel dry-run DAG",
            max_turns=20,
            success_criteria=["planner/code/test/review/deploy boundaries visible"],
        )
        spec["workflow_steps"] = [
            {
                "id": "plan",
                "title": "Plan bounded slice",
                "evidence_contract": ["objective", "acceptance criteria"],
                "rollback_boundary": "discard generated plan document",
                "permission_boundary": "read_only_design",
            },
            {
                "id": "implement",
                "title": "Implement local patch",
                "depends_on": ["plan"],
                "evidence_contract": ["diff", "targeted tests"],
                "rollback_boundary": "revert scoped git diff",
                "permission_boundary": "local_files_only",
                "command": f"touch {marker}",
                "execute": True,
            },
            {
                "id": "review",
                "title": "Review before external side effects",
                "depends_on": ["implement"],
                "evidence_contract": ["risk notes", "human approval"],
                "rollback_boundary": "do not merge or deploy",
                "permission_boundary": "external_side_effect_human_gate",
            },
        ]

        plan = compile_workflow_plan_v1(spec)

        assert plan["schema"] == "workflow_plan_v1"
        assert plan["dry_run"] is True
        assert plan["execution_enabled"] is False
        assert plan["graph"]["acyclic"] is True
        assert plan["graph"]["topological_order"] == ["goal:plan", "goal:implement", "goal:review"]
        assert plan["edges"] == [
            {"from": "goal:plan", "to": "goal:implement"},
            {"from": "goal:implement", "to": "goal:review"},
        ]
        for node in plan["nodes"]:
            assert node["kind"] == "goal_spec_step"
            assert node["evidence_required"] is True
            assert node["evidence_contract"]
            assert node["rollback_boundary"]
            assert node["execute"] is False
        assert plan["nodes"][1]["executor_ref"]["command"] == f"touch {marker}"
        assert plan["nodes"][2]["human_gate_required"] is True
        assert not marker.exists()

    def test_compile_workflow_plan_v1_rejects_cyclic_goal_spec_steps(self):
        from hermes_cli.goals import build_goal_spec, compile_workflow_plan_v1

        spec = build_goal_spec("cycle should fail closed", max_turns=20)
        spec["workflow_steps"] = [
            {"id": "a", "depends_on": ["b"], "evidence_contract": ["a proof"], "rollback_boundary": "a rollback"},
            {"id": "b", "depends_on": ["a"], "evidence_contract": ["b proof"], "rollback_boundary": "b rollback"},
        ]

        with pytest.raises(ValueError, match="acyclic"):
            compile_workflow_plan_v1(spec)

    def test_compile_dynamic_workflow_run_v1_initializes_script_held_state_without_execution(self, tmp_path):
        from hermes_cli.goals import (
            build_goal_spec,
            compile_dynamic_workflow_run_v1,
            compile_workflow_plan_v1,
        )

        marker = tmp_path / "must_not_exist"
        spec = build_goal_spec("runtime land goal workflow harness", max_turns=20)
        spec["workflow_steps"] = [
            {
                "id": "plan",
                "evidence_contract": ["plan artifact"],
                "permission_boundary": "evidence_only",
            },
            {
                "id": "implement",
                "depends_on": ["plan"],
                "evidence_contract": ["scoped diff"],
                "permission_boundary": "local_patch_only",
                "command": f"touch {marker}",
            },
        ]
        plan = compile_workflow_plan_v1(spec)

        run = compile_dynamic_workflow_run_v1(plan)

        assert run["schema"] == "dynamic_goal_workflow_run_v1"
        assert run["mode"] == "dry_run"
        assert run["execution_enabled"] is False
        assert run["script_runtime"] == "hermes_internal_state_machine"
        assert run["progress"] == {"total": 2, "ready": 1, "blocked": 1, "completed": 0, "human_gated": 0}
        assert run["ready_node_ids"] == ["goal:plan"]
        assert run["node_state"]["goal:plan"]["status"] == "ready"
        assert run["node_state"]["goal:implement"]["status"] == "blocked"
        assert run["cost_visibility"]["worker_dispatches"] == 0
        assert run["state_mutations"] == []
        assert run["next_actions"][0]["execute"] is False
        assert not marker.exists()

    def test_goal_manager_workflow_dry_run_persists_recoverable_dynamic_workflow_state(self, hermes_home):
        from hermes_cli.goals import GoalManager, load_goal

        mgr = GoalManager(session_id="workflow-runtime-sid", default_max_turns=12)
        mgr.set("全盘推进 runtime dynamic workflow harness", success_criteria=["run state persisted"])
        mgr.state.spec["workflow_steps"] = [
            {"id": "plan", "evidence_contract": ["plan evidence"]},
            {"id": "verify", "depends_on": ["plan"], "evidence_contract": ["test evidence"]},
        ]

        packet = mgr.workflow_dry_run()
        reloaded = load_goal("workflow-runtime-sid")

        assert packet["schema"] == "goal_dynamic_workflow_packet_v1"
        assert packet["plan"]["schema"] == "workflow_plan_v1"
        assert packet["run"]["schema"] == "dynamic_goal_workflow_run_v1"
        assert packet["run"]["ready_node_ids"] == ["goal:plan"]
        assert reloaded is not None
        assert reloaded.workflow_run["schema"] == "dynamic_goal_workflow_run_v1"
        assert "workflow_run=2" in GoalManager(session_id="workflow-runtime-sid").status_line()

    def test_set_rejects_empty(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-3")
        with pytest.raises(ValueError):
            mgr.set("")
        with pytest.raises(ValueError):
            mgr.set("   ")

    def test_pause_and_resume(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-4")
        mgr.set("goal text")
        mgr.pause(reason="user-paused")
        assert mgr.state.status == "paused"
        assert not mgr.is_active()
        assert mgr.has_goal()

        mgr.resume()
        assert mgr.state.status == "active"
        assert mgr.is_active()

    def test_clear(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-5")
        mgr.set("goal")
        mgr.clear()
        assert mgr.state is None
        assert not mgr.is_active()

    def test_persistence_across_managers(self, hermes_home):
        """Key invariant: a second manager on the same session sees the goal.

        This is what makes /resume work — each session rebinds its
        GoalManager and picks up the saved state.
        """
        from hermes_cli.goals import GoalManager

        mgr1 = GoalManager(session_id="persist-sid")
        mgr1.set("do the thing")

        mgr2 = GoalManager(session_id="persist-sid")
        assert mgr2.state is not None
        assert mgr2.state.goal == "do the thing"
        assert mgr2.is_active()

    def test_force_checkpoint_restores_route_subgoals_proof_refs_and_plan(self, hermes_home):
        """Goal checkpoint sidecar survives an in-memory manager reset.

        Acceptance: force checkpoint; clear in-memory manager; reload state;
        status shows same route/subgoals/proof refs.
        """
        from hermes_cli.goals import GoalManager, GoalState, save_goal

        mgr = GoalManager(session_id="checkpoint-sid", default_max_turns=7)
        state = mgr.set(
            "修改 Hermes /goal checkpoint sidecar",
            success_criteria=["checkpoint survives reload"],
        )
        goal_id = state.goal_id
        mgr.add_subgoal("preserve subgoals")
        mgr.state.proof_refs.append("proof:cmd:pytest-goal-checkpoint")
        mgr.state.remaining_plan.extend(["write red test", "bind pre-compact reload"])
        checkpoint = mgr.force_checkpoint(reason="test-force")

        assert checkpoint["schema"] == "goal_checkpoint_v1"
        assert checkpoint["goal_id"] == goal_id
        assert checkpoint["proof_refs"] == ["proof:cmd:pytest-goal-checkpoint"]
        assert checkpoint["remaining_plan"] == ["write red test", "bind pre-compact reload"]

        # Simulate stale/older state_meta that lacks fields now carried by the
        # checkpoint sidecar; a fresh manager must merge the sidecar back in.
        save_goal("checkpoint-sid", GoalState(goal="修改 Hermes /goal checkpoint sidecar"))
        mgr._state = None

        reloaded = GoalManager(session_id="checkpoint-sid")
        assert reloaded.state is not None
        assert reloaded.state.goal_id == goal_id
        assert reloaded.state.route == state.route
        assert reloaded.state.subgoals == ["preserve subgoals"]
        assert reloaded.state.proof_refs == ["proof:cmd:pytest-goal-checkpoint"]
        assert reloaded.state.remaining_plan == ["write red test", "bind pre-compact reload"]
        status = reloaded.status_line()
        assert "route=taskpool" in status
        assert "1 subgoal" in status
        assert "proof_refs=1" in status

    def test_pre_compact_rebind_copies_goal_checkpoint_to_new_session(self, hermes_home):
        from hermes_cli.goals import GoalManager, load_goal, rebind_goal_checkpoint

        mgr = GoalManager(session_id="old-compact-sid")
        mgr.set("长期推进 Hermes /goal checkpoint")
        mgr.add_subgoal("restore after compact")
        mgr.state.proof_refs.append("proof:sidecar:old")
        mgr.state.remaining_plan.append("next slice")
        mgr.force_checkpoint(reason="pre-compact")

        copied = rebind_goal_checkpoint(
            "old-compact-sid",
            "new-compact-sid",
            reason="compression",
        )

        assert copied is True
        rebound = load_goal("new-compact-sid")
        assert rebound is not None
        assert rebound.goal == "长期推进 Hermes /goal checkpoint"
        assert rebound.route == mgr.state.route
        assert rebound.subgoals == ["restore after compact"]
        assert rebound.proof_refs == ["proof:sidecar:old"]
        assert rebound.remaining_plan == ["next slice"]

    def test_evaluate_after_turn_done(self, hermes_home):
        """Judge says done → status=done, no continuation."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-1")
        mgr.set("ship it")

        with patch.object(goals, "judge_goal", return_value=("done", "shipped", False, None)):
            decision = mgr.evaluate_after_turn("I shipped the feature.")

        assert decision["verdict"] == "done"
        assert decision["should_continue"] is False
        assert decision["continuation_prompt"] is None
        assert mgr.state.status == "done"
        assert mgr.state.turns_used == 1

    def test_evaluate_after_turn_continue_under_budget(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-2", default_max_turns=5)
        mgr.set("a long goal")

        with patch.object(goals, "judge_goal", return_value=("continue", "more work", False, None)):
            decision = mgr.evaluate_after_turn("made some progress")

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert decision["continuation_prompt"] is not None
        assert "a long goal" in decision["continuation_prompt"]
        assert mgr.state.status == "active"
        assert mgr.state.turns_used == 1

    def test_evaluate_after_turn_budget_exhausted(self, hermes_home):
        """When turn budget hits ceiling, auto-pause instead of continuing."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-3", default_max_turns=2)
        mgr.set("hard goal")

        with patch.object(goals, "judge_goal", return_value=("continue", "not yet", False, None)):
            d1 = mgr.evaluate_after_turn("step 1")
            assert d1["should_continue"] is True
            assert mgr.state.turns_used == 1
            assert mgr.state.status == "active"

            d2 = mgr.evaluate_after_turn("step 2")
            # turns_used is now 2 which equals max_turns → paused
            assert d2["should_continue"] is False
            assert mgr.state.status == "paused"
            assert mgr.state.turns_used == 2
            assert "budget" in (mgr.state.paused_reason or "").lower()

    def test_evaluate_after_turn_inactive(self, hermes_home):
        """evaluate_after_turn is a no-op when goal isn't active."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-4")
        d = mgr.evaluate_after_turn("anything")
        assert d["verdict"] == "inactive"
        assert d["should_continue"] is False

        mgr.set("a goal")
        mgr.pause()
        d2 = mgr.evaluate_after_turn("anything")
        assert d2["verdict"] == "inactive"
        assert d2["should_continue"] is False

    def test_continuation_prompt_shape(self, hermes_home):
        """The continuation prompt must include the goal text verbatim —
        and must be safe to inject as a user-role message (prompt-cache
        invariants: no system-prompt mutation)."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="cont-sid")
        mgr.set("port goal command to hermes")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "port goal command to hermes" in prompt
        assert prompt.strip()  # non-empty


# ──────────────────────────────────────────────────────────────────────
# Smoke: CommandDef is wired
# ──────────────────────────────────────────────────────────────────────


def test_goal_command_in_registry():
    from hermes_cli.commands import resolve_command

    cmd = resolve_command("goal")
    assert cmd is not None
    assert cmd.name == "goal"


def test_goal_command_dispatches_in_cli_registry_helpers():
    """goal shows up in autocomplete / help categories alongside other Session cmds."""
    from hermes_cli.commands import COMMANDS, COMMANDS_BY_CATEGORY

    assert "/goal" in COMMANDS
    session_cmds = COMMANDS_BY_CATEGORY.get("Session", {})
    assert "/goal" in session_cmds


# ──────────────────────────────────────────────────────────────────────
# Auto-pause on consecutive judge parse failures
# ──────────────────────────────────────────────────────────────────────


class TestJudgeParseFailureAutoPause:
    """Regression: weak judge models (e.g. deepseek-v4-flash) that return
    empty strings or non-JSON prose must auto-pause the loop after N turns
    instead of burning the whole turn budget."""

    def test_parse_response_flags_empty_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response("")
        assert verdict == "continue"
        assert parse_failed is True
        assert "empty" in reason.lower()

    def test_parse_response_flags_non_json_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, reason, parse_failed, _w = _parse_judge_response(
            "Let me analyze whether the goal is fully satisfied based on the agent's response..."
        )
        assert verdict == "continue"
        assert parse_failed is True
        assert "not json" in reason.lower()

    def test_parse_response_clean_json_is_not_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        verdict, _, parse_failed, _w = _parse_judge_response(
            '{"done": false, "reason": "more work"}'
        )
        assert verdict == "continue"
        assert parse_failed is False

    def test_api_error_does_not_count_as_parse_failure(self):
        """Transient network/API errors must not trip the auto-pause guard."""
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("connection reset")
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, _, parse_failed, _wd = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert parse_failed is False

    def test_empty_judge_reply_flagged_as_parse_failure(self):
        """End-to-end: judge returns empty content → parse_failed=True."""
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=""))]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, _, parse_failed, _wd = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert parse_failed is True

    def test_auto_pause_after_three_consecutive_parse_failures(self, hermes_home):
        """N=3 consecutive parse failures → auto-pause with config pointer."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES

        assert DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES == 3
        mgr = GoalManager(session_id="parse-fail-sid-1", default_max_turns=20)
        mgr.set("do a thing")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge returned empty response", True, None)
        ):
            d1 = mgr.evaluate_after_turn("step 1")
            assert d1["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 1

            d2 = mgr.evaluate_after_turn("step 2")
            assert d2["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 2

            d3 = mgr.evaluate_after_turn("step 3")
            assert d3["should_continue"] is False
            assert d3["status"] == "paused"
            assert mgr.state.consecutive_parse_failures == 3
            # Message points at the config surface so the user can fix it.
            assert "auxiliary" in d3["message"]
            assert "goal_judge" in d3["message"]
            assert "config.yaml" in d3["message"]

    def test_parse_failure_counter_resets_on_good_reply(self, hermes_home):
        """A single good judge reply resets the counter — transient flakes don't pause."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="parse-fail-sid-2", default_max_turns=20)
        mgr.set("another goal")

        # Two parse failures…
        with patch.object(
            goals, "judge_goal", return_value=("continue", "not json", True, None)
        ):
            mgr.evaluate_after_turn("step 1")
            mgr.evaluate_after_turn("step 2")
            assert mgr.state.consecutive_parse_failures == 2

        # …then one clean reply resets the counter.
        with patch.object(
            goals, "judge_goal", return_value=("continue", "making progress", False, None)
        ):
            d = mgr.evaluate_after_turn("step 3")
            assert d["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 0

    def test_parse_failure_counter_not_incremented_by_api_errors(self, hermes_home):
        """API/transport errors must NOT count toward the auto-pause threshold."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="parse-fail-sid-3", default_max_turns=20)
        mgr.set("goal")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge error: RuntimeError", False, None)
        ):
            for _ in range(5):
                d = mgr.evaluate_after_turn("still going")
                assert d["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 0
            assert mgr.state.status == "active"

    def test_consecutive_parse_failures_persists_across_goalmanager_reloads(
        self, hermes_home
    ):
        """The counter must be durable so cross-session resumes see it."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, load_goal

        mgr = GoalManager(session_id="parse-fail-sid-4", default_max_turns=20)
        mgr.set("persistent goal")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "empty", True, None)
        ):
            mgr.evaluate_after_turn("r")
            mgr.evaluate_after_turn("r")

        reloaded = load_goal("parse-fail-sid-4")
        assert reloaded is not None
        assert reloaded.consecutive_parse_failures == 2


# ──────────────────────────────────────────────────────────────────────
# /subgoal — user-added criteria
# ──────────────────────────────────────────────────────────────────────


class TestGoalStateSubgoalsBackcompat:
    def test_old_state_meta_row_loads_without_subgoals(self):
        """A goal serialized BEFORE the subgoals field existed must
        round-trip with an empty list, not crash."""
        from hermes_cli.goals import GoalState

        legacy = json.dumps({
            "goal": "do a thing",
            "status": "active",
            "turns_used": 2,
            "max_turns": 20,
            "created_at": 1.0,
            "last_turn_at": 2.0,
            "consecutive_parse_failures": 0,
        })
        state = GoalState.from_json(legacy)
        assert state.goal == "do a thing"
        assert state.subgoals == []

    def test_subgoals_round_trip(self):
        from hermes_cli.goals import GoalState
        state = GoalState(goal="g", subgoals=["a", "b", "c"])
        rt = GoalState.from_json(state.to_json())
        assert rt.subgoals == ["a", "b", "c"]


class TestMigrateGoalToSession:
    """migrate_goal_to_session carries a /goal from a parent session to its
    compression continuation child (#33618). load_goal does a flat
    per-session lookup with no lineage walk, so without migration an active
    goal silently dies when compression rotates session_id."""

    def test_migrates_active_goal_to_child(self, hermes_home):
        from hermes_cli.goals import save_goal, load_goal, migrate_goal_to_session, GoalState
        save_goal("parent-sid", GoalState(goal="ship the feature"))
        assert migrate_goal_to_session("parent-sid", "child-sid", reason="compression") is True
        child = load_goal("child-sid")
        assert child is not None and child.goal == "ship the feature"
        # Parent row archived (cleared) so only the child is active.
        parent = load_goal("parent-sid")
        assert parent is not None and parent.status == "cleared"

    def test_no_goal_to_migrate_returns_false(self, hermes_home):
        from hermes_cli.goals import migrate_goal_to_session, load_goal
        assert migrate_goal_to_session("empty-parent", "child2") is False
        assert load_goal("child2") is None

    def test_does_not_clobber_existing_child_goal(self, hermes_home):
        from hermes_cli.goals import save_goal, load_goal, migrate_goal_to_session, GoalState
        save_goal("p3", GoalState(goal="parent goal"))
        save_goal("c3", GoalState(goal="child already has one"))
        assert migrate_goal_to_session("p3", "c3") is False
        assert load_goal("c3").goal == "child already has one"

    def test_same_id_is_noop(self, hermes_home):
        from hermes_cli.goals import save_goal, migrate_goal_to_session, GoalState
        save_goal("same", GoalState(goal="g"))
        assert migrate_goal_to_session("same", "same") is False

    def test_cleared_goal_not_migrated(self, hermes_home):
        from hermes_cli.goals import save_goal, clear_goal, migrate_goal_to_session, load_goal, GoalState
        save_goal("p4", GoalState(goal="done already"))
        clear_goal("p4")
        assert migrate_goal_to_session("p4", "c4") is False
        assert load_goal("c4") is None


class TestGoalManagerSubgoals:
    def test_add_subgoal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-add")
        mgr.set("main goal")
        text = mgr.add_subgoal("  use bullet points  ")
        assert text == "use bullet points"
        assert mgr.state.subgoals == ["use bullet points"]

    def test_add_subgoal_requires_active_goal(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-noactive")
        with pytest.raises(RuntimeError):
            mgr.add_subgoal("oops")

    def test_add_empty_subgoal_rejected(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-empty")
        mgr.set("g")
        with pytest.raises(ValueError):
            mgr.add_subgoal("   ")

    def test_remove_subgoal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-remove")
        mgr.set("g")
        mgr.add_subgoal("first")
        mgr.add_subgoal("second")
        mgr.add_subgoal("third")
        removed = mgr.remove_subgoal(2)
        assert removed == "second"
        assert mgr.state.subgoals == ["first", "third"]

    def test_remove_subgoal_out_of_range(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-oob")
        mgr.set("g")
        mgr.add_subgoal("only")
        with pytest.raises(IndexError):
            mgr.remove_subgoal(5)
        with pytest.raises(IndexError):
            mgr.remove_subgoal(0)

    def test_clear_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-clear")
        mgr.set("g")
        mgr.add_subgoal("a")
        mgr.add_subgoal("b")
        prev = mgr.clear_subgoals()
        assert prev == 2
        assert mgr.state.subgoals == []

    def test_subgoals_persist_across_reloads(self, hermes_home):
        """Subgoals stored in SessionDB survive a fresh GoalManager."""
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-persist")
        mgr.set("g")
        mgr.add_subgoal("first")
        mgr.add_subgoal("second")

        mgr2 = GoalManager(session_id="sub-persist")
        assert mgr2.state.subgoals == ["first", "second"]


class TestContinuationPromptWithSubgoals:
    def test_empty_subgoals_uses_original_template(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="cp-empty")
        mgr.set("ship the feature")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "ship the feature" in prompt
        assert "Additional criteria" not in prompt

    def test_with_subgoals_includes_them(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="cp-with")
        mgr.set("ship the feature")
        mgr.add_subgoal("write tests")
        mgr.add_subgoal("update docs")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "ship the feature" in prompt
        assert "Additional criteria" in prompt
        assert "1. write tests" in prompt
        assert "2. update docs" in prompt


class TestJudgeGoalWithSubgoals:
    def test_judge_uses_subgoals_template_when_provided(self, hermes_home):
        """judge_goal switches templates when subgoals is non-empty.

        We don't actually call the model — we patch the aux client to
        capture the prompt that would be sent.
        """
        from unittest.mock import patch
        from hermes_cli import goals

        captured = {}

        class _FakeMsg:
            content = '{"done": true, "reason": "all done"}'
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return _FakeResp()

        with patch.object(goals, "get_text_auxiliary_client",
                          return_value=(_FakeClient, "fake-model"), create=True), \
             patch.object(goals, "get_auxiliary_extra_body",
                          return_value=None, create=True), \
             patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(_FakeClient, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body",
                   return_value=None):
            verdict, reason, parse_failed, _wd = goals.judge_goal(
                "ship the feature",
                "ok shipped. Tests: command: ./venv/bin/python -m pytest -q -> 1 passed",
                subgoals=["write tests", "update docs"],
            )

        # The aux client was called with a prompt that includes the subgoals.
        sent_messages = captured.get("messages") or []
        user_msg = next((m["content"] for m in sent_messages if m["role"] == "user"), "")
        assert "Additional criteria" in user_msg
        assert "1. write tests" in user_msg
        assert "2. update docs" in user_msg
        assert "every additional criterion" in user_msg
        assert verdict == "done"

    def test_judge_uses_original_template_when_no_subgoals(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        captured = {}

        class _FakeMsg:
            content = '{"done": true, "reason": "ok"}'
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return _FakeResp()

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(_FakeClient, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body",
                   return_value=None):
            goals.judge_goal("ship it", "done", subgoals=None)

        sent_messages = captured.get("messages") or []
        user_msg = next((m["content"] for m in sent_messages if m["role"] == "user"), "")
        assert "Additional criteria" not in user_msg
        assert "ship it" in user_msg


class TestStatusLineSubgoalCount:
    def test_status_line_no_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sl-empty")
        mgr.set("ship it")
        line = mgr.status_line()
        assert "ship it" in line
        assert "subgoal" not in line.lower()

    def test_status_line_with_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sl-with")
        mgr.set("ship it")
        mgr.add_subgoal("a")
        mgr.add_subgoal("b")
        line = mgr.status_line()
        assert "2 subgoals" in line


class TestDynamicWorkflowSafeCompletionSurface:
    def test_workflow_template_dry_run_returns_reusable_non_executing_template(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-template")
        mgr.set("ship feature with tests and review")
        packet = mgr.workflow_template_dry_run(name="ship_feature")

        assert packet["schema"] == "goal_workflow_template_packet_v1"
        template = packet["template"]
        assert template["schema"] == "goal_workflow_template_v1"
        assert template["name"] == "ship_feature"
        assert template["reusable"] is True
        assert template["execution_enabled"] is False
        assert template["worker_dispatch_enabled"] is False
        assert template["adapter_write_enabled"] is False
        assert template["state_mutations"] == []
        assert template["nodes"]
        assert all(node["execute"] is False for node in template["nodes"])
        assert template["safety_boundary"].startswith("Reusable workflow template")

    def test_workflow_execution_proposal_keeps_parallel_subagent_plan_not_executed(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-proposal")
        mgr.set("ship feature with tests and review")
        packet = mgr.workflow_execution_proposal()

        assert packet["schema"] == "goal_workflow_execution_proposal_packet_v1"
        proposal = packet["proposal"]
        assert proposal["schema"] == "goal_workflow_execution_proposal_v1"
        assert proposal["approval_required"] is True
        assert proposal["execute"] is False
        assert proposal["execution_enabled"] is False
        assert proposal["worker_dispatch_enabled"] is False
        assert proposal["adapter_write_enabled"] is False
        assert proposal["write_executed"] is False
        assert proposal["dispatch_plan"]
        assert all(item["dispatch_state"] == "not_executed" for item in proposal["dispatch_plan"])
        assert all(item["parallel_eligible"] is True for item in proposal["dispatch_plan"])
        assert "explicit_authority" in proposal["requested_authority"]
        assert "local_dry_run_only" in proposal["rollback_plan"]

    def test_workflow_template_save_persists_registry_across_goalmanagers(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-registry")
        mgr.set("ship feature with tests and review")
        saved = mgr.workflow_template_save(name="ship_feature")

        assert saved["schema"] == "goal_workflow_template_saved_v1"
        assert saved["write_executed"] is True
        assert saved["registry_key"].endswith(":ship_feature")
        assert saved["template"]["execution_enabled"] is False
        assert saved["receipt"]["operation"] == "template_save"

        reloaded = GoalManager(session_id="workflow-registry")
        listed = reloaded.workflow_template_list()
        assert listed["schema"] == "goal_workflow_template_registry_v1"
        assert listed["count"] == 1
        assert listed["templates"][0]["name"] == "ship_feature"
        assert listed["templates"][0]["execution_enabled"] is False

    def test_local_harness_records_receipt_without_worker_dispatch(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-harness")
        mgr.set("ship feature with tests and review")
        receipt_packet = mgr.workflow_local_harness()

        assert receipt_packet["schema"] == "goal_workflow_local_harness_receipt_v1"
        assert receipt_packet["write_executed"] is True
        assert receipt_packet["worker_dispatch_enabled"] is False
        assert receipt_packet["adapter_write_enabled"] is False
        assert receipt_packet["external_side_effects"] == []
        receipt = receipt_packet["receipt"]
        assert receipt["operation"] == "local_harness_record"
        assert receipt["execution_state"] == "recorded_not_dispatched"
        assert receipt["worker_dispatches"] == 0
        assert receipt["dispatch_plan"]
        assert all(item["dispatch_state"] == "recorded_not_executed" for item in receipt["dispatch_plan"])

        reloaded = GoalManager(session_id="workflow-harness")
        receipts = reloaded.workflow_harness_receipts()
        assert receipts["schema"] == "goal_workflow_local_harness_receipts_v1"
        assert receipts["count"] == 1
        assert receipts["receipts"][0]["receipt_id"] == receipt["receipt_id"]

    def test_governed_local_worker_execute_mutates_exact_ready_nodes_with_readback(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-execute")
        mgr.set("ship feature with tests and review")
        run_packet = mgr.workflow_dry_run()
        target_nodes = list(run_packet["run"]["ready_node_ids"])

        packet = mgr.workflow_execute_local(
            target_nodes=target_nodes,
            max_workers=2,
            max_cost=0,
            max_turns=1,
            allowed_toolsets=["terminal", "file"],
            write_boundary="goal_state_meta_only",
            rollback="restore_previous_workflow_run_snapshot",
            readback="state_meta_checkpoint",
            taskpool_integration=True,
            mission_integration=True,
        )

        assert packet["schema"] == "goal_workflow_governed_local_execution_v1"
        assert packet["write_executed"] is True
        assert packet["worker_dispatch_enabled"] is True
        assert packet["external_side_effects"] == []
        assert packet["authority"]["target_nodes"] == target_nodes
        assert packet["authority"]["max_workers"] == 2
        assert packet["authority"]["max_cost"] == 0
        assert packet["authority"]["max_turns"] == 1
        assert packet["authority"]["allowed_toolsets"] == ["terminal", "file"]
        assert packet["authority"]["taskpool_integration"] is True
        assert packet["authority"]["mission_integration"] is True
        assert packet["receipt"]["executed_node_ids"] == target_nodes
        assert all(item["dispatch_state"] == "completed_local_worker" for item in packet["receipt"]["worker_receipts"])
        assert packet["readback"]["completed"] == len(target_nodes)
        assert packet["readback"]["worker_dispatches"] == len(target_nodes)
        assert packet["rollback"]["available"] is True

        reloaded = GoalManager(session_id="workflow-execute")
        state = reloaded.state
        assert state is not None
        assert state.workflow_run is not None
        for node_id in target_nodes:
            assert state.workflow_run["node_state"][node_id]["status"] == "completed"
            assert state.workflow_run["node_state"][node_id]["worker_dispatches"] == 1

    def test_governed_local_worker_execute_rejects_unready_target_node(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-execute-reject")
        mgr.set("ship feature with tests and review")
        mgr.workflow_dry_run()

        try:
            mgr.workflow_execute_local(target_nodes=["missing-node"], max_workers=1, max_cost=0, max_turns=1)
        except ValueError as exc:
            assert "not ready" in str(exc)
        else:
            raise AssertionError("expected unready node rejection")

    def test_governed_local_worker_execute_rejects_invalid_authority_limits(self, hermes_home):
        from hermes_cli.goals import GoalManager

        invalid_cases = [
            {"max_workers": 0, "max_cost": 0, "max_turns": 1},
            {"max_workers": 1, "max_cost": 0, "max_turns": 0},
            {"max_workers": "bad", "max_cost": 0, "max_turns": 1},
            {"max_workers": 1, "max_cost": "bad", "max_turns": 1},
        ]
        for idx, kwargs in enumerate(invalid_cases):
            mgr = GoalManager(session_id=f"workflow-execute-invalid-{idx}")
            mgr.set("ship feature with tests and review")
            mgr.workflow_dry_run()
            try:
                mgr.workflow_execute_local(**kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected invalid authority rejection for {kwargs}")

    def test_workflow_apply_adapters_writes_taskpool_and_mission_adapter_receipts(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-adapters")
        mgr.set("ship feature with tests and review")
        target_nodes = list(mgr.workflow_dry_run()["run"]["ready_node_ids"])
        execution = mgr.workflow_execute_local(target_nodes=target_nodes, taskpool_integration=True, mission_integration=True)

        packet = mgr.workflow_apply_adapters(execution_id=execution["execution_id"], adapters=["taskpool", "mission"])

        assert packet["schema"] == "goal_workflow_adapter_apply_packet_v1"
        assert packet["write_executed"] is True
        assert packet["adapter_write_enabled"] is True
        assert packet["external_side_effects"] == []
        assert packet["source_execution_id"] == execution["execution_id"]
        assert packet["readback"]["applied_adapters"] == ["taskpool", "mission"]
        assert packet["readback"]["taskpool_receipt_count"] == 1
        assert packet["readback"]["mission_receipt_count"] == 1
        taskpool = packet["adapter_receipts"]["taskpool"]
        mission = packet["adapter_receipts"]["mission"]
        assert taskpool["adapter"] == "taskpool"
        assert taskpool["write_boundary"] == "goal_workflow_taskpool_adapter_state_meta"
        assert mission["adapter"] == "mission"
        assert mission["write_boundary"] == "goal_workflow_mission_adapter_state_meta"
        assert all(ref.startswith("goal-workflow-exec:") for ref in taskpool["evidence_refs"])

        reloaded = GoalManager(session_id="workflow-adapters")
        listed = reloaded.workflow_adapter_receipts()
        assert listed["schema"] == "goal_workflow_adapter_receipts_v1"
        assert listed["taskpool_receipt_count"] == 1
        assert listed["mission_receipt_count"] == 1

    def test_workflow_apply_adapters_is_idempotent_for_same_execution(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-adapters-idempotent")
        mgr.set("ship feature with tests and review")
        target_nodes = list(mgr.workflow_dry_run()["run"]["ready_node_ids"])
        execution = mgr.workflow_execute_local(target_nodes=target_nodes, taskpool_integration=True, mission_integration=True)

        first = mgr.workflow_apply_adapters(execution_id=execution["execution_id"], adapters=["taskpool", "mission"])
        second = mgr.workflow_apply_adapters(execution_id=execution["execution_id"], adapters=["taskpool", "mission"])

        assert first["readback"]["taskpool_receipt_count"] == 1
        assert first["readback"]["mission_receipt_count"] == 1
        assert second["readback"]["taskpool_receipt_count"] == 1
        assert second["readback"]["mission_receipt_count"] == 1
        assert second["idempotent_replay"] is True

    def test_workflow_apply_adapters_rejects_unknown_execution_id(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="workflow-adapters-reject")
        mgr.set("ship feature with tests and review")
        try:
            mgr.workflow_apply_adapters(execution_id="missing", adapters=["taskpool"])
        except ValueError as exc:
            assert "execution receipt not found" in str(exc)
        else:
            raise AssertionError("expected missing execution rejection")
# ──────────────────────────────────────────────────────────────────────
# Wait barrier — parking the goal loop on a background process
# ──────────────────────────────────────────────────────────────────────


class TestWaitBarrier:
    """The /goal wait barrier parks the loop on a live PID and resumes when
    the process exits, without burning turns or calling the judge."""

    @staticmethod
    def _spawn_sleeper():
        """Start a short-lived child process; return its Popen handle."""
        import subprocess
        import sys
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    @staticmethod
    def _dead_pid():
        """A PID that is essentially guaranteed not to be running."""
        return 2_000_000_000

    def test_wait_on_requires_active_goal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="wb-noactive")
        with pytest.raises(RuntimeError):
            mgr.wait_on(12345)

    def test_wait_on_rejects_bad_pid(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="wb-badpid")
        mgr.set("g")
        with pytest.raises(ValueError):
            mgr.wait_on(0)

    def test_parked_on_live_pid_does_not_continue_or_judge(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-live")
            mgr.set("ship it", max_turns=5)
            mgr.wait_on(proc.pid, reason="CI green")
            assert mgr.is_waiting() is True

            # The judge must NOT be called while parked, and no turn is burned.
            judge = MagicMock(return_value=("continue", "x", False, None))
            with patch.object(goals, "judge_goal", judge):
                decision = mgr.evaluate_after_turn("still waiting on CI")

            judge.assert_not_called()
            assert decision["verdict"] == "waiting"
            assert decision["should_continue"] is False
            assert decision["continuation_prompt"] is None
            assert mgr.state.turns_used == 0  # no turn consumed while parked
            assert "CI green" in decision["message"]
            assert mgr.state.status == "active"  # still active, just parked
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_barrier_auto_clears_when_process_exits_and_loop_resumes(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        mgr = GoalManager(session_id="wb-exit")
        mgr.set("ship it", max_turns=5)
        mgr.wait_on(proc.pid, reason="build")
        assert mgr.is_waiting() is True

        # Kill the process — barrier should auto-clear and judging resumes.
        proc.terminate()
        proc.wait(timeout=10)

        assert mgr.is_waiting() is False  # lazy auto-clear
        assert mgr.state.waiting_on_pid is None

        with patch.object(goals, "judge_goal", return_value=("continue", "more", False, None)):
            decision = mgr.evaluate_after_turn("process finished, here are results")

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert mgr.state.turns_used == 1  # now a turn IS consumed

    def test_dead_pid_never_parks(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="wb-dead")
        mgr.set("g", max_turns=5)
        mgr.wait_on(self._dead_pid(), reason="already-dead")
        # is_waiting clears the stale barrier immediately.
        assert mgr.is_waiting() is False

        with patch.object(goals, "judge_goal", return_value=("continue", "go", False, None)):
            decision = mgr.evaluate_after_turn("response")
        assert decision["should_continue"] is True

    def test_stop_waiting_clears_barrier(self, hermes_home):
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-stop")
            mgr.set("g")
            mgr.wait_on(proc.pid)
            assert mgr.is_waiting() is True
            assert mgr.stop_waiting() is True
            assert mgr.state.waiting_on_pid is None
            assert mgr.is_waiting() is False
            assert mgr.stop_waiting() is False  # idempotent
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_pause_and_resume_clear_barrier(self, hermes_home):
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-pause")
            mgr.set("g")
            mgr.wait_on(proc.pid)
            mgr.pause()
            assert mgr.state.waiting_on_pid is None

            mgr.resume()
            assert mgr.state.waiting_on_pid is None
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_barrier_persists_and_reloads(self, hermes_home):
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="wb-persist")
            mgr.set("g")
            mgr.wait_on(proc.pid, reason="deploy")

            # Fresh manager loads the persisted barrier.
            mgr2 = GoalManager(session_id="wb-persist")
            assert mgr2.state.waiting_on_pid == proc.pid
            assert mgr2.state.waiting_reason == "deploy"
            assert mgr2.is_waiting() is True
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_old_state_row_loads_without_barrier_fields(self, hermes_home):
        """Backwards-compat: a state_meta row written before the barrier
        existed must load with no barrier."""
        from hermes_cli.goals import GoalState

        legacy = json.dumps({
            "goal": "old goal",
            "status": "active",
            "turns_used": 2,
            "max_turns": 20,
        })
        st = GoalState.from_json(legacy)
        assert st.goal == "old goal"
        assert st.waiting_on_pid is None
        assert st.waiting_reason is None
        assert st.waiting_since == 0.0
        assert st.waiting_until == 0.0


# ──────────────────────────────────────────────────────────────────────
# Judge-driven auto-wait — the judge parks the loop on its own
# ──────────────────────────────────────────────────────────────────────


class TestJudgeDrivenWait:
    """The judge returns a `wait` verdict (given live background-process
    context) and the loop parks automatically — no manual /goal wait."""

    @staticmethod
    def _spawn_sleeper():
        import subprocess, sys
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    def test_judge_wait_pid_parks_loop(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        proc = self._spawn_sleeper()
        try:
            mgr = GoalManager(session_id="jw-pid", default_max_turns=10)
            mgr.set("ship the PR")
            # Judge sees the running process and says wait-on-pid.
            with patch.object(
                goals, "judge_goal",
                return_value=("wait", "CI watcher still running", False, {"pid": proc.pid}),
            ):
                decision = mgr.evaluate_after_turn(
                    "Pushed the PR, watching CI.",
                    background_processes=[{
                        "pid": proc.pid, "command": "wait_for_pr_green.sh",
                        "status": "running", "uptime_seconds": 12,
                    }],
                )
            assert decision["verdict"] == "wait"
            assert decision["should_continue"] is False
            assert decision["continuation_prompt"] is None
            assert mgr.state.waiting_on_pid == proc.pid
            assert mgr.is_waiting() is True

            # Next turn while still parked: judge must NOT be called again.
            judge = MagicMock()
            with patch.object(goals, "judge_goal", judge):
                d2 = mgr.evaluate_after_turn("still going")
            judge.assert_not_called()
            assert d2["verdict"] == "waiting"
            assert d2["should_continue"] is False
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_judge_wait_seconds_parks_loop(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-secs", default_max_turns=10)
        mgr.set("retry after backoff")
        with patch.object(
            goals, "judge_goal",
            return_value=("wait", "rate limited", False, {"seconds": 120}),
        ):
            decision = mgr.evaluate_after_turn("Hit a 429, backing off.")
        assert decision["verdict"] == "wait"
        assert decision["should_continue"] is False
        assert mgr.state.waiting_until > 0
        assert mgr.state.waiting_on_pid is None
        assert mgr.is_waiting() is True

    def test_time_barrier_clears_after_deadline(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-deadline")
        mgr.set("g")
        mgr.wait_for_seconds(120, reason="backoff")
        assert mgr.is_waiting() is True
        # Force the deadline into the past → barrier auto-clears.
        mgr.state.waiting_until = time.time() - 1
        assert mgr.is_waiting() is False
        assert mgr.state.waiting_until == 0.0

    def test_continue_verdict_still_continues_with_background(self, hermes_home):
        """A running process present but judge says continue → normal loop."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="jw-cont", default_max_turns=10)
        mgr.set("do work")
        with patch.object(
            goals, "judge_goal",
            return_value=("continue", "more to do", False, None),
        ):
            decision = mgr.evaluate_after_turn(
                "made progress",
                background_processes=[{"pid": 999999, "command": "x", "status": "running"}],
            )
        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert mgr.state.waiting_on_pid is None


# ──────────────────────────────────────────────────────────────────────
# Session/trigger barrier — wait on a process's OWN trigger, not just exit
# ──────────────────────────────────────────────────────────────────────


class TestSessionTriggerBarrier:
    """The session barrier (wait_on_session) releases when a process's own
    trigger fires — a watch_patterns match mid-run (process may never exit)
    OR exit — not only on PID exit. CI-safe: uses synthetic registry session
    objects, no real child processes."""

    @staticmethod
    def _inject(sid, *, watch_patterns=None, exited=False):
        import time as _t
        from tools.process_registry import process_registry, ProcessSession
        s = ProcessSession(id=sid, command="watcher.sh", task_id="t",
                           session_key="", cwd="/tmp", started_at=_t.time())
        if watch_patterns:
            s.watch_patterns = list(watch_patterns)
        s.exited = exited
        if exited:
            process_registry._finished[sid] = s
        else:
            process_registry._running[sid] = s
        return s, process_registry

    def test_registry_is_session_waiting_running_unmatched(self, hermes_home):
        s, reg = self._inject("proc_t1", watch_patterns=["READY"])
        assert reg.is_session_waiting("proc_t1") is True

    def test_registry_releases_on_watch_match_while_alive(self, hermes_home):
        s, reg = self._inject("proc_t2", watch_patterns=["READY"])
        assert reg.is_session_waiting("proc_t2") is True
        s._watch_hits = 1  # what _check_watch_patterns sets on a match
        # Released even though the process is STILL running (never exited).
        assert s.exited is False
        assert reg.is_session_waiting("proc_t2") is False

    def test_registry_releases_on_exit_plain_session(self, hermes_home):
        s, reg = self._inject("proc_t3")  # no watch pattern
        assert reg.is_session_waiting("proc_t3") is True
        s.exited = True
        assert reg.is_session_waiting("proc_t3") is False

    def test_registry_unknown_session_never_waits(self, hermes_home):
        from tools.process_registry import process_registry
        assert process_registry.is_session_waiting("proc_does_not_exist") is False

    def test_goal_parks_on_session_and_releases_on_trigger(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        s, reg = self._inject("proc_t4", watch_patterns=["BUILD SUCCESSFUL"])
        mgr = GoalManager(session_id="st-goal", default_max_turns=10)
        mgr.set("wait for the build to succeed")
        with patch.object(
            goals, "judge_goal",
            return_value=("wait", "blocked on build", False, {"session_id": "proc_t4"}),
        ):
            decision = mgr.evaluate_after_turn(
                "Started the build watcher.",
                background_processes=[{
                    "session_id": "proc_t4", "pid": 4242, "command": "watcher.sh",
                    "status": "running", "watch_patterns": ["BUILD SUCCESSFUL"],
                    "watch_hit": False,
                }],
            )
        assert decision["verdict"] == "wait"
        assert mgr.state.waiting_on_session == "proc_t4"
        assert mgr.is_waiting() is True

        # Judge must NOT be called again while parked.
        judge = MagicMock()
        with patch.object(goals, "judge_goal", judge):
            d2 = mgr.evaluate_after_turn("still building")
        judge.assert_not_called()
        assert d2["should_continue"] is False

        # Trigger fires mid-run (process still alive) → barrier releases.
        s._watch_hits = 1
        assert mgr.is_waiting() is False
        assert mgr.state.waiting_on_session is None

        # Loop resumes with a real judge verdict.
        with patch.object(goals, "judge_goal",
                          return_value=("continue", "build done", False, None)):
            d3 = mgr.evaluate_after_turn("build succeeded")
        assert d3["should_continue"] is True

    def test_wait_on_session_validation(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="st-val")
        # No active goal → RuntimeError
        try:
            mgr.wait_on_session("proc_x")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass
        mgr.set("g")
        try:
            mgr.wait_on_session("")
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_session_directive_parsed_from_judge(self, hermes_home):
        from hermes_cli.goals import _parse_judge_response
        v, _, pf, wd = _parse_judge_response(
            '{"verdict": "wait", "wait_on_session": "proc_abc", "reason": "r"}'
        )
        assert v == "wait"
        assert pf is False
        assert wd == {"session_id": "proc_abc"}

    def test_old_state_loads_without_session_field(self, hermes_home):
        from hermes_cli.goals import GoalState
        st = GoalState.from_json(json.dumps({
            "goal": "g", "status": "active", "turns_used": 0, "max_turns": 20,
        }))
        assert st.waiting_on_session is None


# ──────────────────────────────────────────────────────────────────────
# Completion contract (Codex-inspired structured goals)
# ──────────────────────────────────────────────────────────────────────


class TestParseContract:
    def test_plain_goal_no_contract(self):
        from hermes_cli.goals import parse_contract

        headline, contract = parse_contract("Migrate auth to JWT")
        assert headline == "Migrate auth to JWT"
        assert contract.is_empty()

    def test_incidental_colon_not_treated_as_field(self):
        from hermes_cli.goals import parse_contract

        # "Fix bug:" — "fix bug" is not a known alias, so the whole line
        # stays the headline and no contract field is populated.
        headline, contract = parse_contract("Fix bug: the parser drops trailing commas")
        assert headline == "Fix bug: the parser drops trailing commas"
        assert contract.is_empty()

    def test_inline_fields_parsed(self):
        from hermes_cli.goals import parse_contract

        text = (
            "Migrate auth to JWT\n"
            "verify: the auth test suite passes\n"
            "constraints: keep the /login response shape unchanged\n"
            "boundaries: only touch services/auth and its tests\n"
            "stop when: a schema change needs product sign-off"
        )
        headline, contract = parse_contract(text)
        assert headline == "Migrate auth to JWT"
        assert contract.verification == "the auth test suite passes"
        assert contract.constraints == "keep the /login response shape unchanged"
        assert contract.boundaries == "only touch services/auth and its tests"
        assert contract.stop_when == "a schema change needs product sign-off"
        assert not contract.is_empty()

    def test_alias_variants(self):
        from hermes_cli.goals import parse_contract

        _, c = parse_contract("Goal\nverified by: tests green\npreserve: public API")
        assert c.verification == "tests green"
        assert c.constraints == "public API"

    def test_multiple_lines_same_field_joined(self):
        from hermes_cli.goals import parse_contract

        _, c = parse_contract("G\nconstraints: a\nconstraints: b")
        assert c.constraints == "a b"


class TestGoalContractSerialization:
    def test_roundtrip_with_contract(self):
        from hermes_cli.goals import GoalState, GoalContract

        state = GoalState(
            goal="ship it",
            contract=GoalContract(
                verification="pytest passes",
                constraints="don't break the API",
            ),
        )
        restored = GoalState.from_json(state.to_json())
        assert restored.goal == "ship it"
        assert restored.contract.verification == "pytest passes"
        assert restored.contract.constraints == "don't break the API"
        assert restored.has_contract()

    def test_old_row_without_contract_loads_clean(self):
        # A state_meta row written before this feature has no "contract" key.
        from hermes_cli.goals import GoalState

        legacy = '{"goal": "old goal", "status": "active", "turns_used": 2}'
        state = GoalState.from_json(legacy)
        assert state.goal == "old goal"
        assert state.turns_used == 2
        assert state.contract.is_empty()
        assert not state.has_contract()

    def test_render_block_omits_empty_fields(self):
        from hermes_cli.goals import GoalContract

        block = GoalContract(outcome="X", verification="Y").render_block()
        assert "Outcome: X" in block
        assert "Verification: Y" in block
        assert "Constraints" not in block


class TestGoalManagerContract:
    def test_set_with_contract(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        mgr = GoalManager(session_id="c-set")
        mgr.set("ship it", contract=GoalContract(verification="tests pass"))
        assert mgr.has_contract()
        assert "contract" in mgr.status_line()

    def test_set_without_contract_no_marker(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="c-none")
        mgr.set("ship it")
        assert not mgr.has_contract()
        assert "contract" not in mgr.status_line()

    def test_continuation_prompt_includes_contract(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        mgr = GoalManager(session_id="c-cont")
        mgr.set("ship it", contract=GoalContract(verification="run pytest"))
        prompt = mgr.next_continuation_prompt()
        assert "Completion contract" in prompt
        assert "run pytest" in prompt
        assert "concrete evidence" in prompt

    def test_set_contract_after_the_fact(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        mgr = GoalManager(session_id="c-after")
        mgr.set("ship it")
        assert not mgr.has_contract()
        mgr.set_contract(GoalContract(verification="x"))
        assert mgr.has_contract()
        # Survives reload.
        from hermes_cli.goals import GoalManager as GM2
        assert GM2(session_id="c-after").has_contract()

    def test_persistence_roundtrip(self, hermes_home):
        from hermes_cli.goals import GoalManager, GoalContract

        GoalManager(session_id="c-persist").set(
            "ship it", contract=GoalContract(outcome="O", verification="V")
        )
        reloaded = GoalManager(session_id="c-persist")
        assert reloaded.state.contract.outcome == "O"
        assert reloaded.state.contract.verification == "V"


class TestJudgeWithContract:
    def _fake_client(self, captured, content='{"done": false, "reason": "more"}'):
        class _FakeMsg:
            pass
        _FakeMsg.content = content
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return _FakeResp()
        return _FakeClient

    def test_judge_uses_contract_template(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        client = self._fake_client(captured)
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(client, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value=None):
            goals.judge_goal(
                "ship it", "I think it's done",
                contract=GoalContract(verification="pytest -q passes"),
            )
        user_msg = next(
            (m["content"] for m in (captured.get("messages") or []) if m["role"] == "user"), ""
        )
        assert "completion contract" in user_msg.lower()
        assert "pytest -q passes" in user_msg
        assert "concrete evidence" in user_msg

    def test_contract_plus_subgoals_combine(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        client = self._fake_client(captured)
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(client, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value=None):
            goals.judge_goal(
                "ship it", "done",
                subgoals=["write changelog"],
                contract=GoalContract(verification="pytest passes"),
            )
        user_msg = next(
            (m["content"] for m in (captured.get("messages") or []) if m["role"] == "user"), ""
        )
        assert "pytest passes" in user_msg
        assert "write changelog" in user_msg


class TestDraftContract:
    def test_draft_parses_json(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        class _FakeMsg:
            content = (
                '{"outcome": "auth on JWT", "verification": "auth suite green", '
                '"constraints": "no API change", "boundaries": "services/auth", '
                '"stop_when": "schema change needed"}'
            )
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        return _FakeResp()

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(_FakeClient, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value=None):
            contract = goals.draft_contract("Migrate auth to JWT")
        assert contract is not None
        assert contract.outcome == "auth on JWT"
        assert contract.verification == "auth suite green"
        assert not contract.is_empty()

    def test_draft_returns_none_on_bad_json(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        class _FakeMsg:
            content = "I cannot produce JSON, sorry"
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        return _FakeResp()

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(_FakeClient, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value=None):
            assert goals.draft_contract("anything") is None

    def test_draft_returns_none_when_no_client(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(None, None)):
            assert goals.draft_contract("anything") is None


# ──────────────────────────────────────────────────────────────────────
# Compose: completion contract + wait barrier in one judge call
# ──────────────────────────────────────────────────────────────────────


class TestContractAndBackgroundCompose:
    """A contract goal blocked on a background process must surface BOTH
    the contract block and the background-process list to the judge, so it
    can return either done (evidence met) or wait (parked on the poller)."""

    def _capture_client(self, captured, content='{"verdict": "wait", "wait_on_pid": 4242, "reason": "CI still running"}'):
        class _FakeMsg:
            pass
        _FakeMsg.content = content
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return _FakeResp()
        return _FakeClient

    def test_judge_prompt_carries_contract_and_background(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        client = self._capture_client(captured)
        bg = [{
            "session_id": "ci-watch", "pid": 4242, "status": "running",
            "command": "wait_for_pr_green.sh 50501", "trigger": "exit",
        }]
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(client, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value=None):
            verdict, reason, parse_failed, wait_directive = goals.judge_goal(
                "ship the PR",
                "I pushed and started the CI watcher; waiting on it now.",
                contract=GoalContract(verification="PR CI goes green"),
                background_processes=bg,
            )
        user_msg = next(
            (m["content"] for m in (captured.get("messages") or []) if m["role"] == "user"), ""
        )
        # Both surfaces present in one prompt.
        assert "completion contract" in user_msg.lower()
        assert "PR CI goes green" in user_msg
        assert "Background processes" in user_msg
        assert "4242" in user_msg
        # The judge can return a wait verdict on a contract goal.
        assert verdict == "wait"
        assert wait_directive and wait_directive.get("pid") == 4242

    def test_contract_goal_can_still_complete_on_evidence(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals
        from hermes_cli.goals import GoalContract

        captured = {}
        client = self._capture_client(
            captured,
            content='{"verdict": "done", "reason": "CI is green, evidence shown"}',
        )
        bg = [{"session_id": "ci", "pid": 4242, "status": "running", "command": "ci", "trigger": "exit"}]
        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(client, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body", return_value=None):
            verdict, reason, parse_failed, wait_directive = goals.judge_goal(
                "ship the PR",
                "CI finished: 30 passed, 0 failed. Done.",
                contract=GoalContract(verification="PR CI goes green"),
                background_processes=bg,
            )
        assert verdict == "done"
        assert wait_directive is None
