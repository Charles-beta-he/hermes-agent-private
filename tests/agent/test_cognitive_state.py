from agent.cognitive_state import (
    BudgetState,
    CognitiveState,
    ContextPressureState,
    IntentState,
    ObservationsState,
    PatternState,
    PolicyState,
    RepoContextState,
    RouteState,
)


def test_cognitive_state_defaults_are_additive_and_serializable():
    state = CognitiveState()

    assert state.intent is None
    assert state.route is None
    assert state.pattern is None
    assert state.repo_context is None
    assert state.budget is None
    assert state.policy is None
    assert state.context_pressure is None
    assert state.observations.stage_timings_ms == {}
    assert state.observations.skipped_stages == {}
    assert state.observations.confidence == {}

    payload = state.to_dict()

    assert payload["intent"] is None
    assert payload["route"] is None
    assert payload["observations"] == {
        "stage_timings_ms": {},
        "skipped_stages": {},
        "confidence": {},
    }


def test_cognitive_state_serializes_nested_stage_outputs():
    state = CognitiveState(
        intent=IntentState(
            type="entity",
            detail="high",
            salience="high",
            recency="latest",
            confidence=0.9,
            reason="matched entity pattern",
        ),
        route=RouteState(
            provider="openai-codex",
            model="gpt-5.5",
            tier="COMPLEX",
            score=0.72,
            reason="reasoning markers",
            activation_hints=[{"family": "memory", "capability": "write"}],
        ),
        pattern=PatternState(summary="reuse last successful repo triage", source="session_recall", confidence=0.6),
        repo_context=RepoContextState(summary="agent/, tests/", top_paths=["agent", "tests"], token_estimate=1200),
        budget=BudgetState(allowed=True, estimated_cost_usd=0.15, remaining_turn_budget_usd=1.85, downgrade_hint=None),
        policy=PolicyState(scoped_policy_text="Scoped Repository Policy", memory_gate={"ok": True}, activation_hints=[{"family": "tool"}]),
        context_pressure=ContextPressureState(should_compress=True, should_spill=False, reason="large history", compression_target="tool_schema"),
        observations=ObservationsState(
            stage_timings_ms={"intent": 1.2, "route": 2.5},
            skipped_stages={"repo_anatomy": "no repo context"},
            confidence={"intent": 0.9, "route": 0.72},
        ),
    )

    payload = state.to_dict()

    assert payload["intent"]["type"] == "entity"
    assert payload["route"]["activation_hints"][0]["family"] == "memory"
    assert payload["pattern"]["source"] == "session_recall"
    assert payload["repo_context"]["top_paths"] == ["agent", "tests"]
    assert payload["budget"]["estimated_cost_usd"] == 0.15
    assert payload["policy"]["memory_gate"]["ok"] is True
    assert payload["context_pressure"]["compression_target"] == "tool_schema"
    assert payload["observations"]["skipped_stages"]["repo_anatomy"] == "no repo context"
