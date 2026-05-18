#!/usr/bin/env python3
"""
deterministic_bypass.py — Tier 0: Skip LLM for deterministic tasks.

Ported from Ruflo's Agent Booster pattern.
For simple, rule-based transformations, skip the LLM entirely
and execute deterministically. $0 cost, <1ms latency.

Usage:
    from agent.deterministic_bypass import try_deterministic
    result = try_deterministic("convert all var to const in this code", code)
    if result:
        # deterministic transform succeeded, skip LLM
        return result
    else:
        # not deterministic, fall through to LLM
        pass
"""

import re
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BypassResult:
    """Result of a deterministic bypass."""
    success: bool
    output: str
    transform: str
    confidence: float
    tokens_saved: int  # estimated tokens saved by skipping LLM


# ── Deterministic transforms ──────────────────────────────────────────

def _var_to_const(code: str) -> Optional[BypassResult]:
    """Convert 'var' declarations to 'const' where safe."""
    lines = code.split('\n')
    changed = 0
    new_lines = []
    for line in lines:
        # Only convert 'var' that aren't reassigned
        if re.match(r'^\s*var\s+', line) and '=' in line:
            new_line = re.sub(r'\bvar\b', 'const', line, count=1)
            if new_line != line:
                changed += 1
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if changed == 0:
        return None

    return BypassResult(
        success=True,
        output='\n'.join(new_lines),
        transform='var-to-const',
        confidence=0.9,
        tokens_saved=500,
    )


def _add_semicolons(code: str) -> Optional[BypassResult]:
    """Add missing semicolons to JS/TS code."""
    lines = code.split('\n')
    changed = 0
    new_lines = []
    for line in lines:
        stripped = line.rstrip()
        if (stripped and
            not stripped.endswith((';', '{', '}', ',', '(', ')', ':', '//', '/*', '*')) and
            not stripped.startswith(('if', 'else', 'for', 'while', 'switch', 'try', 'catch', 'finally', 'class', 'function', 'import', 'export', '//', '/*', '*'))):
            new_lines.append(stripped + ';')
            changed += 1
        else:
            new_lines.append(line)

    if changed == 0:
        return None

    return BypassResult(
        success=True,
        output='\n'.join(new_lines),
        transform='add-semicolons',
        confidence=0.7,
        tokens_saved=300,
    )


def _remove_console_logs(code: str) -> Optional[BypassResult]:
    """Remove console.log statements."""
    lines = code.split('\n')
    new_lines = [l for l in lines if not re.match(r'^\s*console\.(log|debug|info|warn|error)\(', l)]
    removed = len(lines) - len(new_lines)

    if removed == 0:
        return None

    return BypassResult(
        success=True,
        output='\n'.join(new_lines),
        transform='remove-console',
        confidence=0.95,
        tokens_saved=200,
    )


def _sort_imports(code: str) -> Optional[BypassResult]:
    """Sort import statements alphabetically."""
    lines = code.split('\n')
    import_block = []
    other_lines = []
    in_imports = False

    for line in lines:
        if re.match(r'^import\s+', line) or re.match(r'^from\s+\S+\s+import', line):
            import_block.append(line)
            in_imports = True
        elif in_imports and line.strip() == '':
            other_lines.append(line)
            in_imports = False
        else:
            other_lines.append(line)
            in_imports = False

    if len(import_block) < 2:
        return None

    sorted_imports = sorted(import_block)
    if sorted_imports == import_block:
        return None

    # Reconstruct
    result_lines = sorted_imports + [''] + other_lines
    return BypassResult(
        success=True,
        output='\n'.join(result_lines),
        transform='sort-imports',
        confidence=0.85,
        tokens_saved=200,
    )


def _add_type_annotations_hints(code: str) -> Optional[BypassResult]:
    """Add TODO comments for missing type annotations in Python."""
    lines = code.split('\n')
    hints = []
    for i, line in enumerate(lines):
        # Functions without return type
        if re.match(r'^\s*def\s+\w+\([^)]*\)\s*:', line):
            hints.append(f"Line {i+1}: Missing return type annotation")
        # Variables without type (simple heuristic)
        elif re.match(r'^\s*\w+\s*=\s*(?!None|True|False)', line) and 'def ' not in line:
            pass  # Too noisy, skip

    if not hints:
        return None

    return BypassResult(
        success=True,
        output='\n'.join(hints),
        transform='type-hints-audit',
        confidence=0.6,
        tokens_saved=400,
    )


# ── Intent detection ──────────────────────────────────────────────────

_TRANSFORM_PATTERNS = [
    (r'\bvar\b.*\bconst\b', 'var-to-const', _var_to_const),
    (r'\bsemicolon', 'add-semicolons', _add_semicolons),
    (r'\bconsole\.?log\b.*\b(remove|delete|strip)', 'remove-console', _remove_console_logs),
    (r'\b(sort|organize|order)\b.*\bimport', 'sort-imports', _sort_imports),
    (r'\btype\s*(hint|annotation|check)', 'type-hints', _add_type_annotations_hints),
]


def try_deterministic(goal: str, code: str = "") -> Optional[BypassResult]:
    """Try to handle the task deterministically without LLM.

    Returns BypassResult if successful, None if task needs LLM.
    """
    if not goal:
        return None

    goal_lower = goal.lower()

    # Try pattern matching
    for pattern, name, transform_fn in _TRANSFORM_PATTERNS:
        if re.search(pattern, goal_lower):
            if code:
                result = transform_fn(code)
                if result:
                    logger.debug("Deterministic bypass: %s (saved ~%d tokens)", name, result.tokens_saved)
                    return result
            else:
                # Goal matches but no code provided - can't transform
                pass

    return None


# ── Confidence-based routing ──────────────────────────────────────────

def should_bypass(confidence: float, threshold: float = 0.5) -> bool:
    """Should we bypass LLM based on confidence?"""
    return confidence >= threshold
