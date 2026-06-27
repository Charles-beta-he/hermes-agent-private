"""Tests for hermes_cli/goals.py — persistent cross-turn goals."""

from __future__ import annotations

import json
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

        done, reason, _ = _parse_judge_response('{"done": true, "reason": "all good"}')
        assert done is True
        assert reason == "all good"

    def test_clean_json_continue(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response('{"done": false, "reason": "more work needed"}')
        assert done is False
        assert reason == "more work needed"

    def test_json_in_markdown_fence(self):
        from hermes_cli.goals import _parse_judge_response

        raw = '```json\n{"done": true, "reason": "done"}\n```'
        done, reason, _ = _parse_judge_response(raw)
        assert done is True
        assert "done" in reason

    def test_json_embedded_in_prose(self):
        """Some models prefix reasoning before emitting JSON — we extract it."""
        from hermes_cli.goals import _parse_judge_response

        raw = 'Looking at this... the agent says X. Verdict: {"done": false, "reason": "partial"}'
        done, reason, _ = _parse_judge_response(raw)
        assert done is False
        assert reason == "partial"

    def test_string_done_values(self):
        from hermes_cli.goals import _parse_judge_response

        for s in ("true", "yes", "done", "1"):
            done, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert done is True
        for s in ("false", "no", "not yet"):
            done, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert done is False

    def test_malformed_json_fails_open(self):
        """Non-JSON → not done, with error-ish reason (so judge_goal can map to continue)."""
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response("this is not json at all")
        assert done is False
        assert reason  # non-empty

    def test_empty_response(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response("")
        assert done is False
        assert reason


# ──────────────────────────────────────────────────────────────────────
# judge_goal — fail-open semantics
# ──────────────────────────────────────────────────────────────────────


class TestJudgeGoal:
    def test_empty_goal_skipped(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _ = judge_goal("", "some response")
        assert verdict == "skipped"

    def test_empty_response_continues(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _ = judge_goal("ship the thing", "")
        assert verdict == "continue"

    def test_no_aux_client_continues(self):
        """Fail-open: if no aux client, we must return continue, not skipped/done."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(None, None),
        ):
            verdict, _, _ = goals.judge_goal("my goal", "my response")
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
            verdict, reason, _ = goals.judge_goal("goal", "response")
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
            verdict, reason, _ = goals.judge_goal(
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
            verdict, reason, _ = goals.judge_goal("goal", "agent response")
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
            verdict, reason, _ = goals.judge_goal("ship a tested patch", "Done — the patch is finished.")
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
            verdict, reason, _ = goals.judge_goal("ship a tested patch", last_response)
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
            verdict, reason, _ = goals.judge_goal(
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

        with patch.object(goals, "judge_goal", return_value=("done", "shipped", False)):
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

        with patch.object(goals, "judge_goal", return_value=("continue", "more work", False)):
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

        with patch.object(goals, "judge_goal", return_value=("continue", "not yet", False)):
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

        done, reason, parse_failed = _parse_judge_response("")
        assert done is False
        assert parse_failed is True
        assert "empty" in reason.lower()

    def test_parse_response_flags_non_json_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, parse_failed = _parse_judge_response(
            "Let me analyze whether the goal is fully satisfied based on the agent's response..."
        )
        assert done is False
        assert parse_failed is True
        assert "not json" in reason.lower()

    def test_parse_response_clean_json_is_not_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        done, _, parse_failed = _parse_judge_response(
            '{"done": false, "reason": "more work"}'
        )
        assert done is False
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
            verdict, _, parse_failed = goals.judge_goal("goal", "response")
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
            verdict, _, parse_failed = goals.judge_goal("goal", "response")
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
            goals, "judge_goal", return_value=("continue", "judge returned empty response", True)
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
            goals, "judge_goal", return_value=("continue", "not json", True)
        ):
            mgr.evaluate_after_turn("step 1")
            mgr.evaluate_after_turn("step 2")
            assert mgr.state.consecutive_parse_failures == 2

        # …then one clean reply resets the counter.
        with patch.object(
            goals, "judge_goal", return_value=("continue", "making progress", False)
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
            goals, "judge_goal", return_value=("continue", "judge error: RuntimeError", False)
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
            goals, "judge_goal", return_value=("continue", "empty", True)
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
            verdict, reason, parse_failed = goals.judge_goal(
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
