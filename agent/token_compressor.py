#!/usr/bin/env python3
"""
token_compressor.py — General-purpose token compression middleware for Hermes Agent.

Applies compression to all token flows:
1. Tool descriptions (schema-level)
2. Tool outputs (read_file, search_files, browser, web, session)
3. Platform hints
4. Prompt constants
5. Conversation history (via compression settings)

Usage:
    from agent.token_compressor import compress_tool_schema, compress_output
"""

import re
from typing import Any, Dict, List, Optional


# ── Tool Description Compression ──────────────────────────────────────

# Map verbose phrases to terse equivalents
_PHRASE_MAP = [
    (r"The (?:optional )?(?:path|file path|directory) to ", ""),
    (r"Whether to (?:or not )?", "If "),
    (r"The (?:maximum|minimum) (?:number of|amount of) ", "Max "),
    (r"A (?:boolean|flag|value|number|string|list|dict) (?:that |indicating |specifying )", ""),
    (r"(?:If |When )set to (?:true|True),?\s*", ""),
    (r"(?:Defaults?|default)\s*(?:to|is|:)\s*[\"']?[^\"'.\n]+[\"']?", ""),
    (r"\s*\(default[^)]*\)", ""),
    (r"\s*\(optional\)", ""),
    (r"\s*\(required\)", " *"),
    (r"Note:\s*", ""),
    (r"Important:\s*", ""),
    (r"This (?:tool|function|method) ", ""),
    (r"Use this (?:to|when|for|tool) ", ""),
    (r"Returns? (?:a |the )?(?:dict|list|string|result|output) (?:containing|with|of) ", "→ "),
    (r"Please (?:note|be aware|make sure) (?:that )?", ""),
    (r"It (?:is|will be) (?:used|needed|required) (?:to|for|when) ", ""),
]

# Word-level abbreviations for common technical terms
_WORD_MAP = {
    "information": "info",
    "configuration": "config",
    "directory": "dir",
    "parameters": "params",
    "arguments": "args",
    "description": "desc",
    "identifier": "id",
    "environment": "env",
    "application": "app",
    "repository": "repo",
    "operation": "op",
    "characters": "chars",
    "maximum": "max",
    "minimum": "min",
    "number": "num",
    "optional": "opt",
    "available": "avail",
    "following": "following",
    "specific": "spec",
    "additional": "extra",
    "previous": "prev",
    "different": "diff",
    "multiple": "multi",
    "including": "incl",
    "excluding": "excl",
    "approximately": "~",
    "before": "pre",
    "after": "post",
}


def compress_tool_description(desc: str, max_len: int = 120) -> str:
    """Compress a tool description to fit within max_len chars.

    Strategy:
    1. Remove filler phrases
    2. Abbreviate common words
    3. Remove redundant whitespace
    4. Truncate with ellipsis if still too long
    """
    if not desc or len(desc) <= max_len:
        return desc

    text = desc.strip()

    # Remove verbose phrases
    for pattern, replacement in _PHRASE_MAP:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Abbreviate words (only if they appear in longer text)
    if len(text) > max_len:
        for full, abbr in _WORD_MAP.items():
            text = re.sub(r'\b' + full + r'\b', abbr, text, flags=re.IGNORECASE)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Truncate if still too long
    if len(text) > max_len:
        text = text[:max_len - 3] + "..."

    return text


def compress_tool_schemas(
    tools: List[Dict[str, Any]],
    max_desc_len: int = 120,
    max_param_desc_len: int = 80,
    remove_optional_descriptions: bool = False,
) -> List[Dict[str, Any]]:
    """Compress all tool schemas in-place.

    Args:
        tools: List of OpenAI-format tool definitions
        max_desc_len: Max length for tool descriptions
        max_param_desc_len: Max length for parameter descriptions
        remove_optional_descriptions: Strip descriptions from optional params
    """
    import copy
    tools = copy.deepcopy(tools)  # Don't mutate originals

    for tool in tools:
        func = tool.get("function", {})
        if not func:
            continue

        # Compress tool-level description
        desc = func.get("description", "")
        if desc:
            func["description"] = compress_tool_description(desc, max_desc_len)

        # Compress parameter descriptions
        params = func.get("parameters", {})
        properties = params.get("properties", {})
        required = set(params.get("required", []))

        for param_name, param_schema in properties.items():
            if not isinstance(param_schema, dict):
                continue

            param_desc = param_schema.get("description", "")
            if not param_desc:
                continue

            # Remove optional param descriptions if requested
            if remove_optional_descriptions and param_name not in required:
                param_schema.pop("description", None)
                continue

            # Compress parameter description
            param_schema["description"] = compress_tool_description(
                param_desc, max_param_desc_len
            )

    return tools


# ── Tool Output Compression ───────────────────────────────────────────

def compress_tool_output(
    output: str,
    tool_name: str = "",
    max_lines: int = 20,
    max_chars: int = 3000,
) -> str:
    """Compress tool output before injecting into context.

    Applies tool-specific compression strategies:
    - read_file: strip blank lines, compress whitespace
    - search_files: limit results, summarize matches
    - browser_snapshot: truncate DOM, keep interactive elements
    - web_search/extract: summarize, extract key facts
    - session_search: limit to top-N results
    - terminal: use RTK compression
    - delegate_task: already returns summaries
    - Large results (>2K chars): spill to disk, return handle reference
    """
    if not output:
        return output

    # ── Result Spilling: large results → disk + handle ──
    # DeepSeek-TUI pattern: keep full content out of neural context,
    # return a small handle (~200 bytes) with metadata.
    if len(output) > 2000:
        try:
            from agent.result_spiller import ResultSpiller
            spiller = ResultSpiller()
            ref = spiller.spill(output, tool_name=tool_name)
            # Build a compact handle reference for the model
            handle_text = (
                f"[Spilled to disk: {ref['lines']} lines, ~{ref['tokens']} tok]\n"
                f"Hash: {ref['hash']}\n"
                f"Summary: {ref['summary'][:200]}\n"
                f"Retrieve with: retrieve_result(hash='{ref['hash']}', mode='summary')\n"
                "Other modes: lines/query/tail"
            )
            return handle_text
        except Exception:
            pass  # Fall through to regular compression

    # Quick exit if already small
    if len(output) <= max_chars and output.count('\n') <= max_lines:
        return output

    lines = output.split('\n')

    # Tool-specific strategies
    if tool_name in ('read_file', 'read_text_file'):
        return _compress_file_output(lines, max_lines)
    elif tool_name == 'search_files':
        return _compress_search_output(lines, max_lines)
    elif tool_name in ('browser_snapshot', 'browser_vision'):
        return _compress_browser_output(lines, max_lines)
    elif tool_name in ('web_search', 'web_extract'):
        return _compress_web_output(lines, max_lines)
    elif tool_name == 'session_search':
        return _compress_session_output(lines, max_lines)
    else:
        return _compress_generic_output(lines, max_lines, max_chars)


def _compress_file_output(lines: list, max_lines: int) -> str:
    """Compress read_file output: strip blank lines, keep structure."""
    # Remove consecutive blank lines
    compressed = []
    blank_count = 0
    for line in lines:
        if line.strip():
            if blank_count > 1:
                compressed.append(f"[{blank_count} blank lines]")
            blank_count = 0
            compressed.append(line)
        else:
            blank_count += 1
    if blank_count > 1:
        compressed.append(f"[{blank_count} blank lines]")

    if len(compressed) > max_lines:
        head = compressed[:max_lines // 2]
        tail = compressed[-max_lines // 2:]
        return '\n'.join(head) + f"\n[...{len(compressed) - max_lines} lines omitted...]\n" + '\n'.join(tail)

    return '\n'.join(compressed)


def _compress_search_output(lines: list, max_lines: int) -> str:
    """Compress search_files output: limit results."""
    if len(lines) <= max_lines:
        return '\n'.join(lines)

    # Keep first N results + summary
    kept = lines[:max_lines - 1]
    total = len(lines)
    return '\n'.join(kept) + f"\n[...{total - max_lines + 1} more matches]"


def _compress_browser_output(lines: list, max_lines: int) -> str:
    """Compress browser output: keep interactive elements, truncate DOM."""
    if len(lines) <= max_lines:
        return '\n'.join(lines)

    # Keep interactive elements (ref IDs) and key content
    interactive = [l for l in lines if '@e' in l or 'ref=' in l]
    other = [l for l in lines if '@e' not in l and 'ref=' not in l]

    if len(interactive) + len(other) <= max_lines:
        return '\n'.join(lines)

    # Prioritize interactive elements
    head = interactive[:max_lines // 2]
    tail = other[-max_lines // 2:]
    return '\n'.join(head) + f"\n[...truncated browser output...]\n" + '\n'.join(tail)


def _compress_web_output(lines: list, max_lines: int) -> str:
    """Compress web output: extract key facts."""
    if len(lines) <= max_lines:
        return '\n'.join(lines)

    # Keep first N lines (usually most relevant)
    kept = lines[:max_lines]
    return '\n'.join(kept) + f"\n[...{len(lines) - max_lines} more lines]"


def _compress_session_output(lines: list, max_lines: int) -> str:
    """Compress session_search output: limit results."""
    if len(lines) <= max_lines:
        return '\n'.join(lines)

    kept = lines[:max_lines]
    return '\n'.join(kept) + f"\n[...{len(lines) - max_lines} more sessions]"


def _compress_generic_output(lines: list, max_lines: int, max_chars: int) -> str:
    """Generic compression: keep head and tail."""
    if len(lines) <= max_lines:
        text = '\n'.join(lines)
        if len(text) <= max_chars:
            return text

    head_n = max_lines // 2
    tail_n = max_lines - head_n
    head = lines[:head_n]
    tail = lines[-tail_n:]

    result = '\n'.join(head) + f"\n[...{len(lines) - max_lines} lines omitted...]\n" + '\n'.join(tail)
    if len(result) > max_chars:
        result = result[:max_chars - 20] + "\n[...truncated...]"

    return result


# ── Platform Hint Compression ─────────────────────────────────────────

def compress_platform_hint(hint: str, max_len: int = 300) -> str:
    """Compress a platform hint to max_len chars."""
    if not hint or len(hint) <= max_len:
        return hint

    # Extract key rules, skip examples
    lines = hint.split('\n')
    key_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip example lines
        if stripped.startswith('-') and ('e.g.' in stripped or 'example' in stripped.lower()):
            continue
        key_lines.append(stripped)

    result = '\n'.join(key_lines)
    if len(result) > max_len:
        result = result[:max_len - 3] + "..."

    return result


# ── Statistics ─────────────────────────────────────────────────────────

def compression_stats(
    original_tools: List[Dict[str, Any]],
    compressed_tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate compression statistics."""
    import json
    orig_size = sum(len(json.dumps(t)) for t in original_tools)
    comp_size = sum(len(json.dumps(t)) for t in compressed_tools)

    return {
        "original_chars": orig_size,
        "compressed_chars": comp_size,
        "saved_chars": orig_size - comp_size,
        "ratio": (orig_size - comp_size) / orig_size * 100 if orig_size > 0 else 0,
    }
