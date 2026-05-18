#!/usr/bin/env python3
"""
result_spiller.py — Tool Result Spilling & Retrieval.

Ported from DeepSeek-TUI's truncate.rs + recall_archive.rs pattern.
Large tool results are spilled to disk; models retrieve slices on demand.

Usage:
    from agent.result_spiller import ResultSpiller
    spiller = ResultSpiller()
    ref = spiller.spill("large output...", tool_name="read_file")
    # ref = {"hash": "abc123", "summary": "...", "tokens": 5000}
    content = spiller.retrieve("abc123", mode="head", lines=20)
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".hermes" / "cache" / "tool_outputs"
_SPILL_THRESHOLD = 2000  # chars — results larger than this get spilled
_SUMMARY_LINES = 5


class ResultSpiller:
    """Spill large tool results to disk, retrieve slices on demand."""

    def __init__(self, cache_dir: Path = _CACHE_DIR, threshold: int = _SPILL_THRESHOLD):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold
        self._registry: Dict[str, dict] = self._load_manifest()

    def _load_manifest(self) -> Dict[str, dict]:
        """Load persisted spill metadata so new instances can format summaries."""
        manifest = self.cache_dir / "manifest.json"
        if not manifest.exists():
            return {}
        try:
            data = json.loads(manifest.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.debug("Failed to load spill manifest", exc_info=True)
            return {}

    def should_spill(self, content: str) -> bool:
        """Check if content should be spilled."""
        return len(content) > self.threshold

    def spill(self, content: str, tool_name: str = "", filepath: str = "") -> dict:
        """Spill content to disk. Returns reference dict.

        Returns:
            {"hash": "abc123", "summary": "...", "tokens": N, "lines": N, "tool": "..."}
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        lines = content.split("\n")
        tokens_est = len(content) // 4  # rough estimate

        # Write to disk
        out_file = self.cache_dir / f"{content_hash}.txt"
        out_file.write_text(content, encoding="utf-8")

        # Build summary
        summary = self._build_summary(lines, tool_name, filepath)

        # Register
        metadata = {
            "hash": content_hash,
            "summary": summary,
            "tokens": tokens_est,
            "lines": len(lines),
            "chars": len(content),
            "tool": tool_name,
            "filepath": filepath,
        }
        self._registry[content_hash] = metadata

        # Write manifest
        manifest = self.cache_dir / "manifest.json"
        try:
            existing = json.loads(manifest.read_text()) if manifest.exists() else {}
        except Exception:
            existing = {}
        existing[content_hash] = metadata
        manifest.write_text(json.dumps(existing, indent=2))

        return metadata

    def retrieve(
        self,
        content_hash: str,
        mode: str = "summary",
        lines: int = 20,
        start: int = 0,
        end: int | None = None,
        query: str = "",
    ) -> str:
        """Retrieve spilled content by hash.

        Modes:
            summary — first N lines + metadata
            head — first N lines
            tail — last N lines
            lines — specific line range [start:end]
            query — grep for query string, return matches with context
        """
        out_file = self.cache_dir / f"{content_hash}.txt"
        if not out_file.exists():
            return f"[error: {content_hash} not found in spill cache]"

        content = out_file.read_text(encoding="utf-8")
        all_lines = content.split("\n")
        metadata = self._registry.get(content_hash, {})

        if mode == "summary":
            return self._format_summary(all_lines, metadata, preview_lines=lines)
        elif mode == "head":
            return "\n".join(all_lines[:lines])
        elif mode == "tail":
            return "\n".join(all_lines[-lines:])
        elif mode == "lines":
            s = start or 0
            e = end or min(s + lines, len(all_lines))
            return "\n".join(all_lines[s:e])
        elif mode == "query":
            return self._grep(all_lines, query, context=2)
        else:
            return f"[error: unknown mode '{mode}']"

    def list_spilled(self) -> list:
        """List all spilled results."""
        return list(self._registry.values())

    def cleanup(self, max_age_hours: int = 24):
        """Remove old spilled results."""
        import time
        now = time.time()
        for f in self.cache_dir.glob("*.txt"):
            if now - f.stat().st_mtime > max_age_hours * 3600:
                f.unlink()

    # ── Internal helpers ───────────────────────────────────────────────

    def _build_summary(self, lines: list, tool_name: str, filepath: str) -> str:
        """Build a compact summary of the content."""
        total = len(lines)
        head = lines[:3]
        tail = lines[-2:] if total > 5 else []

        parts = []
        if tool_name:
            parts.append(f"[{tool_name}]")
        if filepath:
            parts.append(filepath)
        parts.append(f"({total} lines)")
        parts.append(" | ".join(l.strip() for l in head if l.strip()))
        if tail:
            parts.append("... " + " | ".join(l.strip() for l in tail if l.strip()))

        return " ".join(parts)

    def _format_summary(self, all_lines: list, metadata: dict, preview_lines: int = 20) -> str:
        """Format a summary with metadata header, boundary anchors, and retrieval hints."""
        meta = metadata or {}
        total_lines = len(all_lines)
        preview_budget = max(1, preview_lines)
        head_count = min(max(1, preview_budget // 2), total_lines)
        remaining = max(0, total_lines - head_count)
        tail_count = min(max(0, preview_budget - head_count), remaining)

        header_parts = [
            f"[Spilled: {meta.get('lines', '?')} lines",
            f"~{meta.get('tokens', '?')} tok",
            f"tool={meta.get('tool', '?')}",
        ]
        if meta.get("filepath"):
            header_parts.append(f"path={meta['filepath']}")
        if meta.get("hash"):
            header_parts.append(f"hash={meta['hash']}")
        header = ", ".join(header_parts) + "]"

        def _format_preview(lines: list[str], start_idx: int) -> list[str]:
            return [f"{start_idx + idx:04d}|{line}" for idx, line in enumerate(lines)]

        body_parts = _format_preview(all_lines[:head_count], 0)
        omitted = total_lines - head_count - tail_count
        if omitted > 0:
            body_parts.append(f"[... {omitted} middle lines omitted ...]")
        if tail_count > 0:
            body_parts.extend(_format_preview(all_lines[-tail_count:], total_lines - tail_count))

        hint = None
        if meta.get("hash"):
            content_hash = meta["hash"]
            hint = (
                "Tips: "
                f"retrieve_result(hash='{content_hash}', mode='lines', start=<line>, end=<line>) "
                "| "
                f"retrieve_result(hash='{content_hash}', mode='query', query='<keyword>')"
            )

        body = "\n".join(body_parts)
        if hint:
            return f"{header}\n{body}\n{hint}"
        return f"{header}\n{body}"

    def _grep(self, lines: list, query: str, context: int = 2) -> str:
        """Grep for query in lines, return matches with context."""
        if not query:
            return "[error: no query specified]"

        matches = []
        for i, line in enumerate(lines):
            if query.lower() in line.lower():
                start = max(0, i - context)
                end = min(len(lines), i + context + 1)
                for j in range(start, end):
                    prefix = "→ " if j == i else "  "
                    matches.append(f"{prefix}{j}: {lines[j]}")
                matches.append("---")

        if not matches:
            return f"[no matches for '{query}']"

        return "\n".join(matches[:50])  # cap at 50 lines
