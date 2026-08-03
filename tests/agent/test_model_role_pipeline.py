from copy import deepcopy

import pytest

from agent.model_role_pipeline import (
    INFRASTRUCTURE_FAILURES,
    UnifiedModelRolePipeline,
    _canonical_digest,
    default_model_role_policy,
    infer_surfaces,
)


def _compile_code_turn():
    return UnifiedModelRolePipeline().compile_turn(
        input_id="turn-1",
        source="conversation_loop",
        surfaces=["code_correctness"],
        control_model={"provider": "openai-codex", "model": "gpt-5.6-sol"},
    )


def test_default_policy_freezes_codex_control_claude_code_executor_and_codex_reviewer():
    policy = default_model_role_policy()
    route = _compile_code_turn()

    assert policy["schema"] == "hermes_model_role_policy_v1"
    assert route["schema"] == "hermes_model_role_route_v1"
    assert route["control_plane"]["selected"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
    }
    code_executor = route["executors"]["code_correctness"]["selected"]
    assert code_executor["provider"] == "anthropic"
    assert code_executor["model"] == "claude-sonnet-4-6"
    assert code_executor["execution_engine"] == "coding_agent_cli"
    assert code_executor["command"] == "claude"
    assert route["reviewers"]["code_correctness"]["selected"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
    }
    assert route["authority"]["may_invoke_models"] is False
    assert route["authority"]["may_mutate_global_model_config"] is False
    assert route["route_digest"].startswith("sha256:")


def test_executor_infrastructure_failover_changes_only_executor_and_reselects_independent_reviewer():
    pipeline = UnifiedModelRolePipeline()
    opening = _compile_code_turn()

    successor = pipeline.apply_failure(
        opening,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    assert "provider_timeout" in INFRASTRUCTURE_FAILURES
    assert successor["control_plane"] == opening["control_plane"]
    assert successor["executors"]["code_correctness"]["selected"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
    }
    assert successor["reviewers"]["code_correctness"]["selected"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
    }
    assert successor["transition_receipt"]["changed_roles"] == [
        "executor:code_correctness",
        "reviewer:code_correctness",
    ]
    assert successor["transition_receipt"]["global_model_config_mutated"] is False


def test_reviewer_failover_continues_after_executor_reselected_reviewer():
    pipeline = UnifiedModelRolePipeline()
    after_executor_failure = pipeline.apply_failure(
        _compile_code_turn(),
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )
    opening_executor = deepcopy(after_executor_failure["executors"])

    successor = pipeline.apply_failure(
        after_executor_failure,
        role="reviewer",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    assert successor["executors"] == opening_executor
    assert successor["reviewers"]["code_correctness"]["selected"] == {
        "provider": "xai",
        "model": "grok-4.5",
    }
    assert successor["transition_receipt"]["changed_roles"] == [
        "reviewer:code_correctness"
    ]


def test_executor_failover_never_moves_reviewer_backward_in_its_chain():
    policy = default_model_role_policy()
    policy["surfaces"]["code_correctness"]["executors"] = [
        {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        {"provider": "google-gemini", "model": "gemini-3.1-pro-preview"},
    ]
    policy["surfaces"]["code_correctness"]["reviewers"] = [
        {"provider": "openai-codex", "model": "gpt-5.6-sol"},
        {"provider": "deepseek", "model": "deepseek-v4-pro"},
        {"provider": "xai", "model": "grok-4.5"},
    ]
    pipeline = UnifiedModelRolePipeline(policy=policy)
    route = pipeline.compile_turn(
        input_id="no-reviewer-backtrack",
        source="test",
        surfaces=["code_correctness"],
    )
    route = pipeline.apply_failure(
        route,
        role="reviewer",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )
    assert route["reviewers"]["code_correctness"]["selected_index"] == 1

    successor = pipeline.apply_failure(
        route,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    assert successor["reviewers"]["code_correctness"]["selected_index"] == 1
    assert successor["reviewers"]["code_correctness"]["selected"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
    }


def test_executor_task_failure_does_not_consume_failover_chain():
    pipeline = UnifiedModelRolePipeline()
    opening = _compile_code_turn()

    successor = pipeline.apply_failure(
        opening,
        role="executor",
        surface="code_correctness",
        failure={"category": "task", "code": "tests_failed"},
    )

    assert successor["executors"] == opening["executors"]
    assert successor["reviewers"] == opening["reviewers"]
    assert successor["transition_receipt"]["outcome"] == "return_to_orchestrator"
    assert successor["transition_receipt"]["changed_roles"] == []


def test_reviewer_timeout_switches_only_reviewer_and_never_selects_executor():
    pipeline = UnifiedModelRolePipeline()
    opening = _compile_code_turn()

    successor = pipeline.apply_failure(
        opening,
        role="reviewer",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    assert successor["control_plane"] == opening["control_plane"]
    assert successor["executors"] == opening["executors"]
    reviewer = successor["reviewers"]["code_correctness"]["selected"]
    executor = successor["executors"]["code_correctness"]["selected"]
    assert reviewer != executor
    assert successor["transition_receipt"]["changed_roles"] == [
        "reviewer:code_correctness"
    ]


def test_evidence_failure_is_not_repaired_by_any_model_switch():
    pipeline = UnifiedModelRolePipeline()
    opening = _compile_code_turn()

    successor = pipeline.apply_failure(
        opening,
        role="evidence",
        surface="code_correctness",
        failure={"category": "evidence", "code": "test_evidence_failed"},
    )

    assert successor["control_plane"] == opening["control_plane"]
    assert successor["executors"] == opening["executors"]
    assert successor["reviewers"] == opening["reviewers"]
    assert successor["evidence"]["status"] == "failed"
    assert successor["transition_receipt"]["outcome"] == "evidence_blocked"


def test_route_digest_tamper_is_rejected_before_transition():
    pipeline = UnifiedModelRolePipeline()
    route = _compile_code_turn()
    route["control_plane"]["selected"]["model"] = "tampered-model"

    with pytest.raises(ValueError, match="digest mismatch"):
        pipeline.apply_failure(
            route,
            role="executor",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )


def test_control_plane_failover_and_exhaustion_are_role_local():
    pipeline = UnifiedModelRolePipeline()
    route = _compile_code_turn()
    opening_executors = deepcopy(route["executors"])
    opening_reviewers = deepcopy(route["reviewers"])

    for expected_model in ("claude-sonnet-4.6", "gemini-3.1-pro-preview"):
        route = pipeline.apply_failure(
            route,
            role="control_plane",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )
        assert route["control_plane"]["selected"]["model"] == expected_model
        assert route["executors"] == opening_executors
        assert route["reviewers"] == opening_reviewers

    exhausted = pipeline.apply_failure(
        route,
        role="control_plane",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )
    assert exhausted["control_plane"]["status"] == "blocked_failover_exhausted"
    assert exhausted["control_plane"]["selected"] is None
    assert exhausted["transition_receipt"]["outcome"] == "failover_exhausted"


def test_executor_and_reviewer_failover_exhaustion_clear_stale_selection():
    pipeline = UnifiedModelRolePipeline()
    executor_route = pipeline.apply_failure(
        _compile_code_turn(),
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )
    executor_exhausted = pipeline.apply_failure(
        executor_route,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )
    assert executor_exhausted["executors"]["code_correctness"]["selected"] is None
    assert executor_exhausted["executors"]["code_correctness"]["status"] == "blocked_failover_exhausted"

    reviewer_route = _compile_code_turn()
    for _ in range(3):
        reviewer_route = pipeline.apply_failure(
            reviewer_route,
            role="reviewer",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )
    assert reviewer_route["reviewers"]["code_correctness"]["selected"] is None
    assert reviewer_route["reviewers"]["code_correctness"]["status"] == "no_verdict_failover_exhausted"


@pytest.mark.parametrize(
    "failure",
    [
        {"category": "infrastructure", "code": "not_allowlisted"},
        {"category": "task", "code": "provider_timeout"},
    ],
)
def test_cross_category_or_unlisted_failure_never_triggers_failover(failure):
    pipeline = UnifiedModelRolePipeline()
    opening = _compile_code_turn()
    successor = pipeline.apply_failure(
        opening,
        role="executor",
        surface="code_correctness",
        failure=failure,
    )
    assert successor["executors"] == opening["executors"]
    assert successor["transition_receipt"]["outcome"] == "return_to_orchestrator"


def test_unknown_roles_surfaces_and_malformed_policy_fail_closed():
    pipeline = UnifiedModelRolePipeline()
    route = _compile_code_turn()
    with pytest.raises(ValueError, match="unknown model capability surfaces"):
        pipeline.compile_turn(
            input_id="bad-surface",
            source="test",
            surfaces=["not-real"],
        )
    with pytest.raises(ValueError, match="unknown failure role"):
        pipeline.apply_failure(
            route,
            role="not-real",
            surface="code_correctness",
            failure={"category": "task", "code": "x"},
        )
    with pytest.raises(ValueError, match="unknown executor surface"):
        pipeline.apply_failure(
            route,
            role="executor",
            surface="not-real",
            failure={"category": "task", "code": "x"},
        )
    with pytest.raises(ValueError, match="control_plane"):
        UnifiedModelRolePipeline(
            policy={"schema": "hermes_model_role_policy_v1", "surfaces": {}}
        )


def test_surface_inference_and_evidence_negative_invariant():
    assert infer_surfaces(None) == ["general"]
    assert infer_surfaces("") == ["general"]
    assert infer_surfaces("实现代码、检查安全并验收 UI 截图") == [
        "code_correctness",
        "ui_visual",
        "security_logic",
    ]
    route = _compile_code_turn()
    assert route["evidence"]["model_switch_can_satisfy"] is False


def test_policy_schema_authority_and_structural_validation_fail_closed():
    v2 = default_model_role_policy()
    v2["schema"] = "hermes_model_role_policy_v2"
    with pytest.raises(ValueError, match="unsupported model role policy schema"):
        UnifiedModelRolePipeline(policy=v2)

    malformed_review = default_model_role_policy()
    malformed_review["review_policy"] = "yes"
    with pytest.raises(ValueError, match="review_policy"):
        UnifiedModelRolePipeline(policy=malformed_review)

    expanded_authority = default_model_role_policy()
    expanded_authority["authority"]["may_invoke_models"] = True
    with pytest.raises(ValueError, match="authority.may_invoke_models"):
        UnifiedModelRolePipeline(policy=expanded_authority)

    empty_executor = default_model_role_policy()
    empty_executor["surfaces"]["general"]["executors"] = []
    with pytest.raises(ValueError, match="must be a non-empty list"):
        UnifiedModelRolePipeline(policy=empty_executor)

    malformed_evidence = default_model_role_policy()
    malformed_evidence["surfaces"]["code_correctness"]["required_evidence"] = [
        "tests",
        42,
    ]
    with pytest.raises(ValueError, match="required_evidence"):
        UnifiedModelRolePipeline(policy=malformed_evidence)


def test_non_infrastructure_reviewer_and_control_failures_do_not_fail_over():
    pipeline = UnifiedModelRolePipeline()
    opening = _compile_code_turn()

    reviewer = pipeline.apply_failure(
        opening,
        role="reviewer",
        surface="code_correctness",
        failure={"category": "task", "code": "review_rejected"},
    )
    assert reviewer["reviewers"] == opening["reviewers"]
    assert reviewer["transition_receipt"]["outcome"] == "reviewer_returned_verdict"

    control = pipeline.apply_failure(
        opening,
        role="control_plane",
        surface="code_correctness",
        failure={"category": "task", "code": "plan_rejected"},
    )
    assert control["control_plane"] == opening["control_plane"]
    assert control["transition_receipt"]["outcome"] == "control_returned_to_orchestrator"


def test_blocked_reviewer_is_reconsidered_after_executor_provider_changes():
    policy = default_model_role_policy()
    policy["surfaces"]["code_correctness"]["executors"] = [
        {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        {"provider": "google-gemini", "model": "gemini-3.1-pro-preview"},
    ]
    policy["surfaces"]["code_correctness"]["reviewers"] = [
        {"provider": "anthropic", "model": "claude-opus-4-8"}
    ]
    pipeline = UnifiedModelRolePipeline(policy=policy)
    route = pipeline.compile_turn(
        input_id="blocked-reviewer-reconsidered",
        source="test",
        surfaces=["code_correctness"],
    )
    assert route["reviewers"]["code_correctness"]["status"] == (
        "blocked_no_independent_reviewer"
    )

    successor = pipeline.apply_failure(
        route,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    reviewer = successor["reviewers"]["code_correctness"]
    assert reviewer["selected"] == {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
    }
    assert reviewer["selected_index"] == 0
    assert reviewer["status"] == "recovered"
    assert reviewer["independent_from_executor"] is True
    assert successor["transition_receipt"]["changed_roles"] == [
        "executor:code_correctness",
        "reviewer:code_correctness",
    ]


def test_exhausted_reviewer_chain_is_not_retried_after_executor_changes():
    policy = default_model_role_policy()
    policy["surfaces"]["code_correctness"]["executors"] = [
        {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        {"provider": "google-gemini", "model": "gemini-3.1-pro-preview"},
    ]
    policy["surfaces"]["code_correctness"]["reviewers"] = [
        {"provider": "openai-codex", "model": "gpt-5.6-sol"},
        {"provider": "deepseek", "model": "deepseek-v4-pro"},
    ]
    pipeline = UnifiedModelRolePipeline(policy=policy)
    route = pipeline.compile_turn(
        input_id="exhausted-reviewer-not-retried",
        source="test",
        surfaces=["code_correctness"],
    )
    for _ in range(2):
        route = pipeline.apply_failure(
            route,
            role="reviewer",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )
    assert route["reviewers"]["code_correctness"]["status"] == (
        "no_verdict_failover_exhausted"
    )

    successor = pipeline.apply_failure(
        route,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    reviewer = successor["reviewers"]["code_correctness"]
    assert reviewer["selected"] is None
    assert reviewer["selected_index"] is None
    assert reviewer["status"] == "no_verdict_failover_exhausted"
    assert successor["transition_receipt"]["changed_roles"] == [
        "executor:code_correctness"
    ]


def test_same_provider_reviewer_chain_blocks_independent_review():
    policy = default_model_role_policy()
    policy["surfaces"]["code_correctness"]["reviewers"] = [
        {"provider": "anthropic", "model": "claude-opus-4-8"}
    ]
    route = UnifiedModelRolePipeline(policy=policy).compile_turn(
        input_id="same-provider",
        source="test",
        surfaces=["code_correctness"],
    )
    reviewer = route["reviewers"]["code_correctness"]
    assert reviewer["selected"] is None
    assert reviewer["status"] == "blocked_no_independent_reviewer"
    assert reviewer["independent_from_executor"] is False

    successor = UnifiedModelRolePipeline(policy=policy).apply_failure(
        route,
        role="reviewer",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )
    assert _without_receipt(successor) == _without_receipt(route)
    assert successor["transition_receipt"]["outcome"] == "already_exhausted"


def _without_receipt(route):
    copy = deepcopy(route)
    copy.pop("transition_receipt", None)
    return copy


def test_exhausted_failure_domains_are_idempotent_and_never_restart_chains():
    pipeline = UnifiedModelRolePipeline()

    cases = []
    executor = _compile_code_turn()
    for _ in range(2):
        executor = pipeline.apply_failure(
            executor,
            role="executor",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )
    cases.append((executor, "executor"))

    reviewer = _compile_code_turn()
    for _ in range(3):
        reviewer = pipeline.apply_failure(
            reviewer,
            role="reviewer",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )
    cases.append((reviewer, "reviewer"))

    control = _compile_code_turn()
    for _ in range(3):
        control = pipeline.apply_failure(
            control,
            role="control_plane",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )
    cases.append((control, "control_plane"))

    evidence = pipeline.apply_failure(
        _compile_code_turn(),
        role="evidence",
        surface="code_correctness",
        failure={"category": "evidence", "code": "test_evidence_failed"},
    )
    cases.append((evidence, "evidence"))

    for exhausted, role in cases:
        successor = pipeline.apply_failure(
            exhausted,
            role=role,
            surface="code_correctness",
            failure={
                "category": "evidence" if role == "evidence" else "infrastructure",
                "code": "test_evidence_failed" if role == "evidence" else "provider_timeout",
            },
        )
        assert _without_receipt(successor) == _without_receipt(exhausted)
        assert successor["transition_receipt"]["outcome"] == "already_exhausted"
        assert successor["transition_receipt"]["changed_roles"] == []


def test_failover_on_one_surface_does_not_mutate_other_surface_routes():
    pipeline = UnifiedModelRolePipeline()
    opening = pipeline.compile_turn(
        input_id="multi-surface",
        source="test",
        surfaces=["code_correctness", "ui_visual"],
    )
    opening_visual_executor = deepcopy(opening["executors"]["ui_visual"])
    opening_visual_reviewer = deepcopy(opening["reviewers"]["ui_visual"])

    successor = pipeline.apply_failure(
        opening,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    assert successor["executors"]["ui_visual"] == opening_visual_executor
    assert successor["reviewers"]["ui_visual"] == opening_visual_reviewer


def test_executor_failover_does_not_report_unchanged_none_reviewer():
    policy = default_model_role_policy()
    policy["surfaces"]["code_correctness"]["executors"] = [
        {"provider": "openai-codex", "model": "gpt-5.6-sol"},
        {"provider": "openai-codex", "model": "gpt-5.5-codex"},
    ]
    policy["surfaces"]["code_correctness"]["reviewers"] = [
        {"provider": "openai-codex", "model": "gpt-5.6-sol"},
        {"provider": "openai-codex", "model": "gpt-5.5-codex"},
    ]
    pipeline = UnifiedModelRolePipeline(policy=policy)
    opening = pipeline.compile_turn(
        input_id="none-reviewer",
        source="test",
        surfaces=["code_correctness"],
    )
    assert opening["reviewers"]["code_correctness"]["selected"] is None

    successor = pipeline.apply_failure(
        opening,
        role="executor",
        surface="code_correctness",
        failure={"category": "infrastructure", "code": "provider_timeout"},
    )

    assert successor["reviewers"]["code_correctness"]["selected"] is None
    assert successor["transition_receipt"]["changed_roles"] == [
        "executor:code_correctness"
    ]


def test_executor_failure_rejects_digest_valid_route_missing_reviewer_surface():
    route = _compile_code_turn()
    route["reviewers"].pop("code_correctness")
    route["route_digest"] = _canonical_digest(route)

    with pytest.raises(ValueError, match="unknown reviewer surface"):
        UnifiedModelRolePipeline().apply_failure(
            route,
            role="executor",
            surface="code_correctness",
            failure={"category": "infrastructure", "code": "provider_timeout"},
        )


def test_duplicate_model_targets_in_one_chain_are_rejected():
    policy = default_model_role_policy()
    duplicate = deepcopy(policy["control_plane"]["chain"][0])
    policy["control_plane"]["chain"].insert(1, duplicate)
    with pytest.raises(ValueError, match="duplicates earlier target"):
        UnifiedModelRolePipeline(policy=policy)


@pytest.mark.parametrize(
    "control_model",
    [None, {"provider": "", "model": ""}, {"provider": "openai-codex"}],
)
def test_missing_or_partial_control_model_uses_policy_chain(control_model):
    policy = default_model_role_policy()
    route = UnifiedModelRolePipeline(policy=policy).compile_turn(
        input_id="default-control",
        source="test",
        surfaces=["general"],
        control_model=control_model,
    )
    assert route["control_plane"]["chain"] == policy["control_plane"]["chain"]
    assert route["control_plane"]["selected"] == policy["control_plane"]["chain"][0]


def test_policy_unknown_keys_are_rejected_at_governed_levels():
    mutations = [
        (lambda policy: policy.__setitem__("unexpected", True), "policy"),
        (
            lambda policy: policy["control_plane"].__setitem__("unexpected", True),
            "control_plane",
        ),
        (
            lambda policy: policy["surfaces"]["general"].__setitem__("unexpected", True),
            "surfaces.general",
        ),
        (
            lambda policy: policy["review_policy"].__setitem__("unexpected", True),
            "review_policy",
        ),
        (
            lambda policy: policy["authority"].__setitem__("unexpected", True),
            "authority",
        ),
    ]
    for mutate, expected_path in mutations:
        policy = default_model_role_policy()
        mutate(policy)
        with pytest.raises(ValueError, match=expected_path):
            UnifiedModelRolePipeline(policy=policy)


def test_partial_review_policy_is_rejected_with_missing_key_name():
    policy = default_model_role_policy()
    policy["review_policy"] = {"forbid_executor_self_review": True}
    with pytest.raises(ValueError, match="require_provider_independence"):
        UnifiedModelRolePipeline(policy=policy)


def test_enabled_conversation_router_attaches_role_projection_without_mutating_agent_model():
    from agent.conversation_loop import _run_conductor_intent_router

    class FakeAgent:
        _conductor_config = {"conductor": {"intent_router": {"enabled": True}}}
        provider = "openai-codex"
        model = "gpt-5.6-sol"
        _fallback_index = 0
        _fallback_chain = [{"provider": "legacy", "model": "legacy-fallback"}]

    agent = FakeAgent()
    opening_fallback = deepcopy(agent._fallback_chain)

    result = _run_conductor_intent_router(
        agent,
        user_message="实现代码并独立审核",
    )

    assert result is not None
    projection = result["model_role_pipeline"]
    assert projection["source"] == "conversation_loop"
    assert projection["control_plane"]["selected"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
    }
    assert projection["executors"]["code_correctness"]["selected"]["command"] == "claude"
    assert agent.model == "gpt-5.6-sol"
    assert agent.provider == "openai-codex"
    assert agent._fallback_index == 0
    assert agent._fallback_chain == opening_fallback


def test_router_without_live_agent_identity_uses_frozen_control_chain():
    from agent.conversation_loop import _run_conductor_intent_router

    class FakeAgent:
        _conductor_config = {"conductor": {"intent_router": {"enabled": True}}}

    result = _run_conductor_intent_router(
        FakeAgent(),
        user_message="普通问题",
    )

    assert result is not None
    assert result["model_role_pipeline"]["control_plane"]["selected"] == {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
    }


def test_compile_turn_empty_surfaces_defaults_to_general():
    route = UnifiedModelRolePipeline().compile_turn(
        input_id="empty-surfaces",
        source="test",
        surfaces=[],
    )
    assert route["surfaces"] == ["general"]
    assert route["evidence"]["status"] == "not_required"


def test_route_compilation_is_deterministic_and_does_not_mutate_policy_or_input():
    policy = default_model_role_policy()
    original_policy = deepcopy(policy)
    control = {"provider": "openai-codex", "model": "gpt-5.6-sol"}
    original_control = deepcopy(control)
    pipeline = UnifiedModelRolePipeline(policy=policy)

    first = pipeline.compile_turn(
        input_id="turn-stable",
        source="gateway:telegram",
        surfaces=["ui_visual", "code_correctness", "ui_visual"],
        control_model=control,
    )
    second = pipeline.compile_turn(
        input_id="turn-stable",
        source="gateway:telegram",
        surfaces=["ui_visual", "code_correctness", "ui_visual"],
        control_model=control,
    )

    assert first == second
    assert policy == original_policy
    assert control == original_control
    assert first["surfaces"] == ["ui_visual", "code_correctness"]
    for surface, reviewer_route in first["reviewers"].items():
        assert reviewer_route["selected"] != first["executors"][surface]["selected"]
