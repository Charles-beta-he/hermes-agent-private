#!/usr/bin/env python3
"""
budget_meter.py — Per-session cost budget gate.

Ported from GBrain's budget-meter.ts pattern.
Estimates max cost before each API call, denies if over budget.

Usage:
    from agent.budget_meter import BudgetMeter
    meter = BudgetMeter(daily_limit_usd=5.0, per_turn_limit_usd=0.50)
    if meter.can_afford(model, input_tokens, max_output_tokens):
        # proceed with API call
        meter.record(model, input_tokens, output_tokens)
    else:
        # over budget, degrade or skip
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Model pricing ($/M tokens)
MODEL_PRICING = {
    # OpenAI
    "gpt-5.5": {"input": 2.50, "output": 10.00},
    "gpt-5.4": {"input": 2.50, "output": 10.00},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # Anthropic
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    # DeepSeek
    "deepseek-v4-pro": {"input": 0.27, "output": 1.10},
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    # Xiaomi
    "mimo-v2.5-pro": {"input": 0.07, "output": 0.28},
    # Fallback
    "default": {"input": 2.50, "output": 10.00},
}


class BudgetMeter:
    """Per-session and per-turn cost budget gate."""

    def __init__(
        self,
        daily_limit_usd: float = 10.0,
        per_turn_limit_usd: float = 2.0,
        per_call_limit_usd: float = 1.0,
    ):
        self.daily_limit = daily_limit_usd
        self.per_turn_limit = per_turn_limit_usd
        self.per_call_limit = per_call_limit_usd
        self._daily_spent: float = 0.0
        self._turn_spent: float = 0.0
        self._daily_reset: datetime = datetime.now()
        self._history: list = []

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        max_output_tokens: int = 4096,
    ) -> float:
        """Estimate max cost for a single API call."""
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (max_output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    def can_afford(
        self,
        model: str,
        input_tokens: int,
        max_output_tokens: int = 4096,
    ) -> bool:
        """Check if we can afford this API call."""
        self._maybe_reset_daily()

        estimated = self.estimate_cost(model, input_tokens, max_output_tokens)

        # Per-call limit
        if estimated > self.per_call_limit:
            logger.warning(
                "Budget: per-call estimate $%.4f > limit $%.2f",
                estimated, self.per_call_limit,
            )
            return False

        # Per-turn limit
        if self._turn_spent + estimated > self.per_turn_limit:
            logger.warning(
                "Budget: turn spent $%.4f + estimate $%.4f > limit $%.2f",
                self._turn_spent, estimated, self.per_turn_limit,
            )
            return False

        # Daily limit
        if self._daily_spent + estimated > self.daily_limit:
            logger.warning(
                "Budget: daily spent $%.4f + estimate $%.4f > limit $%.2f",
                self._daily_spent, estimated, self.daily_limit,
            )
            return False

        return True

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ):
        """Record actual cost after API call."""
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
        actual = (input_tokens / 1_000_000) * pricing["input"] + \
                 (output_tokens / 1_000_000) * pricing["output"]
        self._daily_spent += actual
        self._turn_spent += actual
        self._history.append({
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": actual,
            "timestamp": datetime.now().isoformat(),
        })

    def start_turn(self):
        """Reset per-turn counter."""
        self._turn_spent = 0.0

    def get_stats(self) -> dict:
        """Return budget statistics."""
        self._maybe_reset_daily()
        return {
            "daily_spent": round(self._daily_spent, 4),
            "daily_limit": self.daily_limit,
            "daily_remaining": round(self.daily_limit - self._daily_spent, 4),
            "turn_spent": round(self._turn_spent, 4),
            "turn_limit": self.per_turn_limit,
            "calls_today": len(self._history),
        }

    def _maybe_reset_daily(self):
        """Reset daily counter at midnight."""
        now = datetime.now()
        if now.date() > self._daily_reset.date():
            self._daily_spent = 0.0
            self._daily_reset = now
            self._history.clear()
