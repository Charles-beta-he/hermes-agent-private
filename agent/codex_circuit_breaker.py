"""Per-(provider, model) circuit breaker for Codex Responses streaming.

Trips after N consecutive transport failures, stays open for a cooldown, then
half-opens on the next call. Process-local; survives nothing across restarts.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple


class CodexCircuitOpen(Exception):
    """Raised when the breaker is open and a new request is rejected."""


class CodexBreaker:
    _registry: Dict[Tuple[str, str], "CodexBreaker"] = {}
    _registry_lock = threading.Lock()

    FAILURE_THRESHOLD = 5
    COOLDOWN_SECONDS = 30.0

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._last_exc: Optional[BaseException] = None

    @classmethod
    def for_key(cls, provider: Optional[str], model: Optional[str]) -> "CodexBreaker":
        key = (provider or "", model or "")
        with cls._registry_lock:
            inst = cls._registry.get(key)
            if inst is None:
                inst = cls(provider or "", model or "")
                cls._registry[key] = inst
            return inst

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if (time.monotonic() - self._opened_at) >= self.COOLDOWN_SECONDS:
                # Half-open: clear state so the next call gets one trial.
                self._opened_at = None
                self._failures = 0
                self._last_exc = None
                return False
            return True

    def open_reason(self) -> str:
        with self._lock:
            exc_repr = repr(self._last_exc) if self._last_exc is not None else "no recent exception"
            remaining = 0.0
            if self._opened_at is not None:
                remaining = max(0.0, self.COOLDOWN_SECONDS - (time.monotonic() - self._opened_at))
            return (
                f"Codex circuit open for provider={self.provider!r} model={self.model!r} "
                f"after {self._failures} consecutive transport failures; "
                f"cooldown ~{remaining:.1f}s remaining; last_error={exc_repr}"
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._last_exc = None

    def record_failure(self, exc: BaseException) -> None:
        with self._lock:
            self._failures += 1
            self._last_exc = exc
            if self._failures >= self.FAILURE_THRESHOLD and self._opened_at is None:
                self._opened_at = time.monotonic()


__all__ = ["CodexBreaker", "CodexCircuitOpen"]
