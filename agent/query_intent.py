#!/usr/bin/env python3
"""
query_intent.py — Zero-cost regex intent classifier.

Ported from GBrain's query-intent.ts pattern.
Classifies queries into intent types without any LLM calls.
Drives search behavior and routing decisions.

Usage:
    from agent.query_intent import classify_intent
    intent = classify_intent("find the React component that handles auth")
    # → Intent(type='entity', detail='high', salience='high', recency='normal')
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Intent:
    """Query intent classification result."""
    type: str           # entity, temporal, event, general
    detail: str         # low, normal, high
    salience: str       # low, normal, high
    recency: str        # old, normal, recent, latest
    confidence: float   # 0.0 - 1.0
    reason: str         # human-readable explanation


# ── Pattern definitions ────────────────────────────────────────────────

_ENTITY_PATTERNS = [
    re.compile(r'\b(find|where|locate|show me|look up)\b.*\b(class|function|method|file|component|module|variable|type|interface)\b', re.I),
    re.compile(r'\b(which|what)\s+(file|class|function|module|component)\b', re.I),
    re.compile(r'\b(def|class|function|import|export|const|let|var)\s+\w+', re.I),
    re.compile(r'\b\w+\.(py|js|ts|tsx|jsx|vue|go|rs|rb|java)\b', re.I),
    re.compile(r'\b(src|lib|app|test|spec|dist)/\w+', re.I),
]

_TEMPORAL_PATTERNS = [
    re.compile(r'\b(yesterday|today|last\s+(week|month|hour|night)|recent|latest|newest)\b', re.I),
    re.compile(r'\b(before|after|since|until|ago)\s+\d', re.I),
    re.compile(r'\b(when|what time|what date|how long)\b', re.I),
    re.compile(r'\b(last|previous|next|current)\s+(commit|change|update|version|release)\b', re.I),
]

_EVENT_PATTERNS = [
    re.compile(r'\b(crash|error|fail|bug|break|exception|issue|problem)\b', re.I),
    re.compile(r'\b(what happened|what went wrong|why did|how did)\b', re.I),
    re.compile(r'\b(deploy|release|merge|push|publish|build)\b', re.I),
    re.compile(r'\b(test|spec|check|verify|validate)\b.*\b(fail|pass|result)\b', re.I),
]

_DETAIL_LOW_PATTERNS = [
    re.compile(r'^(hi|hello|hey|ok|thanks|summarize|overview|brief)\s*[.!?]?$', re.I),
    re.compile(r'\b(list|show|summarize|overview|brief|quick)\b', re.I),
]

_DETAIL_HIGH_PATTERNS = [
    re.compile(r'\b(explain|analyze|debug|trace|investigate|deep|detailed|comprehensive)\b', re.I),
    re.compile(r'\b(why|how|reason|cause|root cause|trade-?off|compare)\b', re.I),
    re.compile(r'\b(step by step|line by line|every|all|each)\b', re.I),
]

_SALIENCE_HIGH_PATTERNS = [
    re.compile(r'\b(important|critical|urgent|security|vulnerability|production|breaking)\b', re.I),
    re.compile(r'\b(must|need|should|have to|required|necessary)\b', re.I),
    re.compile(r'\b(fix|resolve|solve|patch|hotfix)\b', re.I),
]

_RECENCY_PATTERNS = {
    'latest': [
        re.compile(r'\b(latest|newest|most recent|current|head|tip)\b', re.I),
    ],
    'recent': [
        re.compile(r'\b(recent|today|this week|just|just now)\b', re.I),
    ],
    'old': [
        re.compile(r'\b(old|legacy|deprecated|historical|archive)\b', re.I),
    ],
}


def classify_intent(query: str) -> Intent:
    """Classify query intent using pure regex patterns. Zero LLM cost."""
    if not query or not query.strip():
        return Intent("general", "normal", "normal", "normal", 0.0, "empty query")

    text = query.strip()

    # Type classification
    type_scores = {
        "entity": sum(1 for p in _ENTITY_PATTERNS if p.search(text)),
        "temporal": sum(1 for p in _TEMPORAL_PATTERNS if p.search(text)),
        "event": sum(1 for p in _EVENT_PATTERNS if p.search(text)),
    }
    max_type = max(type_scores, key=type_scores.get)
    query_type = max_type if type_scores[max_type] > 0 else "general"

    # Detail level
    if any(p.search(text) for p in _DETAIL_LOW_PATTERNS):
        detail = "low"
    elif any(p.search(text) for p in _DETAIL_HIGH_PATTERNS):
        detail = "high"
    else:
        detail = "normal"

    # Salience
    salience = "high" if any(p.search(text) for p in _SALIENCE_HIGH_PATTERNS) else "normal"

    # Recency
    recency = "normal"
    for level, patterns in _RECENCY_PATTERNS.items():
        if any(p.search(text) for p in patterns):
            recency = level
            break

    # Confidence
    total_matches = sum(type_scores.values())
    confidence = min(1.0, 0.3 + total_matches * 0.2)

    # Reason
    reasons = []
    if query_type != "general":
        reasons.append(f"type={query_type}({type_scores[query_type]} matches)")
    if detail != "normal":
        reasons.append(f"detail={detail}")
    if salience != "normal":
        reasons.append(f"salience={salience}")
    if recency != "normal":
        reasons.append(f"recency={recency}")

    return Intent(
        type=query_type,
        detail=detail,
        salience=salience,
        recency=recency,
        confidence=confidence,
        reason=", ".join(reasons) if reasons else "general query",
    )


def intent_to_routing(intent: Intent) -> dict:
    """Convert intent to routing recommendations."""
    return {
        "search_detail": intent.detail,
        "boost_salience": intent.salience == "high",
        "boost_recency": intent.recency in ("latest", "recent"),
        "prefer_structural": intent.type == "entity",
        "prefer_temporal": intent.type == "temporal",
        "prefer_event": intent.type == "event",
    }
