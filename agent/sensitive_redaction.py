"""Best-effort redaction for secrets before persistence or derived projections.

This module is intentionally small and dependency-free so it can be used from
core state storage, prompt/compression paths, and local projection scripts.
It is not a credential validator; it prevents high-confidence access tokens from
being written into long-lived transcripts, FTS indexes, and ContextCard caches.
"""
from __future__ import annotations

import re
from typing import Any

REDACTION_PLACEHOLDER = "[REDACTED_SECRET]"

# Keep patterns high-confidence: avoid redacting ordinary prose while covering
# tokens users paste into chat. Add provider-specific patterns here as incidents
# prove them. The AQ.* Stitch-style token is intentionally first.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AQ\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*\b", re.IGNORECASE),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*(['\"]?)[A-Za-z0-9._~+/-]{16,}\2"
    ),
)


def redact_sensitive_text(text: str) -> str:
    """Return *text* with high-confidence secrets replaced by a placeholder."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: _redact_match(match), redacted)
    return redacted


def _redact_match(match: re.Match[str]) -> str:
    value = match.group(0)
    # Preserve the key label for key=value forms so downstream summaries retain
    # useful structure without the credential itself.
    if ":" in value or "=" in value:
        sep = ":" if ":" in value and ("=" not in value or value.index(":") < value.index("=")) else "="
        label = value.split(sep, 1)[0].strip()
        return f"{label}{sep} {REDACTION_PLACEHOLDER}"
    if value.lower().startswith("bearer "):
        return "Bearer " + REDACTION_PLACEHOLDER
    return REDACTION_PLACEHOLDER


def redact_sensitive_value(value: Any) -> Any:
    """Recursively redact secrets from JSON-like values while preserving shape."""
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_sensitive_value(item) for key, item in value.items()}
    return value


def contains_unredacted_secret(value: Any) -> bool:
    """True when *value* still contains a high-confidence secret pattern."""
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    if isinstance(value, dict):
        return any(contains_unredacted_secret(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_unredacted_secret(v) for v in value)
    return False
