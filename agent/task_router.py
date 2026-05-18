#!/usr/bin/env python3
"""
task_router.py — Complexity-based model routing for Hermes Agent.

Ported from LiteLLM's complexity_router strategy.
Scores prompts across 7 dimensions, maps to model tiers.
Zero API calls, <1ms per classification.

Usage:
    from agent.task_router import TaskRouter
    router = TaskRouter(config)
    tier, score = router.classify("fix the React component")
    # → ("COMPLEX", 0.42)
    result = router.route("what time is it?")
    # → {"provider": "openai-codex", "model": "gpt-4o-mini", "tier": "SIMPLE", "score": 0.08}
"""

import re
import logging
from typing import Dict, Tuple, Optional, Any

try:
    from .openclaw_control_plane import activation_hints_for_text, openclaw_activation_hints_enabled
except Exception:  # pragma: no cover - optional local control-plane helper
    activation_hints_for_text = None
    openclaw_activation_hints_enabled = None

logger = logging.getLogger(__name__)

# ── Default configuration ──────────────────────────────────────────────

DEFAULT_TIERS = {
    "SIMPLE": {"provider": "openai-codex", "model": "gpt-4o-mini"},
    "MEDIUM": {"provider": "openai-codex", "model": "gpt-4o"},
    "COMPLEX": {"provider": "openai-codex", "model": "gpt-5.4"},
    "REASONING": {"provider": "anthropic", "model": "claude-sonnet-4"},
}

DEFAULT_BOUNDARIES = {
    "simple_medium": 0.15,
    "medium_complex": 0.35,
    "complex_reasoning": 0.60,
}

DEFAULT_WEIGHTS = {
    "token_count": 0.08,
    "code_presence": 0.23,
    "reasoning_markers": 0.19,
    "technical_terms": 0.19,
    "simple_indicators": 0.04,
    "multi_step_patterns": 0.12,
    "question_complexity": 0.15,
}

# ── Pattern definitions ────────────────────────────────────────────────

_CODE_PATTERNS = [
    re.compile(r'```'),
    re.compile(r'\b(def|class|import|from|function|const|let|var|async|await)\b'),
    re.compile(r'[{}\[\]();]'),
    re.compile(r'\b(return|if|else|for|while|try|catch|throw)\b'),
    re.compile(r'\.(py|js|ts|jsx|tsx|java|go|rs|rb|php|css|html|sql|sh)\b'),
]

_REASONING_MARKERS = [
    re.compile(r'\b(analyze|explain|why|how|compare|contrast|evaluate|assess)\b', re.I),
    re.compile(r'\b(pros?\s+and\s+cons?|trade-?offs?|implications?|consequences?)\b', re.I),
    re.compile(r'\b(design|architect|plan|strategy|approach|methodology)\b', re.I),
    re.compile(r'\b(optimize|improve|refactor|migrate|scale)\b', re.I),
    re.compile(r'\b(debug|diagnose|investigate|troubleshoot|root\s*cause)\b', re.I),
]

_TECHNICAL_TERMS = [
    re.compile(r'\b(api|database|deploy|config|docker|kubernetes|ci[/-]?cd)\b', re.I),
    re.compile(r'\b(git|github|commit|branch|merge|pull\s*request|pr)\b', re.I),
    re.compile(r'\b(server|client|endpoint|route|middleware|auth|token)\b', re.I),
    re.compile(r'\b(test|spec|benchmark|performance|latency|throughput)\b', re.I),
    re.compile(r'\b(security|encrypt|hash|certificate|ssl|cors)\b', re.I),
    re.compile(r'\b(railway|vercel|aws|azure|gcp|cloudflare|heroku)\b', re.I),
    re.compile(r'\b(react|vue|angular|next|nuxt|svelte|node|express|fastapi)\b', re.I),
    re.compile(r'\b(architecture|migration|refactor|scale|optimize|infrastructure)\b', re.I),
    re.compile(r'\b(database|schema|migration|index|query|transaction|cache)\b', re.I),
    re.compile(r'\b(component|module|service|microservice|monolith|pipeline)\b', re.I),
]

_SIMPLE_INDICATORS = [
    re.compile(r'^(hi|hello|hey|ok|thanks|thank you|yes|no|sure|bye)\s*[.!?]?$', re.I),
    re.compile(r'^(what time|what date|what day)\b', re.I),
    re.compile(r'^(list|show|tell me|give me)\s+(me\s+)?(the\s+)?\w+\s*$', re.I),
]

_MULTI_STEP_PATTERNS = [
    re.compile(r'\b(first|then|next|finally|step\s*\d)\b', re.I),
    re.compile(r'\b(\d+[\.\)]\s+\w)'),  # numbered lists
    re.compile(r'\b(after|before|once|when)\b.*\b(then|do|run|execute)\b', re.I),
    re.compile(r'\b(all|every|each|both)\b.*\b(and|plus|also|additionally)\b', re.I),
]


class TaskRouter:
    """Complexity-based model router. Zero API calls, <1ms per classify."""

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.config = cfg
        self.enabled = cfg.get("enabled", True)
        self.strategy = cfg.get("strategy", "complexity")
        self.tiers = cfg.get("tiers", DEFAULT_TIERS)
        self.boundaries = cfg.get("boundaries", DEFAULT_BOUNDARIES)
        self.weights = cfg.get("weights", DEFAULT_WEIGHTS)
        self._stats = {"total": 0, "by_tier": {}}

    def classify(self, text: str) -> Tuple[str, float]:
        """Classify text complexity. Returns (tier, score)."""
        if not text or not text.strip():
            return "SIMPLE", 0.0

        score = self._compute_score(text)
        tier = self._score_to_tier(score)
        return tier, score

    def route(self, text: str) -> dict:
        """Route text to optimal model. Returns {provider, model, tier, score, reason}."""
        tier, score = self.classify(text)
        model_cfg = self.tiers.get(tier, self.tiers.get("MEDIUM"))

        self._stats["total"] += 1
        self._stats["by_tier"][tier] = self._stats["by_tier"].get(tier, 0) + 1

        result = {
            "provider": model_cfg.get("provider", "openai-codex"),
            "model": model_cfg.get("model", "gpt-4o"),
            "tier": tier,
            "score": round(score, 3),
            "reason": self._explain_score(text, score),
        }
        # Optional OpenClaw-informed control-plane hinting. Off by default and
        # fail-soft: this never changes routing, it only exposes manifest-first
        # capability hints when config explicitly enables it.
        if activation_hints_for_text and openclaw_activation_hints_enabled and openclaw_activation_hints_enabled(self.config):
            try:
                result["activation_hints"] = activation_hints_for_text(text)
            except Exception as exc:  # pragma: no cover - advisory only
                logger.debug("openclaw activation hints unavailable: %s", exc)
        return result

    def route_delegation(self, task_goal: str, task_context: str = "") -> dict:
        """Route a delegation task. Combines goal + context for scoring."""
        combined = task_goal
        if task_context:
            # Only take first 500 chars of context to avoid overweighting
            combined = task_goal + "\n" + task_context[:500]
        return self.route(combined)

    def get_stats(self) -> dict:
        """Return routing statistics."""
        return dict(self._stats)

    # ── Internal scoring ───────────────────────────────────────────────

    def _compute_score(self, text: str) -> float:
        """Compute complexity score (0.0-1.0) across 7 dimensions."""
        w = self.weights
        scores = {}

        scores["token_count"] = self._score_token_count(text)
        scores["code_presence"] = self._score_code_presence(text)
        scores["reasoning_markers"] = self._score_patterns(text, _REASONING_MARKERS)
        scores["technical_terms"] = self._score_patterns(text, _TECHNICAL_TERMS)
        scores["simple_indicators"] = self._score_simple(text)
        scores["multi_step_patterns"] = self._score_patterns(text, _MULTI_STEP_PATTERNS)
        scores["question_complexity"] = self._score_question_complexity(text)

        # Weighted sum
        total = sum(scores[k] * w.get(k, 0) for k in scores)

        # Normalize to 0-1
        return min(1.0, max(0.0, total))

    def _score_token_count(self, text: str) -> float:
        """Score based on text length. Shorter = simpler."""
        tokens = len(text.split())
        if tokens < 10:
            return 0.0
        elif tokens < 30:
            return 0.2
        elif tokens < 80:
            return 0.5
        elif tokens < 200:
            return 0.7
        else:
            return 0.9

    def _score_code_presence(self, text: str) -> float:
        """Score code indicators. More code = more complex."""
        matches = sum(1 for p in _CODE_PATTERNS if p.search(text))
        return min(1.0, matches * 0.2)

    def _score_patterns(self, text: str, patterns: list) -> float:
        """Score pattern matches. More matches = higher score."""
        matches = sum(1 for p in patterns if p.search(text))
        return min(1.0, matches * 0.25)

    def _score_simple(self, text: str) -> float:
        """Score simple indicators. Simple text = lower complexity."""
        stripped = text.strip()
        for p in _SIMPLE_INDICATORS:
            if p.match(stripped):
                return 1.0  # High simple score = lowers overall complexity
        return 0.0

    def _score_question_complexity(self, text: str) -> float:
        """Score question complexity by length and sub-questions."""
        questions = text.count('?')
        if questions == 0:
            return 0.3  # Statement, moderate
        elif questions == 1:
            return 0.2  # Single question
        else:
            return min(1.0, 0.3 + questions * 0.15)  # Multiple questions

    def _score_to_tier(self, score: float) -> str:
        """Map score to tier using boundaries."""
        b = self.boundaries
        if score < b.get("simple_medium", 0.15):
            return "SIMPLE"
        elif score < b.get("medium_complex", 0.35):
            return "MEDIUM"
        elif score < b.get("complex_reasoning", 0.60):
            return "COMPLEX"
        else:
            return "REASONING"

    def _explain_score(self, text: str, score: float) -> str:
        """Generate human-readable explanation of routing decision."""
        tier = self._score_to_tier(score)
        reasons = []

        if self._score_code_presence(text) > 0.3:
            reasons.append("code")
        if self._score_patterns(text, _REASONING_MARKERS) > 0.3:
            reasons.append("reasoning")
        if self._score_patterns(text, _TECHNICAL_TERMS) > 0.3:
            reasons.append("technical")
        if self._score_patterns(text, _MULTI_STEP_PATTERNS) > 0.3:
            reasons.append("multi-step")
        if self._score_simple(text) > 0.5:
            reasons.append("simple")

        reason_str = ", ".join(reasons) if reasons else "general"
        return f"{tier} ({score:.2f}) [{reason_str}]"
