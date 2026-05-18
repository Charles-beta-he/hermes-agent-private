#!/usr/bin/env python3
"""
anatomy_scanner.py — Project file index with token estimates.

Ported from OpenWolf's anatomy scanner concept.
Scans project directories, generates file descriptions + token estimates.
Used by token_compressor to skip redundant file reads.

Usage:
    from agent.anatomy_scanner import AnatomyScanner
    scanner = AnatomyScanner()
    index = scanner.scan("/path/to/project")
    summary = scanner.get_summary("/path/to/project/src/index.js")
    # → "index.js — Main entry point, exports Express app. (~340 tok)"
"""

import os
import re
import json
import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)

# Cache directory
_CACHE_DIR = Path.home() / ".hermes" / "cache" / "anatomy"

# File extensions to scan
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".java", ".go", ".rs", ".rb", ".php", ".c", ".cpp", ".h",
    ".css", ".scss", ".less", ".html", ".xml", ".yaml", ".yml",
    ".json", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql", ".gql",
    ".md", ".txt", ".rst",
}

# Ignore patterns
_IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "coverage", ".cache",
    "vendor", "target", "bin", "obj", ".idea", ".vscode",
}

_IGNORE_FILES = {
    ".DS_Store", "Thumbs.db", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "Cargo.lock", "go.sum", "poetry.lock",
}

# Token estimation: chars per token (code vs prose)
_CHARS_PER_TOKEN_CODE = 3.5
_CHARS_PER_TOKEN_PROSE = 4.0

# Max file size to scan (100KB)
_MAX_FILE_SIZE = 100 * 1024


class AnatomyScanner:
    """Scan project directories and build file indexes with token estimates."""

    def __init__(self, cache_dir: Path = _CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session_reads: Dict[str, int] = {}  # filepath -> token count

    def scan(self, project_root: str, force: bool = False) -> Dict[str, dict]:
        """Scan project and return anatomy index.

        Returns dict: {relative_path: {description, tokens, size, ext}}
        """
        project_root = os.path.abspath(project_root)
        cache_key = hashlib.md5(project_root.encode()).hexdigest()[:12]
        cache_file = self.cache_dir / f"{cache_key}.json"

        # Check cache
        if not force and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                if cached.get("root") == project_root:
                    logger.debug("Anatomy cache hit: %s", project_root)
                    return cached.get("index", {})
            except Exception:
                pass

        # Scan
        index = {}
        for root, dirs, files in os.walk(project_root):
            # Skip ignored directories
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]

            for filename in files:
                if filename in _IGNORE_FILES:
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, project_root)
                ext = os.path.splitext(filename)[1].lower()

                if ext not in _CODE_EXTENSIONS:
                    continue

                try:
                    stat = os.stat(filepath)
                    if stat.st_size > _MAX_FILE_SIZE:
                        continue
                    if stat.st_size == 0:
                        continue

                    # Read first 2KB for description extraction
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        head = f.read(2048)

                    description = self._extract_description(head, filename, ext)
                    tokens = self._estimate_tokens(stat.st_size, ext)

                    index[rel_path] = {
                        "description": description,
                        "tokens": tokens,
                        "size": stat.st_size,
                        "ext": ext,
                    }
                except (OSError, PermissionError):
                    continue

        # Cache
        try:
            cache_file.write_text(json.dumps({
                "root": project_root,
                "index": index,
                "file_count": len(index),
            }, indent=2))
        except Exception as e:
            logger.debug("Anatomy cache write failed: %s", e)

        return index

    def get_summary(self, filepath: str, project_root: str = None) -> Optional[str]:
        """Get one-line summary for a file. Returns None if not indexed."""
        if project_root:
            index = self.scan(project_root)
            rel_path = os.path.relpath(filepath, project_root)
        else:
            # Try to find in any cached index
            for cache_file in self.cache_dir.glob("*.json"):
                try:
                    cached = json.loads(cache_file.read_text())
                    index = cached.get("index", {})
                    # Try absolute and relative paths
                    for key, val in index.items():
                        if filepath.endswith(key) or key.endswith(filepath):
                            return self._format_summary(key, val)
                    continue
                except Exception:
                    continue
            return None

        entry = index.get(rel_path)
        if not entry:
            return None

        return self._format_summary(rel_path, entry)

    def check_repeat(self, filepath: str) -> Optional[dict]:
        """Check if file was already read this session.
        Returns {tokens, count} if repeat, None if first read."""
        if filepath in self._session_reads:
            return {
                "tokens": self._session_reads[filepath],
                "count": sum(1 for f in self._session_reads if f == filepath),
            }
        return None

    def record_read(self, filepath: str, tokens: int):
        """Record a file read for repeat detection."""
        self._session_reads[filepath] = tokens

    def get_session_stats(self) -> dict:
        """Get session read statistics."""
        return {
            "files_read": len(self._session_reads),
            "total_tokens": sum(self._session_reads.values()),
            "files": dict(self._session_reads),
        }

    def clear_session(self):
        """Clear session read tracking."""
        self._session_reads.clear()

    # ── Internal helpers ───────────────────────────────────────────────

    def _extract_description(self, head: str, filename: str, ext: str) -> str:
        """Extract a one-line description from file head."""
        lines = head.split("\n")

        # Try docstring (Python)
        if ext == ".py":
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    desc = stripped.strip('"\'').strip()
                    if desc and len(desc) > 10:
                        return desc[:100]
                if stripped.startswith("#") and not stripped.startswith("#!"):
                    desc = stripped.lstrip("# ").strip()
                    if desc and len(desc) > 10:
                        return desc[:100]

        # Try JSDoc / comments (JS/TS)
        if ext in (".js", ".ts", ".jsx", ".tsx", ".vue"):
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("/**"):
                    continue
                if stripped.startswith("*") and not stripped.startswith("*/"):
                    desc = stripped.lstrip("* ").strip()
                    if desc and len(desc) > 10 and not desc.startswith("@"):
                        return desc[:100]
                if stripped.startswith("//"):
                    desc = stripped.lstrip("/ ").strip()
                    if desc and len(desc) > 10:
                        return desc[:100]

        # Try exports
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("export default"):
                return f"Default export: {stripped[15:60].strip()}"
            if stripped.startswith("export "):
                return f"Export: {stripped[7:60].strip()}"

        # Fallback: filename-based
        name = os.path.splitext(filename)[0]
        ext_desc = {
            ".py": "Python module",
            ".js": "JavaScript module",
            ".ts": "TypeScript module",
            ".jsx": "React component",
            ".tsx": "React TypeScript component",
            ".vue": "Vue component",
            ".css": "Stylesheet",
            ".html": "HTML document",
            ".json": "JSON data",
            ".yaml": "YAML config",
            ".yml": "YAML config",
            ".md": "Documentation",
            ".sh": "Shell script",
            ".sql": "SQL script",
        }
        return f"{name} ({ext_desc.get(ext, ext[1:].upper())})"

    def _estimate_tokens(self, size_bytes: int, ext: str) -> int:
        """Estimate token count from file size."""
        if ext in (".md", ".txt", ".rst"):
            return int(size_bytes / _CHARS_PER_TOKEN_PROSE)
        return int(size_bytes / _CHARS_PER_TOKEN_CODE)

    def _format_summary(self, rel_path: str, entry: dict) -> str:
        """Format a one-line summary."""
        name = os.path.basename(rel_path)
        desc = entry.get("description", "")
        tokens = entry.get("tokens", 0)
        return f"{name} — {desc} (~{tokens} tok)"
