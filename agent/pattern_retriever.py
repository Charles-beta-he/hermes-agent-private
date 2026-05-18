#!/usr/bin/env python3
"""
pattern_retriever.py — RETRIEVE: Search memory for similar past tasks.

Ported from Ruflo's 4-Step Intelligence Pipeline (RETRIEVE phase).
Before executing a task, search session history for similar past tasks.
If a successful pattern is found, reuse it instead of re-deriving.

Usage:
    from agent.pattern_retriever import PatternRetriever
    retriever = PatternRetriever()
    pattern = retriever.retrieve("fix React component rendering issue")
    if pattern:
        # reuse pattern
        print(pattern['approach'])
"""

import re
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".hermes" / "cache" / "patterns"


class PatternRetriever:
    """Search memory for similar past tasks."""

    def __init__(self, cache_dir: Path = _CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._patterns: Dict[str, dict] = {}
        self._load_patterns()

    def _load_patterns(self):
        """Load stored patterns from disk."""
        pattern_file = self.cache_dir / "patterns.json"
        if pattern_file.exists():
            try:
                self._patterns = json.loads(pattern_file.read_text())
            except Exception:
                self._patterns = {}

    def _save_patterns(self):
        """Save patterns to disk."""
        pattern_file = self.cache_dir / "patterns.json"
        try:
            pattern_file.write_text(json.dumps(self._patterns, indent=2))
        except Exception as e:
            logger.debug("Pattern save failed: %s", e)

    def retrieve(self, query: str, min_score: float = 0.3) -> Optional[dict]:
        """Retrieve the best matching pattern for a query.

        Returns dict with keys: approach, outcome, score, tokens_saved
        """
        if not query or not self._patterns:
            return None

        query_tokens = self._tokenize(query)
        best_match = None
        best_score = 0.0

        for pattern_id, pattern in self._patterns.items():
            pattern_tokens = self._tokenize(pattern.get('goal', ''))
            score = self._jaccard_similarity(query_tokens, pattern_tokens)

            if score > best_score and score >= min_score:
                best_score = score
                best_match = {
                    'pattern_id': pattern_id,
                    'approach': pattern.get('approach', ''),
                    'outcome': pattern.get('outcome', 'success'),
                    'score': score,
                    'tokens_saved': pattern.get('tokens_saved', 0),
                    'goal': pattern.get('goal', ''),
                }

        if best_match:
            logger.debug(
                "Pattern retrieved: %s (score=%.2f, tokens_saved=%d)",
                best_match['pattern_id'][:20],
                best_match['score'],
                best_match['tokens_saved'],
            )

        return best_match

    def store(
        self,
        goal: str,
        approach: str,
        outcome: str = "success",
        tokens_saved: int = 0,
        metadata: dict = None,
    ):
        """Store a successful pattern for future retrieval."""
        pattern_id = f"p_{hash(goal) % 100000:05d}_{int(datetime.now().timestamp())}"
        self._patterns[pattern_id] = {
            'goal': goal,
            'approach': approach,
            'outcome': outcome,
            'tokens_saved': tokens_saved,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {},
        }
        self._save_patterns()
        logger.debug("Pattern stored: %s", pattern_id)

    def list_patterns(self, limit: int = 20) -> list:
        """List stored patterns."""
        patterns = sorted(
            self._patterns.values(),
            key=lambda p: p.get('timestamp', ''),
            reverse=True,
        )
        return patterns[:limit]

    # ── Internal helpers ───────────────────────────────────────────────

    def _tokenize(self, text: str) -> set:
        """Tokenize text into lowercase words."""
        return set(re.findall(r'[a-z][a-z0-9_-]{2,}', text.lower()))

    def _jaccard_similarity(self, set1: set, set2: set) -> float:
        """Compute Jaccard similarity between two sets."""
        if not set1 or not set2:
            return 0.0
        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union) if union else 0.0
