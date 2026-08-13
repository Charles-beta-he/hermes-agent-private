import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.model_router import (
    RouteTarget,
    _parse_classifier_tier,
    _resolve_target_runtime,
    append_route_receipt,
    maybe_route_session,
    resolve_routing_policy,
    select_route_decision,
)
from tools.delegate_tool import _append_child_execution_route_receipt, _route_task_creds


def _legacy_config():
    return {
        "smart_model_routing": {
            "enabled": True,
            "candidate_models": ["cheap", "strong"],
        },
        "router": {
            "enabled": True,
            "strategy": "complexity",
            "boundaries": {
                "simple_medium": 0.08,
                "medium_complex": 0.15,
                "complex_reasoning": 0.25,
            },
            "tiers": {
                "SIMPLE": {"provider": "xiaomi", "model": "mimo-v2.5-pro"},
                "MEDIUM": {"provider": "openai-codex", "model": "gpt-5.5"},
                "COMPLEX": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                "REASONING": {"provider": "claude-code", "model": "claude-opus-4-8"},
            },
        },
    }


def test_legacy_router_tiers_are_an_explicit_adapter_not_candidate_models():
    policy = resolve_routing_policy(_legacy_config())

    assert policy.enabled is True
    assert policy.config_source == "router.tiers_legacy_adapter"
    assert policy.tiers["MEDIUM"].provider == "openai-codex"
    assert policy.tiers["MEDIUM"].model == "gpt-5.5"
    assert "candidate_models_not_execution_targets" in policy.diagnostics


def test_candidate_models_without_executable_tiers_fail_closed():
    policy = resolve_routing_policy(
        {"smart_model_routing": {"enabled": True, "candidate_models": ["a", "b"]}}
    )

    assert policy.enabled is False
    assert "no_executable_tiers" in policy.diagnostics


def test_new_three_tier_schema_requires_provider_model_binding():
    policy = resolve_routing_policy(
        {
            "smart_model_routing": {
                "enabled": True,
                "tiers": {
                    "light": {"provider": "xiaomi", "model": "mimo-v2.5-pro"},
                    "standard": {"provider": "openai-codex", "model": "gpt-5.5"},
                    "heavy": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                },
            }
        }
    )

    assert policy.enabled is True
    assert policy.config_source == "smart_model_routing.tiers"
    assert policy.tiers["HEAVY"].provider == "deepseek"


def test_canonical_four_tier_schema_preserves_existing_routing_granularity():
    config = _legacy_config()
    config["smart_model_routing"]["tiers"] = config["router"]["tiers"]
    config["smart_model_routing"]["boundaries"] = config["router"]["boundaries"]
    del config["router"]

    policy = resolve_routing_policy(config)

    assert policy.enabled is True
    assert policy.config_source == "smart_model_routing.tiers"
    assert policy.tier_order == ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")
    assert policy.tiers["COMPLEX"] == RouteTarget("deepseek", "deepseek-v4-pro")
    assert policy.boundaries["complex_reasoning"] == 0.25


def test_classifier_parser_accepts_only_one_exact_tier_token():
    valid = ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")

    assert _parse_classifier_tier(" medium\n", valid) == "MEDIUM"
    assert _parse_classifier_tier("I choose MEDIUM because...", valid) is None
    assert _parse_classifier_tier("SIMPLE or MEDIUM", valid) is None
    assert _parse_classifier_tier("unknown", valid) is None


def test_select_route_decision_binds_tier_provider_and_model():
    decision = select_route_decision(
        "Design a production architecture and prove the invariants",
        _legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
    )

    assert decision.status == "selected"
    assert decision.tier == "REASONING"
    assert decision.provider == "claude-code"
    assert decision.model == "claude-opus-4-8"
    assert decision.config_source == "router.tiers_legacy_adapter"


def test_select_route_decision_loads_runtime_config_when_not_injected(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: _legacy_config(),
    )

    decision = select_route_decision(
        "Design a production architecture and prove the invariants",
        classifier=lambda _text, _tiers: "REASONING",
    )

    assert decision.status == "selected"
    assert decision.model == "claude-opus-4-8"


def test_new_session_routes_once_and_receipt_binds_actual_runtime(tmp_path):
    agent = SimpleNamespace(
        session_id="session-new",
        model="gpt-5.6-sol",
        provider="openai-codex",
        base_url="https://example.invalid",
        api_mode="responses",
        api_key="",
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )
    receipt_path = tmp_path / "model-routing.jsonl"

    def resolver(target, _agent, _config):
        return {
            "success": True,
            "model": target.model,
            "provider": target.provider,
            "api_key": "resolved",
            "base_url": "https://routed.invalid",
            "api_mode": "chat_completions",
        }

    def switch_model(**kwargs):
        agent.model = kwargs["new_model"]
        agent.provider = kwargs["new_provider"]
        agent.base_url = kwargs["base_url"]
        agent.api_mode = kwargs["api_mode"]

    agent.switch_model.side_effect = switch_model

    first = maybe_route_session(
        agent,
        user_message="Implement a small parser",
        messages=[{"role": "user", "content": "Implement a small parser"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "MEDIUM",
        runtime_resolver=resolver,
        receipt_path=receipt_path,
    )
    second = maybe_route_session(
        agent,
        user_message="Continue",
        messages=[
            {"role": "user", "content": "Implement a small parser"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Continue"},
        ],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=resolver,
        receipt_path=receipt_path,
    )

    assert first["status"] == "applied"
    assert second["status"] == "skipped_already_applied"
    assert agent._smart_routing_runtime == {
        "session_id": "session-new",
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "base_url": "https://routed.invalid",
        "api_mode": "chat_completions",
        "tier": "MEDIUM",
    }
    agent.switch_model.assert_called_once()
    persisted = [json.loads(line) for line in receipt_path.read_text().splitlines()]
    assert len(persisted) == 1
    assert persisted[0]["session_id"] == "session-new"
    assert persisted[0]["selected"] == {
        "tier": "MEDIUM",
        "provider": "openai-codex",
        "model": "gpt-5.5",
    }
    assert persisted[0]["actual"]["provider"] == "openai-codex"
    assert persisted[0]["actual"]["model"] == "gpt-5.5"
    assert persisted[0]["switch_status"] == "applied"


def test_resumed_session_does_not_route_or_churn_cache(tmp_path):
    agent = SimpleNamespace(
        session_id="session-resumed",
        model="gpt-5.6-sol",
        provider="openai-codex",
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )

    result = maybe_route_session(
        agent,
        user_message="Continue old work",
        messages=[
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "Continue old work"},
        ],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert result["status"] == "skipped_resumed_session"
    agent.switch_model.assert_not_called()


def test_resumed_session_with_only_system_history_is_not_misclassified_fresh(tmp_path):
    agent = SimpleNamespace(
        session_id="session-compacted-resume",
        model="gpt-5.6-sol",
        provider="openai-codex",
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )

    result = maybe_route_session(
        agent,
        user_message="Continue after compaction",
        messages=[
            {"role": "system", "content": "Summary of the prior session"},
            {"role": "user", "content": "Continue after compaction"},
        ],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert result["status"] == "skipped_resumed_session"
    agent.switch_model.assert_not_called()


def test_explicit_model_override_wins_before_first_turn(tmp_path):
    agent = SimpleNamespace(
        session_id="session-explicit",
        model="chosen-by-user",
        provider="custom:user",
        _smart_routing_entrypoint_eligible=True,
        _smart_routing_user_override_session_id="session-explicit",
        switch_model=MagicMock(),
    )

    result = maybe_route_session(
        agent,
        user_message="Do something hard",
        messages=[{"role": "user", "content": "Do something hard"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert result["status"] == "skipped_explicit_override"
    agent.switch_model.assert_not_called()


def test_switch_failure_is_truthfully_receipted_and_original_runtime_survives(tmp_path):
    agent = SimpleNamespace(
        session_id="session-failure",
        model="working-model",
        provider="working-provider",
        base_url="https://working.invalid",
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(side_effect=RuntimeError("rebuild failed")),
    )
    receipt_path = tmp_path / "model-routing.jsonl"

    result = maybe_route_session(
        agent,
        user_message="Hard task",
        messages=[{"role": "user", "content": "Hard task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "COMPLEX",
        runtime_resolver=lambda target, _agent, _config: {
            "success": True,
            "model": target.model,
            "provider": target.provider,
            "api_key": "",
            "base_url": "https://target.invalid",
            "api_mode": "chat_completions",
        },
        receipt_path=receipt_path,
    )

    assert result["status"] == "switch_failed"
    assert agent.model == "working-model"
    assert agent.provider == "working-provider"
    receipt = json.loads(receipt_path.read_text().strip())
    assert receipt["switch_status"] == "failed"
    assert receipt["actual"]["model"] == "working-model"
    assert receipt["actual"]["provider"] == "working-provider"
    assert "rebuild failed" in receipt["error"]


def test_delegation_routes_fresh_child_credentials_and_records_resolution(tmp_path):
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-session",
        model="parent-model",
        provider="parent-provider",
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }

    routed = _route_task_creds(
        base,
        "Prove the architecture invariants",
        parent,
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=lambda target, _agent, _config: {
            "success": True,
            "model": target.model,
            "provider": target.provider,
            "api_key": "resolved",
            "base_url": "https://child.invalid",
            "api_mode": "chat_completions",
        },
        receipt_path=tmp_path / "model-routing.jsonl",
        task_index=2,
    )

    assert routed["model"] == "claude-opus-4-8"
    assert routed["provider"] == "claude-code"
    receipt = json.loads((tmp_path / "model-routing.jsonl").read_text().strip())
    assert receipt["scope"] == "delegation"
    assert receipt["event_type"] == "route_resolution"
    assert receipt["child_task_index"] == 2
    assert receipt["selected"]["tier"] == "REASONING"
    assert receipt["actual"]["model"] == "claude-opus-4-8"
    assert receipt["status"] == "routing_credentials_resolved"


@pytest.mark.parametrize(
    ("eligible", "override_session", "expected_status"),
    [
        (False, "", "skipped_parent_entrypoint_not_eligible"),
        (True, "parent-pinned", "skipped_parent_explicit_override"),
    ],
)
def test_parent_runtime_authority_blocks_delegation_routing(
    tmp_path, eligible, override_session, expected_status
):
    receipt_path = tmp_path / "model-routing.jsonl"
    parent = SimpleNamespace(
        session_id="parent-pinned",
        model="pinned-model",
        provider="pinned-provider",
        _smart_routing_entrypoint_eligible=eligible,
        _smart_routing_user_override_session_id=override_session,
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }
    classifier = MagicMock(return_value="REASONING")
    resolver = MagicMock()

    unchanged = _route_task_creds(
        base,
        "Prove the architecture invariants",
        parent,
        config=_legacy_config(),
        classifier=classifier,
        runtime_resolver=resolver,
        receipt_path=receipt_path,
    )

    assert unchanged == base
    classifier.assert_not_called()
    resolver.assert_not_called()
    receipt = json.loads(receipt_path.read_text().strip())
    assert receipt["status"] == expected_status
    assert receipt["selected"] is None
    assert receipt["actual"] == {
        "provider": "pinned-provider",
        "model": "pinned-model",
    }


def test_direct_parent_without_authority_marker_blocks_delegation_routing(tmp_path):
    classifier = MagicMock(return_value="REASONING")
    resolver = MagicMock()
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }

    unchanged = _route_task_creds(
        base,
        "Prove the architecture invariants",
        SimpleNamespace(
            session_id="direct-parent",
            model="direct-model",
            provider="direct-provider",
        ),
        config=_legacy_config(),
        classifier=classifier,
        runtime_resolver=resolver,
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert unchanged == base
    classifier.assert_not_called()
    resolver.assert_not_called()


def test_explicit_delegation_model_wins_over_smart_routing(tmp_path):
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-session",
        model="parent-model",
        provider="parent-provider",
    )
    explicit = {
        "model": "explicit-child-model",
        "provider": "explicit-provider",
        "base_url": "https://explicit.invalid",
        "api_key": "explicit",
        "api_mode": "chat_completions",
    }

    unchanged = _route_task_creds(
        explicit,
        "Prove the architecture invariants",
        parent,
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
        task_index=3,
    )

    assert unchanged == explicit
    receipt = json.loads((tmp_path / "model-routing.jsonl").read_text().strip())
    assert receipt["status"] == "skipped_explicit_delegation_model"
    assert receipt["actual"] == {
        "provider": "explicit-provider",
        "model": "explicit-child-model",
    }


def test_explicit_delegation_api_key_pin_wins_over_smart_routing(tmp_path):
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-session",
        model="parent-model",
        provider="parent-provider",
    )
    explicit = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": "delegation-only-key",
        "api_mode": "anthropic_messages",
    }

    unchanged = _route_task_creds(
        explicit,
        "Prove the architecture invariants",
        parent,
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=lambda *_args: {
            "success": True,
            "provider": "other-provider",
            "model": "other-model",
            "api_key": "other-key",
            "api_mode": "chat_completions",
        },
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert unchanged == explicit
    receipt = json.loads((tmp_path / "model-routing.jsonl").read_text().strip())
    assert receipt["status"] == "skipped_explicit_delegation_model"


def test_disabled_delegation_routing_emits_no_resolution_receipt(tmp_path):
    receipt_path = tmp_path / "model-routing.jsonl"
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-session",
        model="parent-model",
        provider="parent-provider",
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }

    assert _route_task_creds(
        base,
        "ordinary delegated task",
        parent,
        config={},
        receipt_path=receipt_path,
    ) == base
    assert not receipt_path.exists()


def test_apply_to_delegation_false_preserves_parent_inheritance(tmp_path):
    config = _legacy_config()
    config["smart_model_routing"]["apply_to_delegation"] = False
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-session",
        model="parent-model",
        provider="parent-provider",
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }

    unchanged = _route_task_creds(
        base,
        "Prove the architecture invariants",
        parent,
        config=config,
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
        task_index=4,
    )

    assert unchanged == base
    receipt = json.loads((tmp_path / "model-routing.jsonl").read_text().strip())
    assert receipt["status"] == "skipped_apply_to_delegation_false"
    assert receipt["actual"] == {
        "provider": "parent-provider",
        "model": "parent-model",
    }


def test_batch_child_routing_is_independent_under_concurrency(tmp_path):
    config = _legacy_config()
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-batch",
        model="parent-model",
        provider="parent-provider",
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }
    receipt_path = tmp_path / "model-routing.jsonl"

    def route(item):
        index, goal, tier = item
        return _route_task_creds(
            base,
            goal,
            parent,
            config=config,
            classifier=lambda _text, _tiers: tier,
            runtime_resolver=lambda target, _parent, _config: {
                "success": True,
                "provider": target.provider,
                "model": target.model,
                "base_url": f"https://{target.provider}.invalid/v1",
                "api_key": f"key-{index}",
                "api_mode": "chat_completions",
            },
            receipt_path=receipt_path,
            task_index=index,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        simple, reasoning = list(
            executor.map(
                route,
                [
                    (0, "summarize this", "SIMPLE"),
                    (1, "prove architecture invariants", "REASONING"),
                ],
            )
        )

    assert simple["model"] == "mimo-v2.5-pro"
    assert simple["api_key"] == "key-0"
    assert reasoning["model"] == "claude-opus-4-8"
    assert reasoning["api_key"] == "key-1"
    assert base == {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }
    receipts = [json.loads(line) for line in receipt_path.read_text().splitlines()]
    assert {row["child_task_index"] for row in receipts} == {0, 1}
    assert {row["actual"]["model"] for row in receipts} == {
        "mimo-v2.5-pro",
        "claude-opus-4-8",
    }


def test_turn_start_wiring_supplies_history_plus_current_user(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", _legacy_config)
    calls = []
    monkeypatch.setattr(
        "agent.model_router.maybe_route_session",
        lambda agent, **kwargs: calls.append((agent, kwargs)) or {"status": "applied"},
    )
    agent = SimpleNamespace(session_id="session-wire")

    result = _route_smart_model_for_turn(
        agent,
        user_message="current",
        conversation_history=[
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
        ],
    )

    assert result["status"] == "applied"
    assert calls[0][1]["user_message"] == "current"
    assert calls[0][1]["messages"][-1] == {"role": "user", "content": "current"}
    assert len(calls[0][1]["messages"]) == 3


def test_turn_start_disabled_policy_skips_history_and_caches_gate(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    config_loads = MagicMock(return_value={})
    durable_reads = MagicMock(side_effect=AssertionError("disabled routing read history"))
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", config_loads)
    agent = SimpleNamespace(
        session_id="disabled-session",
        _session_db=SimpleNamespace(get_messages=durable_reads),
    )

    first = _route_smart_model_for_turn(
        agent, user_message="first", conversation_history=None
    )
    second = _route_smart_model_for_turn(
        agent, user_message="second", conversation_history=None
    )

    assert first["status"] == "disabled"
    assert second["status"] == "disabled"
    config_loads.assert_called_once_with()
    durable_reads.assert_not_called()


def test_turn_start_already_applied_skips_config_and_history(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    config_loads = MagicMock(side_effect=AssertionError("already-applied routing loaded config"))
    durable_reads = MagicMock(side_effect=AssertionError("already-applied routing read history"))
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", config_loads)
    agent = SimpleNamespace(
        session_id="applied-session",
        _smart_routing_session_id="applied-session",
        _smart_routing_applied=True,
        _session_db=SimpleNamespace(get_messages=durable_reads),
    )

    result = _route_smart_model_for_turn(
        agent, user_message="next", conversation_history=None
    )

    assert result["status"] == "skipped_already_applied"
    config_loads.assert_not_called()
    durable_reads.assert_not_called()


def test_turn_start_new_session_rearms_after_applied_fast_path(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", _legacy_config)
    calls = []
    monkeypatch.setattr(
        "agent.model_router.maybe_route_session",
        lambda agent, **kwargs: calls.append((agent, kwargs)) or {"status": "applied"},
    )
    agent = SimpleNamespace(
        session_id="new-session",
        _smart_routing_session_id="old-session",
        _smart_routing_applied=True,
    )

    result = _route_smart_model_for_turn(
        agent,
        user_message="first",
        conversation_history=[{"role": "user", "content": "old"}],
    )

    assert result["status"] == "applied"
    assert len(calls) == 1


def test_turn_start_uses_durable_history_when_caller_omits_it(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", _legacy_config)
    monkeypatch.setattr("agent.model_router.append_route_receipt", lambda *_args: None)
    db = SimpleNamespace(
        get_messages=lambda _sid: [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
        ]
    )
    agent = SimpleNamespace(
        session_id="resumed",
        model="working-model",
        provider="working-provider",
        base_url="https://working.invalid/v1",
        api_key="working-key",
        api_mode="chat_completions",
        _session_db=db,
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )

    result = _route_smart_model_for_turn(
        agent, user_message="next", conversation_history=None
    )

    assert result["status"] == "skipped_resumed_session"
    agent.switch_model.assert_not_called()


def test_turn_start_non_dict_history_fails_closed_as_existing(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", _legacy_config)
    monkeypatch.setattr("agent.model_router.append_route_receipt", lambda *_args: None)
    agent = SimpleNamespace(
        session_id="resumed",
        model="working-model",
        provider="working-provider",
        base_url="https://working.invalid/v1",
        api_key="working-key",
        api_mode="chat_completions",
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )

    result = _route_smart_model_for_turn(
        agent,
        user_message="next",
        conversation_history=[SimpleNamespace(role="user", content="old")],
    )

    assert result["status"] == "skipped_resumed_session"
    agent.switch_model.assert_not_called()


def test_turn_start_empty_cache_without_durable_db_fails_closed(monkeypatch):
    from agent.conversation_loop import _route_smart_model_for_turn

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", _legacy_config)
    monkeypatch.setattr("agent.model_router.append_route_receipt", lambda *_args: None)
    agent = SimpleNamespace(
        session_id="resume-without-readable-db",
        model="working-model",
        provider="working-provider",
        base_url="https://working.invalid/v1",
        api_key="working-key",
        api_mode="chat_completions",
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )

    result = _route_smart_model_for_turn(
        agent,
        user_message="next",
        conversation_history=[],
    )

    assert result["status"] == "skipped_resumed_session"
    agent.switch_model.assert_not_called()


def test_manual_switch_marks_session_override_but_router_switch_does_not(monkeypatch):
    from run_agent import AIAgent

    monkeypatch.setattr(
        "agent.agent_runtime_helpers.switch_model",
        lambda *_args, **_kwargs: "switched",
    )
    manual = AIAgent.__new__(AIAgent)
    manual.session_id = "manual-session"
    automatic = AIAgent.__new__(AIAgent)
    automatic.session_id = "automatic-session"
    automatic._smart_routing_switch_in_progress = True

    assert AIAgent.switch_model(manual, "m", "p") == "switched"
    assert AIAgent.switch_model(automatic, "m", "p") == "switched"

    assert manual._smart_routing_user_override_session_id == "manual-session"
    assert not hasattr(automatic, "_smart_routing_user_override_session_id")


def test_child_execution_receipt_binds_actual_runtime_and_api_calls(tmp_path):
    receipt_path = tmp_path / "model-routing.jsonl"
    child = SimpleNamespace(
        session_id="child-session",
        provider="actual-provider",
        model="actual-model",
        _smart_routing_selected_provider="selected-provider",
        _smart_routing_selected_model="selected-model",
        _smart_routing_selected_tier="HEAVY",
        _smart_routing_config_source="smart_model_routing.tiers",
        _smart_routing_decision_reason="heuristic",
        _smart_routing_diagnostics=("diagnostic",),
    )
    parent = SimpleNamespace(session_id="parent-session")

    _append_child_execution_route_receipt(
        child,
        parent,
        task_index=2,
        status="completed",
        api_calls=3,
        receipt_path=receipt_path,
    )

    receipt = json.loads(receipt_path.read_text().strip())
    assert receipt["scope"] == "delegation_execution"
    assert receipt["schema"] == "hermes_model_route_receipt_v1"
    assert receipt["event_type"] == "execution_route"
    assert isinstance(receipt["created_at"], float)
    assert receipt["session_id"] == "parent-session"
    assert receipt["child_session_id"] == "child-session"
    assert receipt["child_task_index"] == 2
    assert receipt["task_index"] == 2
    assert receipt["selected"] == {
        "tier": "HEAVY",
        "provider": "selected-provider",
        "model": "selected-model",
    }
    assert receipt["actual"] == {
        "provider": "actual-provider",
        "model": "actual-model",
    }
    assert receipt["api_calls"] == 3
    assert receipt["executed"] is True


def test_unrouted_child_execution_does_not_emit_route_receipt(tmp_path):
    receipt_path = tmp_path / "model-routing.jsonl"
    child = SimpleNamespace(
        session_id="child-session",
        provider="configured-provider",
        model="configured-model",
    )

    receipt = _append_child_execution_route_receipt(
        child,
        SimpleNamespace(session_id="parent-session"),
        task_index=0,
        status="completed",
        api_calls=1,
        receipt_path=receipt_path,
    )

    assert receipt["selected"] is None
    assert not receipt_path.exists()


def test_configured_classifier_receives_current_agent_runtime(monkeypatch):
    captured = {}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="MEDIUM"))]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response)
        )
    )

    def fake_client(_task, *, main_runtime=None):
        captured["main_runtime"] = main_runtime
        return client, "classifier-model"

    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client", fake_client)
    config = _legacy_config()
    config["auxiliary"] = {"routing_classifier": {"model": "classifier-model"}}
    runtime = {
        "provider": "openai-codex",
        "model": "gpt-5.6-sol",
        "base_url": "https://current.invalid",
        "api_key": "current-key",
        "api_mode": "responses",
    }

    decision = select_route_decision("classify me", config, main_runtime=runtime)

    assert decision.tier == "MEDIUM"
    assert captured["main_runtime"] is runtime


def test_explicit_delegation_command_pin_wins_over_smart_routing(tmp_path):
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-command-pin",
        model="parent-model",
        provider="copilot-acp",
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
        "command": "copilot",
        "args": ["--stdio"],
    }

    unchanged = _route_task_creds(
        base,
        "prove architecture invariants",
        parent,
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
        task_index=8,
    )

    assert unchanged == base
    receipt = json.loads((tmp_path / "model-routing.jsonl").read_text().strip())
    assert receipt["status"] == "skipped_explicit_delegation_model"


def test_direct_agent_without_entrypoint_opt_in_is_never_auto_routed(tmp_path):
    agent = SimpleNamespace(
        session_id="direct-explicit",
        model="operator-model",
        provider="operator-provider",
        _smart_routing_entrypoint_eligible=False,
        switch_model=MagicMock(),
    )
    result = maybe_route_session(
        agent,
        user_message="hard task",
        messages=[{"role": "user", "content": "hard task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=lambda *_args: {"success": True, "model": "routed-model", "provider": "routed-provider", "api_key": "routed-key"},
        receipt_path=tmp_path / "model-routing.jsonl",
    )
    assert result["status"] == "skipped_entrypoint_not_eligible"
    agent.switch_model.assert_not_called()


def test_cli_route_eligibility_requires_new_unpinned_session():
    from cli import _smart_routing_entrypoint_is_eligible

    assert _smart_routing_entrypoint_is_eligible() is True
    assert _smart_routing_entrypoint_is_eligible(resume="session-id") is False
    assert _smart_routing_entrypoint_is_eligible(model="operator-model") is False
    assert _smart_routing_entrypoint_is_eligible(provider="custom:operator") is False
    assert _smart_routing_entrypoint_is_eligible(api_key="operator-key") is False
    assert _smart_routing_entrypoint_is_eligible(base_url="https://operator.invalid") is False


def test_cli_session_rotation_preserves_manual_model_pin():
    from cli import _carry_smart_routing_override_on_session_rotation

    agent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        _smart_routing_user_override_session_id="old-session",
    )

    _carry_smart_routing_override_on_session_rotation(
        agent,
        old_session_id="old-session",
        new_session_id="new-session",
    )

    assert agent._smart_routing_entrypoint_eligible is False
    assert agent._smart_routing_user_override_session_id == "new-session"


def test_missing_entrypoint_eligibility_is_fail_closed(tmp_path):
    agent = SimpleNamespace(
        session_id="session-missing-eligibility",
        model="old-model",
        provider="old-provider",
        switch_model=MagicMock(),
    )

    result = maybe_route_session(
        agent,
        user_message="medium task",
        messages=[{"role": "user", "content": "medium task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "MEDIUM",
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert result["status"] == "skipped_entrypoint_not_eligible"
    second = maybe_route_session(
        agent,
        user_message="another task",
        messages=[{"role": "user", "content": "another task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        receipt_path=tmp_path / "model-routing.jsonl",
    )
    assert second["status"] == "skipped_already_applied"
    assert len((tmp_path / "model-routing.jsonl").read_text().splitlines()) == 1
    agent.switch_model.assert_not_called()


def test_disabled_session_routing_emits_no_receipt(tmp_path):
    receipt_path = tmp_path / "model-routing.jsonl"
    agent = SimpleNamespace(
        session_id="session-disabled",
        model="configured-model",
        provider="configured-provider",
        switch_model=MagicMock(),
    )

    result = maybe_route_session(
        agent,
        user_message="ordinary task",
        messages=[{"role": "user", "content": "ordinary task"}],
        config={},
        receipt_path=receipt_path,
    )

    assert result["status"] == "disabled"
    assert not receipt_path.exists()
    agent.switch_model.assert_not_called()


def test_cli_agent_setup_projects_route_eligibility():
    from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin

    for eligible in (True, False):
        cli = SimpleNamespace(
            agent=SimpleNamespace(),
            _smart_routing_entrypoint_eligible=eligible,
        )
        CLIAgentSetupMixin._project_smart_routing_entrypoint_eligibility(cli)
        assert cli.agent._smart_routing_entrypoint_eligible is eligible


def test_run_agent_main_marks_only_default_runtime_as_route_eligible(monkeypatch):
    import run_agent

    built = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            built.append(self)

        def run_conversation(self, _query):
            return {
                "completed": True,
                "api_calls": 0,
                "messages": [],
                "final_response": "",
            }

    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)
    run_agent.main(query="probe")
    assert built.pop()._smart_routing_entrypoint_eligible is True

    run_agent.main(query="probe", model="operator-model")
    assert built.pop()._smart_routing_entrypoint_eligible is False

    run_agent.main(query="probe", api_key="operator-key")
    assert built.pop()._smart_routing_entrypoint_eligible is False


def test_configured_classifier_error_fails_closed_on_current_runtime(monkeypatch):
    config = _legacy_config()
    config["auxiliary"] = {"routing_classifier": {"model": "classifier-model"}}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=MagicMock(side_effect=TimeoutError("slow")))))
    monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client", lambda *_args, **_kwargs: (client, "classifier-model"))
    decision = select_route_decision("hard task", config)
    assert decision.status == "classifier_failed"
    assert decision.provider == ""
    assert decision.model == ""


def test_runtime_resolution_preserves_process_transport_metadata(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "copilot-acp", "model": "copilot-acp", "api_key": "process-transport",
            "base_url": "acp://copilot", "api_mode": "chat_completions",
            "command": "copilot", "args": ["--acp", "--stdio"],
        },
    )
    resolved = _resolve_target_runtime(RouteTarget("copilot-acp", "copilot-acp"), None, {})
    assert resolved["command"] == "copilot"
    assert resolved["args"] == ["--acp", "--stdio"]


def test_cold_native_none_api_key_normalizes_to_explicit_empty(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "aws-bedrock",
            "model": "bedrock-model",
            "api_key": None,
            "base_url": "bedrock://local",
            "api_mode": "bedrock_converse",
        },
    )

    resolved = _resolve_target_runtime(
        RouteTarget("aws-bedrock", "bedrock-model"), None, {}
    )

    assert resolved["success"] is True
    assert resolved["api_key"] == ""


def test_runtime_resolution_non_mapping_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: None,
    )

    resolved = _resolve_target_runtime(RouteTarget("custom", "target-model"), None, {})

    assert resolved["success"] is False
    assert "expected mapping" in resolved["error"]


def test_main_session_process_transport_route_fails_closed(tmp_path):
    agent = SimpleNamespace(
        session_id="session-process-target", model="working-model", provider="working-provider",
        _smart_routing_entrypoint_eligible=True, switch_model=MagicMock(),
    )
    result = maybe_route_session(
        agent,
        user_message="hard task",
        messages=[{"role": "user", "content": "hard task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=lambda *_args: {
            "success": True, "model": "claude-opus-4-8", "provider": "claude-code",
            "api_key": "process-transport", "command": "claude", "args": ["--print"],
        },
        receipt_path=tmp_path / "model-routing.jsonl",
    )
    assert result["status"] == "unsupported_hot_switch_runtime"
    agent.switch_model.assert_not_called()


def test_main_session_process_source_route_fails_closed(tmp_path):
    agent = SimpleNamespace(
        session_id="session-process-source",
        model="copilot-acp",
        provider="copilot-acp",
        api_mode="chat_completions",
        acp_command="copilot",
        acp_args=["--acp", "--stdio"],
        _smart_routing_entrypoint_eligible=True,
        switch_model=MagicMock(),
    )
    result = maybe_route_session(
        agent,
        user_message="hard task",
        messages=[{"role": "user", "content": "hard task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=lambda *_args: {
            "success": True,
            "model": "gpt-5.5",
            "provider": "openai-codex",
            "api_key": "key",
            "base_url": "https://new.invalid",
            "api_mode": "codex_responses",
        },
        receipt_path=tmp_path / "model-routing.jsonl",
    )
    assert result["status"] == "unsupported_hot_switch_runtime"
    agent.switch_model.assert_not_called()


def test_rebind_smart_routing_runtime_to_compression_descendant():
    from agent.model_router import rebind_smart_routing_runtime

    agent = SimpleNamespace(
        session_id="parent",
        _smart_routing_runtime={
            "session_id": "parent",
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "base_url": "",
            "api_mode": "responses",
            "tier": "SIMPLE",
        },
        _session_init_model_config={"max_iterations": 90},
    )

    assert rebind_smart_routing_runtime(agent, "descendant") is True
    assert agent._smart_routing_runtime["session_id"] == "descendant"
    assert agent._session_init_model_config["smart_routing_runtime"] == agent._smart_routing_runtime


@pytest.mark.parametrize(
    "hostile_route",
    [
        {
            "session_id": "parent",
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "api_key": "must-not-persist",
        },
        {
            "session_id": "parent",
            "provider": {"name": "openai-codex"},
            "model": "gpt-5.5",
        },
        {
            "session_id": "wrong-parent",
            "provider": "openai-codex",
            "model": "gpt-5.5",
        },
    ],
)
def test_rebind_rejects_hostile_or_session_mismatched_marker_without_child_residue(
    hostile_route,
):
    from agent.model_router import rebind_smart_routing_runtime

    agent = SimpleNamespace(
        session_id="parent",
        _smart_routing_runtime=dict(hostile_route),
        _session_init_model_config={
            "max_iterations": 90,
            "smart_routing_runtime": dict(hostile_route),
        },
    )

    assert rebind_smart_routing_runtime(agent, "descendant") is False
    assert agent._smart_routing_runtime is None
    assert "smart_routing_runtime" not in agent._session_init_model_config


def test_successful_route_updates_durable_model_config_before_session_create(tmp_path):
    agent = SimpleNamespace(
        session_id="session-durable", model="old-model", provider="old-provider",
        base_url="https://old.invalid", api_mode="chat_completions",
        _smart_routing_entrypoint_eligible=True,
        _session_init_model_config={"max_iterations": 90}, _session_db=None,
        switch_model=MagicMock(),
    )
    def switch_model(**kwargs):
        agent.model = kwargs["new_model"]
        agent.provider = kwargs["new_provider"]
        agent.base_url = kwargs["base_url"]
        agent.api_mode = kwargs["api_mode"]
    agent.switch_model.side_effect = switch_model
    result = maybe_route_session(
        agent,
        user_message="medium task",
        messages=[{"role": "user", "content": "medium task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "MEDIUM",
        runtime_resolver=lambda *_args: {
            "success": True, "model": "gpt-5.5", "provider": "openai-codex", "api_key": "key",
            "base_url": "https://new.invalid", "api_mode": "codex_responses",
        },
        receipt_path=tmp_path / "model-routing.jsonl",
    )
    assert result["status"] == "applied"
    assert agent._session_init_model_config.items() >= {
        "model": "gpt-5.5", "provider": "openai-codex",
        "base_url": "https://new.invalid", "api_mode": "codex_responses",
    }.items()
    assert agent._session_init_model_config["smart_routing_runtime"] == {
        "session_id": "session-durable",
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "base_url": "https://new.invalid",
        "api_mode": "codex_responses",
        "tier": "MEDIUM",
    }


def test_successful_route_updates_existing_session_db_row(tmp_path):
    db = MagicMock()
    agent = SimpleNamespace(
        session_id="session-existing-row",
        model="old-model",
        provider="old-provider",
        base_url="https://old.invalid",
        api_mode="chat_completions",
        _smart_routing_entrypoint_eligible=True,
        _session_init_model_config={"max_iterations": 90},
        _session_db=db,
        _session_db_created=True,
        switch_model=MagicMock(),
    )

    def switch_model(**kwargs):
        agent.model = kwargs["new_model"]
        agent.provider = kwargs["new_provider"]
        agent.base_url = kwargs["base_url"]
        agent.api_mode = kwargs["api_mode"]

    agent.switch_model.side_effect = switch_model
    result = maybe_route_session(
        agent,
        user_message="medium task",
        messages=[{"role": "user", "content": "medium task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "MEDIUM",
        runtime_resolver=lambda *_args: {
            "success": True,
            "model": "gpt-5.5",
            "provider": "openai-codex",
            "api_key": "secret-not-persisted",
            "base_url": "https://new.invalid",
            "api_mode": "codex_responses",
        },
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert result["status"] == "applied"
    db.update_session_meta.assert_called_once()
    session_id, encoded = db.update_session_meta.call_args.args
    assert session_id == "session-existing-row"
    persisted = json.loads(encoded)
    assert persisted["model"] == "gpt-5.5"
    assert persisted["provider"] == "openai-codex"
    assert "api_key" not in persisted
    assert db.update_session_meta.call_args.kwargs["model"] == "gpt-5.5"


def test_successful_route_round_trips_real_session_db_without_secrets(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        session_id = "session-real-row"
        db.create_session(
            session_id=session_id,
            source="cli",
            model="old-model",
            model_config={
                "max_iterations": 90,
                "service_tier": "priority",
                "reasoning_config": {"enabled": True, "effort": "high"},
                "api_key": "old-secret-not-preserved",
                "token": "old-token-not-preserved",
                "password": "old-password-not-preserved",
                "unknown_runtime_blob": {"secret": "nested-secret-not-preserved"},
                "smart_routing_runtime": {
                    "session_id": session_id,
                    "provider": "stale-provider",
                    "model": "stale-model",
                    "base_url": "https://stale.invalid",
                    "api_mode": "chat_completions",
                    "tier": "SIMPLE",
                },
            },
        )
        agent = SimpleNamespace(
            session_id=session_id,
            model="old-model",
            provider="old-provider",
            base_url="https://old.invalid",
            api_mode="chat_completions",
            _smart_routing_entrypoint_eligible=True,
            _session_init_model_config={"max_iterations": 90},
            _session_db=db,
            _session_db_created=True,
            switch_model=MagicMock(),
        )

        def switch_model(**kwargs):
            agent.model = kwargs["new_model"]
            agent.provider = kwargs["new_provider"]
            agent.base_url = kwargs["base_url"]
            agent.api_mode = kwargs["api_mode"]

        agent.switch_model.side_effect = switch_model
        result = maybe_route_session(
            agent,
            user_message="medium task",
            messages=[{"role": "user", "content": "medium task"}],
            config=_legacy_config(),
            classifier=lambda _text, _tiers: "MEDIUM",
            runtime_resolver=lambda *_args: {
                "success": True,
                "model": "gpt-5.5",
                "provider": "openai-codex",
                "api_key": "secret-not-persisted",
                "base_url": "https://new.invalid",
                "api_mode": "codex_responses",
            },
            receipt_path=tmp_path / "model-routing.jsonl",
        )

        row = db.get_session(session_id)
        assert row is not None
        persisted = json.loads(row["model_config"])
        assert result["status"] == "applied"
        assert row["model"] == "gpt-5.5"
        assert persisted.items() >= {
            "model": "gpt-5.5",
            "provider": "openai-codex",
            "base_url": "https://new.invalid",
            "api_mode": "codex_responses",
            "service_tier": "priority",
            "reasoning_config": {"enabled": True, "effort": "high"},
        }.items()
        assert "api_key" not in persisted
        assert "token" not in persisted
        assert "password" not in persisted
        assert "unknown_runtime_blob" not in persisted
        assert "secret-not-persisted" not in row["model_config"]
        assert "old-secret-not-preserved" not in row["model_config"]
        assert "old-token-not-preserved" not in row["model_config"]
        assert "old-password-not-preserved" not in row["model_config"]
        assert "nested-secret-not-preserved" not in row["model_config"]
        assert persisted["smart_routing_runtime"] == {
            "session_id": session_id,
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "base_url": "https://new.invalid",
            "api_mode": "codex_responses",
            "tier": "MEDIUM",
        }
    finally:
        db.close()


@pytest.mark.parametrize(
    "sensitive_url",
    [
        "https://user:password@example.invalid/v1",
        "https://example.invalid/v1?access_token=must-not-persist",
        "https://example.invalid/v1?key=must-not-persist",
        "https://example.invalid/v1?sig=must-not-persist",
        "https://example.invalid/v1?signature=must-not-persist",
        "https://example.invalid/v1#access_token=must-not-persist",
        "https://api.telegram.org/bot123456:secret/getMe",
    ],
)
def test_active_runtime_sensitive_base_url_is_not_persisted(tmp_path, sensitive_url):
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        session_id = "session-sensitive-active-url"
        db.create_session(session_id=session_id, source="cli")
        agent = SimpleNamespace(
            session_id=session_id,
            model="old-model",
            provider="old-provider",
            base_url="https://old.invalid",
            api_mode="chat_completions",
            _smart_routing_entrypoint_eligible=True,
            _session_init_model_config={"max_iterations": 90},
            _session_db=db,
            _session_db_created=True,
            switch_model=MagicMock(),
        )

        def switch_model(**kwargs):
            agent.model = kwargs["new_model"]
            agent.provider = kwargs["new_provider"]
            agent.base_url = kwargs["base_url"]
            agent.api_mode = kwargs["api_mode"]

        agent.switch_model.side_effect = switch_model
        result = maybe_route_session(
            agent,
            user_message="medium task",
            messages=[{"role": "user", "content": "medium task"}],
            config=_legacy_config(),
            classifier=lambda _text, _tiers: "MEDIUM",
            runtime_resolver=lambda *_args: {
                "success": True,
                "model": "gpt-5.5",
                "provider": "openai-codex",
                "api_key": "runtime-secret",
                "base_url": sensitive_url,
                "api_mode": "codex_responses",
            },
            receipt_path=tmp_path / "model-routing.jsonl",
        )

        row = db.get_session(session_id)
        assert row is not None
        persisted = json.loads(row["model_config"])
        assert result["status"] == "applied"
        assert "smart_routing_runtime" not in persisted
        assert "base_url" not in persisted
        assert sensitive_url not in row["model_config"]
        assert "runtime-secret" not in row["model_config"]
        assert agent._smart_routing_runtime is None
    finally:
        db.close()


def test_already_active_route_repairs_durable_runtime_identity(tmp_path):
    db = MagicMock()
    agent = SimpleNamespace(
        session_id="session-already-active",
        model="gpt-5.5",
        provider="openai-codex",
        base_url="https://active.invalid",
        api_mode="codex_responses",
        _smart_routing_entrypoint_eligible=True,
        _session_init_model_config={"max_iterations": 90},
        _session_db=db,
        _session_db_created=True,
        switch_model=MagicMock(),
    )

    result = maybe_route_session(
        agent,
        user_message="medium task",
        messages=[{"role": "user", "content": "medium task"}],
        config=_legacy_config(),
        classifier=lambda _text, _tiers: "MEDIUM",
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert result["status"] == "already_active"
    assert agent._session_init_model_config["provider"] == "openai-codex"
    db.update_session_meta.assert_called_once()


def test_parallel_receipt_appends_are_complete_json_lines(tmp_path):
    receipt_path = tmp_path / "parallel-routing.jsonl"
    count = 64

    def append_one(index):
        append_route_receipt(
            {"schema": "hermes_model_route_receipt_v1", "index": index},
            receipt_path,
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(append_one, range(count)))

    lines = receipt_path.read_text().splitlines()
    decoded = [json.loads(line) for line in lines]
    assert len(decoded) == count
    assert sorted(item["index"] for item in decoded) == list(range(count))


def test_same_agent_concurrent_first_turn_classifies_only_once(tmp_path):
    calls = 0
    entered = threading.Event()
    release = threading.Event()
    calls_lock = threading.Lock()
    agent = SimpleNamespace(
        session_id="session-race", model="old-model", provider="old-provider",
        _smart_routing_entrypoint_eligible=True, switch_model=MagicMock(),
    )
    def classifier(_text, _tiers):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        release.wait(timeout=2)
        return "MEDIUM"
    def run_once():
        return maybe_route_session(
            agent,
            user_message="race",
            messages=[{"role": "user", "content": "race"}],
            config=_legacy_config(), classifier=classifier,
            runtime_resolver=lambda *_args: {"success": True, "model": "gpt-5.5", "provider": "openai-codex", "api_key": "key"},
            receipt_path=tmp_path / "model-routing.jsonl",
        )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_once)
        assert entered.wait(timeout=2)
        second = executor.submit(run_once)
        release.set()
        results = [first.result(timeout=2), second.result(timeout=2)]
    assert calls == 1
    assert sorted(result["status"] for result in results) == ["applied", "skipped_already_applied"]


def test_string_boolean_does_not_enable_routing_policy():
    config = _legacy_config()
    config["smart_model_routing"]["enabled"] = "false"
    assert resolve_routing_policy(config).enabled is False


def test_string_false_apply_to_delegation_fails_closed(tmp_path):
    config = _legacy_config()
    config["smart_model_routing"]["apply_to_delegation"] = "false"
    parent = SimpleNamespace(
        _smart_routing_entrypoint_eligible=True,
        session_id="parent-malformed-apply",
        model="parent-model",
        provider="parent-provider",
    )
    base = {
        "model": None,
        "provider": None,
        "base_url": None,
        "api_key": None,
        "api_mode": None,
    }

    unchanged = _route_task_creds(
        base,
        "hard task",
        parent,
        config=config,
        classifier=lambda _text, _tiers: "REASONING",
        runtime_resolver=MagicMock(),
        receipt_path=tmp_path / "model-routing.jsonl",
    )

    assert unchanged == base
    receipt = json.loads((tmp_path / "model-routing.jsonl").read_text().strip())
    assert receipt["status"] == "skipped_invalid_apply_to_delegation"
