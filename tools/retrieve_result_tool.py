#!/usr/bin/env python3
"""Retrieve previously spilled large tool results by hash."""

from __future__ import annotations

from agent.result_spiller import ResultSpiller
from tools.registry import registry, tool_error


RETRIEVE_RESULT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "retrieve_result",
        "description": (
            "Retrieve a previously spilled large tool result by hash. "
            "Use this when a prior tool output was replaced with a spill handle like "
            "'Spilled to disk ... Hash: abc123'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hash": {
                    "type": "string",
                    "description": "Spill hash from the handle text.",
                },
                "content_hash": {
                    "type": "string",
                    "description": "Alias for hash.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "head", "tail", "lines", "query"],
                    "description": (
                        "Retrieval mode: summary=head with metadata, head=first N lines, "
                        "tail=last N lines, lines=specific range, query=grep-style search."
                    ),
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to return for summary/head/tail/lines modes.",
                    "minimum": 1,
                },
                "start": {
                    "type": "integer",
                    "description": "0-based start line for mode='lines'.",
                    "minimum": 0,
                },
                "end": {
                    "type": "integer",
                    "description": "0-based exclusive end line for mode='lines'.",
                    "minimum": 0,
                },
                "query": {
                    "type": "string",
                    "description": "Search string for mode='query'.",
                },
            },
        },
    },
}


def retrieve_result_tool(
    hash: str | None = None,
    content_hash: str | None = None,
    mode: str = "summary",
    lines: int = 20,
    start: int = 0,
    end: int | None = None,
    query: str = "",
    spiller: ResultSpiller | None = None,
) -> str:
    """Retrieve spilled content slices via ResultSpiller."""
    resolved_hash = (hash or content_hash or "").strip()
    if not resolved_hash:
        return tool_error("Missing required parameter: hash")

    if mode not in {"summary", "head", "tail", "lines", "query"}:
        return tool_error(f"Invalid mode: {mode}")

    active_spiller = spiller or ResultSpiller()
    return active_spiller.retrieve(
        content_hash=resolved_hash,
        mode=mode,
        lines=lines,
        start=start,
        end=end,
        query=query,
    )


def check_retrieve_result_requirements() -> bool:
    """Result retrieval is always available; cache dir is created lazily."""
    return True


registry.register(
    name="retrieve_result",
    toolset="retrieve_result",
    schema=RETRIEVE_RESULT_SCHEMA,
    handler=lambda args, **kw: retrieve_result_tool(
        hash=args.get("hash"),
        content_hash=args.get("content_hash"),
        mode=args.get("mode", "summary"),
        lines=args.get("lines", 20),
        start=args.get("start", 0),
        end=args.get("end"),
        query=args.get("query", ""),
        spiller=kw.get("spiller"),
    ),
    check_fn=check_retrieve_result_requirements,
    emoji="🗂️",
)
