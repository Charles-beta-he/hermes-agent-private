from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IntentState:
    type: str
    detail: str
    salience: str
    recency: str
    confidence: float
    reason: str


@dataclass
class RouteState:
    provider: str
    model: str
    tier: str
    score: float
    reason: str
    activation_hints: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PatternState:
    summary: str
    source: str
    confidence: float


@dataclass
class RepoContextState:
    summary: str
    top_paths: List[str] = field(default_factory=list)
    token_estimate: Optional[int] = None


@dataclass
class BudgetState:
    allowed: bool
    estimated_cost_usd: float
    remaining_turn_budget_usd: Optional[float] = None
    downgrade_hint: Optional[str] = None


@dataclass
class PolicyState:
    scoped_policy_text: Optional[str] = None
    memory_gate: Optional[Dict[str, Any]] = None
    activation_hints: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ContextPressureState:
    should_compress: bool = False
    should_spill: bool = False
    reason: Optional[str] = None
    compression_target: Optional[str] = None


@dataclass
class ObservationsState:
    stage_timings_ms: Dict[str, float] = field(default_factory=dict)
    skipped_stages: Dict[str, str] = field(default_factory=dict)
    confidence: Dict[str, float] = field(default_factory=dict)


@dataclass
class CognitiveState:
    intent: Optional[IntentState] = None
    route: Optional[RouteState] = None
    pattern: Optional[PatternState] = None
    repo_context: Optional[RepoContextState] = None
    budget: Optional[BudgetState] = None
    policy: Optional[PolicyState] = None
    context_pressure: Optional[ContextPressureState] = None
    observations: ObservationsState = field(default_factory=ObservationsState)

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


def _serialize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__") and not isinstance(value, type):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value
