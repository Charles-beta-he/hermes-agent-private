from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Set

from agent.anatomy_scanner import AnatomyScanner
from agent.budget_meter import BudgetMeter
from agent.cognitive_state import (
    BudgetState,
    CognitiveState,
    ContextPressureState,
    IntentState,
    PatternState,
    PolicyState,
    RepoContextState,
    RouteState,
)
from agent.openclaw_control_plane import load_experimental_config, memory_write_gate, render_scoped_policy_context
from agent.pattern_retriever import PatternRetriever
from agent.query_intent import classify_intent
from agent.result_spiller import ResultSpiller
from agent.task_router import TaskRouter


class CognitiveStage(Protocol):
    name: str

    def run(self, turn_input: "TurnInput", state: CognitiveState) -> CognitiveState:
        ...


@dataclass
class TurnInput:
    user_message: str
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    workdir: Optional[str] = None
    enabled_toolsets: List[str] = field(default_factory=list)
    config_flags: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_message": self.user_message,
            "conversation_history": list(self.conversation_history),
            "workdir": self.workdir,
            "enabled_toolsets": list(self.enabled_toolsets),
            "config_flags": dict(self.config_flags),
            "metadata": dict(self.metadata),
        }


@dataclass
class IntentStage:
    classifier: Callable[[str], Any] = classify_intent
    name: str = "intent"

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        if not turn_input.user_message.strip():
            state.observations.skipped_stages[self.name] = "empty_message"
            return state
        result = self.classifier(turn_input.user_message)
        state.intent = IntentState(
            type=getattr(result, "type", "general"),
            detail=getattr(result, "detail", "normal"),
            salience=getattr(result, "salience", "normal"),
            recency=getattr(result, "recency", "normal"),
            confidence=float(getattr(result, "confidence", 0.0)),
            reason=getattr(result, "reason", ""),
        )
        return state


@dataclass
class RouteStage:
    router: Any = field(default_factory=lambda: TaskRouter())
    name: str = "route"

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        routed = self.router.route(turn_input.user_message)
        state.route = RouteState(
            provider=routed.get("provider", "openai-codex"),
            model=routed.get("model", "gpt-4o"),
            tier=routed.get("tier", "MEDIUM"),
            score=float(routed.get("score", 0.0)),
            reason=routed.get("reason", ""),
            activation_hints=list(routed.get("activation_hints", []) or []),
        )
        return state


@dataclass
class PatternRecallStage:
    retriever: Any = field(default_factory=PatternRetriever)
    min_score: float = 0.3
    max_summary_chars: int = 240
    name: str = "pattern"

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        pattern = self.retriever.retrieve(turn_input.user_message, min_score=self.min_score)
        if not pattern:
            state.observations.skipped_stages[self.name] = "no_match"
            return state
        summary = str(pattern.get("approach") or pattern.get("goal") or "").strip()
        if len(summary) > self.max_summary_chars:
            summary = summary[: self.max_summary_chars - 3] + "..."
        state.pattern = PatternState(
            summary=summary,
            source=str(pattern.get("pattern_id") or pattern.get("source") or "pattern_cache"),
            confidence=float(pattern.get("score", 0.0)),
        )
        return state


@dataclass
class RepoAnatomyStage:
    scanner: Any = field(default_factory=AnatomyScanner)
    max_paths: int = 5
    name: str = "repo_anatomy"

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        if not turn_input.workdir:
            state.observations.skipped_stages[self.name] = "no_workdir"
            return state
        if not _looks_repo_related(turn_input.user_message, state.intent):
            state.observations.skipped_stages[self.name] = "not_repo_related"
            return state
        index = self.scanner.scan(turn_input.workdir)
        if not index:
            state.observations.skipped_stages[self.name] = "empty_index"
            return state
        top_items = sorted(index.items(), key=lambda item: item[1].get("tokens", 0), reverse=True)
        selected = top_items[: self.max_paths]
        top_paths = [path for path, _ in selected]
        summary = ", ".join(top_paths)
        token_estimate = sum(item.get("tokens", 0) for _, item in top_items)
        state.repo_context = RepoContextState(
            summary=summary,
            top_paths=top_paths,
            token_estimate=token_estimate,
        )
        return state


@dataclass
class BudgetStage:
    meter: Any = field(default_factory=BudgetMeter)
    max_output_tokens: int = 4096
    fallback_model: str = "gpt-4o-mini"
    name: str = "budget"

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        model = state.route.model if state.route else self.fallback_model
        input_tokens = max(1, _estimate_tokens(turn_input))
        estimated = float(self.meter.estimate_cost(model, input_tokens, self.max_output_tokens))
        allowed = bool(self.meter.can_afford(model, input_tokens, self.max_output_tokens))
        stats = self.meter.get_stats() if hasattr(self.meter, "get_stats") else {}
        remaining = None
        if isinstance(stats, dict) and "turn_limit" in stats and "turn_spent" in stats:
            remaining = round(float(stats["turn_limit"]) - float(stats["turn_spent"]), 4)
        state.budget = BudgetState(
            allowed=allowed,
            estimated_cost_usd=round(estimated, 4),
            remaining_turn_budget_usd=remaining,
            downgrade_hint=None if allowed else f"downgrade model/tier (e.g. {self.fallback_model})",
        )
        return state


@dataclass
class PolicyStage:
    policy_renderer: Callable[..., str] = render_scoped_policy_context
    memory_gate: Callable[..., Dict[str, Any]] = memory_write_gate
    name: str = "policy"

    @staticmethod
    def _memory_gate_enabled(turn_input: TurnInput) -> bool:
        if "memory_gate_enabled" in turn_input.metadata:
            return bool(turn_input.metadata.get("memory_gate_enabled"))
        try:
            cfg = load_experimental_config() or {}
        except Exception:
            return False
        ocp = cfg.get("openclaw_control_plane") or {}
        return bool(ocp.get("memory_write_gate", False))

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        scoped_policy_text = None
        if turn_input.workdir:
            repo = Path(turn_input.workdir)
            scoped_policy_text = self.policy_renderer(repo, repo)
        memory_candidate = turn_input.metadata.get("memory_candidate", "")
        memory_evidence = turn_input.metadata.get("memory_evidence", "")
        memory_gate_enabled = self._memory_gate_enabled(turn_input)
        gate_result: Dict[str, Any] = {"enabled": memory_gate_enabled}
        if memory_candidate and memory_gate_enabled:
            preview = self.memory_gate(
                "procedural_skills",
                memory_candidate,
                evidence=memory_evidence,
                durable_days=int(turn_input.metadata.get("memory_durable_days", 0) or 0),
                contains_secret=bool(turn_input.metadata.get("memory_contains_secret", False)),
            )
            if isinstance(preview, dict):
                gate_result.update(preview)
        state.policy = PolicyState(
            scoped_policy_text=scoped_policy_text,
            memory_gate=gate_result,
            activation_hints=list(state.route.activation_hints if state.route else []),
        )
        return state


@dataclass
class ContextPressureStage:
    compress_chars: int = 12000
    spill_threshold: Optional[int] = None
    spiller: Any = field(default_factory=ResultSpiller)
    name: str = "context_pressure"

    def __post_init__(self) -> None:
        if self.spill_threshold is None:
            self.spill_threshold = int(getattr(self.spiller, "threshold", 2000))

    def run(self, turn_input: TurnInput, state: CognitiveState) -> CognitiveState:
        total_chars = _estimate_chars(turn_input)
        should_compress = total_chars >= self.compress_chars
        should_spill = total_chars >= int(self.spill_threshold or 0)
        reason_parts = []
        if should_compress:
            reason_parts.append(f"chars>={self.compress_chars}")
        if should_spill:
            reason_parts.append(f"chars>={self.spill_threshold}")
        state.context_pressure = ContextPressureState(
            should_compress=should_compress,
            should_spill=should_spill,
            reason=", ".join(reason_parts) or "below_threshold",
            compression_target="conversation_history" if should_compress else None,
        )
        return state


class CognitivePipeline:
    def __init__(
        self,
        stages: Optional[Sequence[CognitiveStage]] = None,
        disabled_stages: Optional[Set[str]] = None,
    ):
        self.stages = list(stages or [])
        self.disabled_stages = set(disabled_stages or set())

    def run(self, turn_input: TurnInput, initial_state: Optional[CognitiveState] = None) -> CognitiveState:
        state = initial_state or CognitiveState()
        for stage in self.stages:
            stage_name = getattr(stage, "name", stage.__class__.__name__)
            if stage_name in self.disabled_stages:
                state.observations.skipped_stages[stage_name] = "disabled"
                continue

            started = time.perf_counter()
            try:
                next_state = stage.run(turn_input, state)
                if next_state is not None:
                    state = next_state
            except Exception as exc:
                state.observations.skipped_stages[stage_name] = f"error: {exc}"
                continue
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                state.observations.stage_timings_ms[stage_name] = round(elapsed_ms, 3)

            _record_confidence(stage_name, state)

        return state


def build_default_cognitive_pipeline(disabled_stages: Optional[Set[str]] = None) -> CognitivePipeline:
    return CognitivePipeline(
        stages=[
            IntentStage(),
            RouteStage(),
            PatternRecallStage(),
            RepoAnatomyStage(),
            BudgetStage(),
            PolicyStage(),
            ContextPressureStage(),
        ],
        disabled_stages=disabled_stages,
    )


def _record_confidence(stage_name: str, state: CognitiveState) -> None:
    state_attr_map = {
        "intent": "intent",
        "route": "route",
        "pattern": "pattern",
        "repo_anatomy": "repo_context",
        "budget": "budget",
        "policy": "policy",
        "context_pressure": "context_pressure",
    }
    stage_state = getattr(state, state_attr_map.get(stage_name, stage_name), None)
    if stage_state is None:
        return
    confidence = getattr(stage_state, "confidence", None)
    if confidence is None:
        confidence = getattr(stage_state, "score", None)
    if confidence is None:
        return
    try:
        state.observations.confidence[stage_name] = float(confidence)
    except (TypeError, ValueError):
        return


def _estimate_tokens(turn_input: TurnInput) -> int:
    return max(1, _estimate_chars(turn_input) // 4)


def _estimate_chars(turn_input: TurnInput) -> int:
    history_chars = sum(len(str(item)) for item in turn_input.conversation_history)
    metadata_chars = sum(len(str(v)) for v in turn_input.metadata.values())
    return len(turn_input.user_message) + history_chars + metadata_chars


def _looks_repo_related(user_message: str, intent: Optional[IntentState]) -> bool:
    text = user_message.lower()
    if any(token in text for token in ("repo", "repository", "code", "module", "file", "project", "debug")):
        return True
    return bool(intent and intent.type == "entity")
