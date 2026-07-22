from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.image_intent_routing import (
    CONTRACT_VERSION,
    ImageIntent,
    build_provider_capability_contract,
    classify_image_intent_shadow,
    evaluate_image_intent_contract,
    finalize_image_intent_shadow,
    run_image_intent_shadow,
)
from agent.conversation_loop import _run_image_intent_shadow_router


class FakeProvider:
    name = "fake"

    def capabilities(self):
        return {"modalities": ["text", "image"], "max_reference_images": 3}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("生成一张赛博朋克西安城墙的图片", ImageIntent.GENERATE_ASSET),
        ("Create an image of a red fox", ImageIntent.GENERATE_ASSET),
        ("把这张图片改成水彩风格", ImageIntent.EDIT_ASSET),
        ("Edit this image to remove the background", ImageIntent.EDIT_ASSET),
        ("分析这张图片里的布局问题", ImageIntent.ANALYZE_ASSET),
        ("Analyze this image for accessibility issues", ImageIntent.ANALYZE_ASSET),
        ("只帮我写一个文生图提示词，不要生成图片", ImageIntent.PROMPT_ONLY),
        ("Write an image prompt only; do not generate it", ImageIntent.PROMPT_ONLY),
        ("解释一下 Image Generator 的工作原理", ImageIntent.EXPLAIN_ONLY),
        ("Explain how image generation works", ImageIntent.EXPLAIN_ONLY),
        ("想做个视觉方案", ImageIntent.AMBIGUOUS),
    ],
)
def test_shadow_classifier_is_conservative_and_negative_cases_win(text, expected):
    decision = classify_image_intent_shadow(text)
    assert decision.intent is expected
    assert decision.schema_version == CONTRACT_VERSION
    assert decision.source == "deterministic_shadow_v1"


def test_provider_capability_contract_normalizes_existing_provider_surface():
    contract = build_provider_capability_contract(FakeProvider(), model="fake-v2")
    assert contract.schema_version == CONTRACT_VERSION
    assert contract.provider == "fake"
    assert contract.model == "fake-v2"
    assert contract.text_to_image is True
    assert contract.image_edit is True
    assert contract.reference_images is True
    assert contract.max_reference_images == 3


def test_provider_capability_contract_accepts_authoritative_capability_mapping():
    contract = build_provider_capability_contract(
        None,
        capabilities={
            "provider": "FAL.ai",
            "model": "Flux Edit",
            "modalities": ["text", "image"],
            "max_reference_images": 4,
        },
    )
    assert contract.provider == "FAL.ai"
    assert contract.model == "Flux Edit"
    assert contract.text_to_image is True
    assert contract.image_edit is True
    assert contract.max_reference_images == 4


def test_contract_requires_artifact_without_forcing_runtime_execution():
    provider = build_provider_capability_contract(FakeProvider())
    decision = classify_image_intent_shadow("生成一张猫的图片")
    route = evaluate_image_intent_contract(
        decision,
        provider=provider,
        image_tool_available=True,
    )
    assert route.artifact_required is True
    assert route.recommended_tool_policy == "prefer_image_generate"
    assert route.runtime_mutation_authorized is False
    assert route.eligible is True


def test_negative_intents_forbid_generation_in_contract():
    provider = build_provider_capability_contract(FakeProvider())
    for text in (
        "解释图片生成原理",
        "只写提示词，不要生成图片",
        "分析这张图片",
    ):
        route = evaluate_image_intent_contract(
            classify_image_intent_shadow(text),
            provider=provider,
            image_tool_available=True,
        )
        assert route.artifact_required is False
        assert route.recommended_tool_policy == "forbid_image_generate"
        assert route.runtime_mutation_authorized is False


def test_missing_provider_or_tool_never_claims_eligibility():
    decision = classify_image_intent_shadow("生成一张图片")
    no_provider = evaluate_image_intent_contract(
        decision,
        provider=None,
        image_tool_available=True,
    )
    no_tool = evaluate_image_intent_contract(
        decision,
        provider=build_provider_capability_contract(FakeProvider()),
        image_tool_available=False,
    )
    assert no_provider.eligible is False
    assert no_provider.blocker == "provider_unavailable"
    assert no_tool.eligible is False
    assert no_tool.blocker == "image_tool_unavailable"


def test_default_config_keeps_image_intent_runtime_off():
    from hermes_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["image_intent_routing"] == {
        "enabled": False,
        "mode": "shadow",
        "kill_switch": False,
        "ledger_enabled": False,
        "ledger_path": "",
    }


def test_shadow_runtime_is_default_off_and_kill_switch_wins(tmp_path):
    assert run_image_intent_shadow(
        user_message="生成一张图片",
        config={},
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="t0",
    ) is None

    killed = run_image_intent_shadow(
        user_message="生成一张图片",
        config={
            "image_intent_routing": {
                "enabled": True,
                "mode": "shadow",
                "kill_switch": True,
                "ledger_path": str(tmp_path / "events.jsonl"),
            }
        },
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="t1",
    )
    assert killed["status"] == "disabled_by_kill_switch"
    assert not (tmp_path / "events.jsonl").exists()


def test_shadow_runtime_writes_local_readback_without_changing_policy(tmp_path):
    ledger = tmp_path / "events.jsonl"
    receipt = run_image_intent_shadow(
        user_message="生成一张城市夜景图片",
        config={
            "image_intent_routing": {
                "enabled": True,
                "mode": "shadow",
                "kill_switch": False,
                "ledger_enabled": True,
                "ledger_path": str(ledger),
            }
        },
        image_tool_available=True,
        provider=FakeProvider(),
        model="fake-v2",
        turn_id="turn-1",
    )
    assert receipt["status"] == "observed"
    assert receipt["runtime_mutation_authorized"] is False
    assert receipt["route"]["recommended_tool_policy"] == "prefer_image_generate"
    assert receipt["route"]["runtime_mutation_authorized"] is False

    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows == [receipt]
    assert rows[0]["turn_id"] == "turn-1"
    assert "user_message" not in rows[0]


def test_shadow_ledger_tightens_existing_file_permissions(tmp_path):
    ledger = tmp_path / "events.jsonl"
    ledger.write_text("", encoding="utf-8")
    ledger.chmod(0o644)
    run_image_intent_shadow(
        user_message="生成一张城市夜景图片",
        config={
            "image_intent_routing": {
                "enabled": True,
                "mode": "shadow",
                "ledger_enabled": True,
                "ledger_path": str(ledger),
            }
        },
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="turn-permissions",
    )
    assert ledger.stat().st_mode & 0o777 == 0o600


def test_enforce_mode_is_rejected_until_separately_certified(tmp_path):
    receipt = run_image_intent_shadow(
        user_message="生成一张图片",
        config={
            "image_intent_routing": {
                "enabled": True,
                "mode": "enforce",
                "ledger_enabled": True,
                "ledger_path": str(tmp_path / "events.jsonl"),
            }
        },
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="turn-enforce",
    )
    assert receipt["status"] == "unsupported_mode"
    assert receipt["runtime_mutation_authorized"] is False


def test_conversation_loop_shadow_wiring_is_default_off(monkeypatch):
    class FakeAgent:
        valid_tool_names = {"image_generate"}
        model = "main-model"

    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {})
    monkeypatch.setattr(
        "tools.image_generation_tool.get_active_image_capabilities",
        lambda: pytest.fail("disabled shadow must not resolve capabilities"),
    )
    agent = FakeAgent()
    assert _run_image_intent_shadow_router(
        agent,
        user_message="生成一张图片",
        turn_id="turn-off",
    ) is None
    assert not hasattr(agent, "_last_image_intent_shadow")


def test_conversation_loop_shadow_wiring_exposes_readback(monkeypatch, tmp_path):
    class FakeAgent:
        valid_tool_names = {"image_generate"}
        model = "main-model"

    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {
            "image_intent_routing": {
                "enabled": True,
                "mode": "shadow",
                "ledger_enabled": True,
                "ledger_path": str(tmp_path / "events.jsonl"),
            }
        },
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_active_image_capabilities",
        lambda: {
            "provider": "fake",
            "model": "fake-v2",
            "modalities": ["text", "image"],
            "max_reference_images": 3,
        },
    )
    agent = FakeAgent()
    receipt = _run_image_intent_shadow_router(
        agent,
        user_message="生成一张图片",
        turn_id="turn-on",
    )
    assert receipt["status"] == "observed"
    assert receipt["runtime_mutation_authorized"] is False
    assert agent._last_image_intent_shadow == receipt


def test_conversation_loop_shadow_failure_clears_stale_state_without_mutating_policy(monkeypatch):
    class FakeAgent:
        valid_tool_names = {"image_generate", "read_file"}
        tools = [{"name": "image_generate"}, {"name": "read_file"}]
        model = "main-model"
        provider = "main-provider"
        _last_image_intent_shadow = {"turn_id": "stale"}

    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {"image_intent_routing": {"enabled": True, "mode": "shadow"}},
    )
    monkeypatch.setattr(
        "tools.image_generation_tool.get_active_image_capabilities",
        lambda: (_ for _ in ()).throw(RuntimeError("resolver unavailable")),
    )
    agent = FakeAgent()
    before = (set(agent.valid_tool_names), list(agent.tools), agent.model, agent.provider)
    assert _run_image_intent_shadow_router(
        agent,
        user_message="生成一张图片",
        turn_id="turn-fail-soft",
    ) is None
    after = (set(agent.valid_tool_names), list(agent.tools), agent.model, agent.provider)
    assert after == before
    assert getattr(agent, "_last_image_intent_shadow", None) is None


def test_finalize_shadow_detects_negative_generation_conflict():
    receipt = run_image_intent_shadow(
        user_message="只写提示词，不要生成图片",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="turn-negative",
    )
    final = finalize_image_intent_shadow(
        receipt,
        messages=[
            {"role": "user", "content": "只写提示词，不要生成图片"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "image_generate", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ],
    )
    assert final["status"] == "finalized"
    assert final["observed_tool_names"] == ["image_generate"]
    assert final["alignment"]["status"] == "conflict"
    assert final["alignment"]["reason"] == "generation_observed_for_negative_intent"
    assert final["alignment"]["review_required"] is True
    assert final["alignment"]["production_gate"] == "block"


def test_finalize_shadow_detects_missing_expected_artifact_tool():
    receipt = run_image_intent_shadow(
        user_message="生成一张图片",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="turn-generate",
    )
    final = finalize_image_intent_shadow(
        receipt,
        messages=[
            {"role": "user", "content": "生成一张图片"},
            {"role": "assistant", "content": "这里是图片。"},
        ],
    )
    assert final["alignment"]["status"] == "conflict"
    assert final["alignment"]["reason"] == "artifact_required_but_generation_not_observed"
    assert final["alignment"]["production_gate"] == "block"


def test_finalize_shadow_blocks_contract_version_drift():
    receipt = run_image_intent_shadow(
        user_message="生成一张图片",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="turn-drift",
    )
    receipt["schema_version"] = "hermes_image_intent_contract_v0"
    final = finalize_image_intent_shadow(
        receipt,
        messages=[{"role": "user", "content": "生成一张图片"}],
    )
    assert final["alignment"]["status"] == "conflict"
    assert final["alignment"]["reason"] == "contract_version_mismatch"
    assert final["alignment"]["production_gate"] == "block"


def test_edit_contract_blocks_provider_without_image_capability():
    class TextOnlyProvider:
        name = "text-only"

        def capabilities(self):
            return {"modalities": ["text"], "max_reference_images": 0}

    route = evaluate_image_intent_contract(
        classify_image_intent_shadow("把这张图片改成水彩风格"),
        provider=build_provider_capability_contract(TextOnlyProvider()),
        image_tool_available=True,
    )
    assert route.eligible is False
    assert route.blocker == "provider_missing_image_edit"
    assert route.runtime_mutation_authorized is False


def test_finalize_shadow_keeps_ambiguous_and_aligned_turns_non_blocking():
    ambiguous = run_image_intent_shadow(
        user_message="想做个视觉方案",
        config={"image_intent_routing": {"enabled": True, "mode": "shadow"}},
        image_tool_available=True,
        provider=FakeProvider(),
        turn_id="turn-ambiguous",
    )
    final = finalize_image_intent_shadow(
        ambiguous,
        messages=[
            {"role": "user", "content": "想做个视觉方案"},
            {"role": "assistant", "content": "可以先梳理方向。"},
        ],
    )
    assert final["alignment"]["status"] == "not_applicable"
    assert final["alignment"]["review_required"] is False
    assert final["alignment"]["production_gate"] == "observe"
