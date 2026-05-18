from pathlib import Path

from agent.cognitive_pipeline import (
    BudgetStage,
    CognitivePipeline,
    ContextPressureStage,
    IntentStage,
    PatternRecallStage,
    PolicyStage,
    RepoAnatomyStage,
    RouteStage,
    TurnInput,
    build_default_cognitive_pipeline,
)
from agent.cognitive_state import CognitiveState, IntentState, RouteState


class IntentSeedStage:
    name = "intent"

    def run(self, turn_input, state):
        state.intent = IntentState(
            type="event",
            detail="high",
            salience="normal",
            recency="normal",
            confidence=0.7,
            reason="debug keyword",
        )
        return state


class RouteSeedStage:
    name = "route"

    def run(self, turn_input, state):
        state.route = RouteState(
            provider="openai-codex",
            model="gpt-5.5",
            tier="COMPLEX",
            score=0.8,
            reason="debugging task",
        )
        return state


class ExplodingStage:
    name = "repo_anatomy"

    def run(self, turn_input, state):
        raise RuntimeError("scanner unavailable")


class SkipStage:
    name = "pattern"

    def run(self, turn_input, state):
        raise AssertionError("should not run when disabled")


def test_cognitive_pipeline_runs_stages_in_order_and_records_observations():
    pipeline = CognitivePipeline(stages=[IntentSeedStage(), RouteSeedStage()])
    turn_input = TurnInput(user_message="debug this repo")

    state = pipeline.run(turn_input)

    assert isinstance(state, CognitiveState)
    assert state.intent is not None
    assert state.route is not None
    assert state.route.model == "gpt-5.5"
    assert set(state.observations.stage_timings_ms) == {"intent", "route"}
    assert state.observations.confidence["intent"] == 0.7
    assert state.observations.confidence["route"] == 0.8


def test_cognitive_pipeline_is_fail_soft_and_records_stage_errors():
    pipeline = CognitivePipeline(stages=[IntentSeedStage(), ExplodingStage()])

    state = pipeline.run(TurnInput(user_message="debug this repo"))

    assert state.intent is not None
    assert state.repo_context is None
    assert state.observations.skipped_stages["repo_anatomy"] == "error: scanner unavailable"
    assert "repo_anatomy" not in state.observations.confidence


def test_cognitive_pipeline_skips_disabled_stage_names():
    pipeline = CognitivePipeline(stages=[IntentSeedStage(), SkipStage()], disabled_stages={"pattern"})

    state = pipeline.run(TurnInput(user_message="debug this repo"))

    assert state.intent is not None
    assert state.pattern is None
    assert state.observations.skipped_stages["pattern"] == "disabled"


def test_turn_input_defaults_are_lightweight_and_serializable():
    turn_input = TurnInput(user_message="hello")

    payload = turn_input.to_dict()

    assert payload == {
        "user_message": "hello",
        "conversation_history": [],
        "workdir": None,
        "enabled_toolsets": [],
        "config_flags": {},
        "metadata": {},
    }


class FakeRouter:
    def route(self, text):
        return {
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "tier": "COMPLEX",
            "score": 0.81,
            "reason": f"routed:{text}",
            "activation_hints": [{"family": "memory"}],
        }


class FakeRetriever:
    def __init__(self, result=None):
        self.result = result

    def retrieve(self, query, min_score=0.3):
        return self.result


class FakeScanner:
    def __init__(self, index=None):
        self.index = index or {}

    def scan(self, workdir, force=False):
        return self.index


class FakeBudgetMeter:
    def __init__(self, afford=True, estimate=0.12, turn_limit=1.0, turn_spent=0.2):
        self.afford = afford
        self.estimate = estimate
        self.turn_limit = turn_limit
        self.turn_spent = turn_spent

    def estimate_cost(self, model, input_tokens, max_output_tokens=4096):
        return self.estimate

    def can_afford(self, model, input_tokens, max_output_tokens=4096):
        return self.afford

    def get_stats(self):
        return {
            "turn_limit": self.turn_limit,
            "turn_spent": self.turn_spent,
        }


def fake_classify_intent(_query):
    return IntentState(
        type="entity",
        detail="high",
        salience="high",
        recency="latest",
        confidence=0.91,
        reason="matched regex",
    )


def test_intent_and_route_stages_populate_state():
    pipeline = CognitivePipeline(
        stages=[
            IntentStage(classifier=fake_classify_intent),
            RouteStage(router=FakeRouter()),
        ]
    )

    state = pipeline.run(TurnInput(user_message="find latest auth module"))

    assert state.intent is not None
    assert state.intent.type == "entity"
    assert state.route is not None
    assert state.route.model == "gpt-5.5"
    assert state.route.activation_hints == [{"family": "memory"}]


def test_pattern_and_anatomy_stages_are_advisory():
    retriever = FakeRetriever(
        {
            "approach": "reuse repo triage",
            "goal": "debug repo",
            "score": 0.66,
            "outcome": "success",
        }
    )
    scanner = FakeScanner(
        {
            "agent/cognitive_pipeline.py": {"description": "pipeline", "tokens": 120},
            "tests/agent/test_cognitive_pipeline.py": {"description": "tests", "tokens": 80},
        }
    )
    pipeline = CognitivePipeline(
        stages=[
            IntentStage(classifier=fake_classify_intent),
            PatternRecallStage(retriever=retriever),
            RepoAnatomyStage(scanner=scanner, max_paths=1),
        ]
    )

    state = pipeline.run(TurnInput(user_message="debug repo", workdir="/tmp/repo"))

    assert state.pattern is not None
    assert "reuse repo triage" in state.pattern.summary
    assert state.repo_context is not None
    assert state.repo_context.top_paths == ["agent/cognitive_pipeline.py"]
    assert state.repo_context.token_estimate == 200


def test_budget_stage_emits_guidance_without_failing_turn():
    pipeline = CognitivePipeline(
        stages=[
            RouteSeedStage(),
            BudgetStage(meter=FakeBudgetMeter(afford=False, estimate=1.4, turn_limit=1.0, turn_spent=0.2)),
        ]
    )

    state = pipeline.run(TurnInput(user_message="deep debug"))

    assert state.budget is not None
    assert state.budget.allowed is False
    assert state.budget.estimated_cost_usd == 1.4
    assert state.budget.remaining_turn_budget_usd == 0.8
    assert state.budget.downgrade_hint is not None


def test_policy_and_context_pressure_stages_emit_advisory_state(tmp_path):
    def fake_policy_renderer(repo, target, max_rules_per_scope=12):
        assert repo == tmp_path
        return "Scoped Repository Policy\n- Keep tests green"

    def fake_memory_gate(source, content, evidence="", durable_days=0, contains_secret=False):
        return {"ok": True, "source": source, "content": content}

    pipeline = CognitivePipeline(
        stages=[
            RouteSeedStage(),
            PolicyStage(
                policy_renderer=fake_policy_renderer,
                memory_gate=fake_memory_gate,
            ),
            ContextPressureStage(compress_chars=40, spill_threshold=60),
        ]
    )

    turn_input = TurnInput(
        user_message="x" * 80,
        workdir=str(tmp_path),
        metadata={
            "memory_candidate": "verified workflow",
            "memory_evidence": "tests passed",
            "memory_gate_enabled": True,
        },
    )
    state = pipeline.run(turn_input)

    assert state.policy is not None
    assert "Scoped Repository Policy" in state.policy.scoped_policy_text
    assert state.policy.memory_gate["ok"] is True
    assert state.context_pressure is not None
    assert state.context_pressure.should_compress is True
    assert state.context_pressure.should_spill is True


def test_policy_stage_exposes_memory_gate_enablement_without_candidate(tmp_path):
    stage = PolicyStage(
        policy_renderer=lambda repo, target, max_rules_per_scope=12: "Scoped Repository Policy",
        memory_gate=lambda *args, **kwargs: {"ok": True},
    )
    turn_input = TurnInput(
        user_message="remember durable repo convention",
        workdir=str(tmp_path),
        metadata={"memory_gate_enabled": True},
    )

    state = stage.run(turn_input, CognitiveState())

    assert state.policy is not None
    assert state.policy.memory_gate["enabled"] is True
    assert state.policy.memory_gate.get("ok") is None


def test_build_default_cognitive_pipeline_contains_non_mainline_stages():
    pipeline = build_default_cognitive_pipeline()

    stage_names = [stage.name for stage in pipeline.stages]

    assert stage_names == [
        "intent",
        "route",
        "pattern",
        "repo_anatomy",
        "budget",
        "policy",
        "context_pressure",
    ]
