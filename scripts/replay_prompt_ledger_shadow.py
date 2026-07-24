#!/usr/bin/env python3
"""Read-only replay of Prompt Ledger context projections over SessionDB rows."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.prompt_ledger import decode_stored_content, project_messages_shadow


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.expanduser().resolve()
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _select_session_ids(
    conn: sqlite3.Connection,
    requested: Iterable[str] | None,
    top: int,
) -> list[str]:
    explicit = [str(value) for value in (requested or []) if str(value)]
    if explicit:
        return explicit
    rows = conn.execute(
        """
        SELECT session_id, SUM(length(CAST(content AS BLOB))) AS payload_bytes
        FROM messages
        WHERE active = 1
        GROUP BY session_id
        ORDER BY payload_bytes DESC, session_id
        LIMIT ?
        """,
        (max(1, int(top)),),
    ).fetchall()
    return [str(row["session_id"]) for row in rows]


def _load_messages(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT role, content, tool_name, tool_call_id
        FROM messages
        WHERE session_id = ? AND active = 1
        ORDER BY id
        """,
        (session_id,),
    ).fetchall()
    messages = []
    for row in rows:
        message: dict[str, Any] = {
            "role": row["role"],
            "content": decode_stored_content(row["content"]),
        }
        if row["tool_name"] is not None:
            message["tool_name"] = row["tool_name"]
        if row["tool_call_id"] is not None:
            message["tool_call_id"] = row["tool_call_id"]
        messages.append(message)
    return messages


def replay_sessions(
    db_path: Path | str,
    *,
    session_ids: Iterable[str] | None = None,
    top: int = 2,
    protect_last_tool_results: int = 1,
    min_text_chars: int = 4096,
    preview_chars: int = 256,
) -> dict[str, Any]:
    """Return bounded projection metrics without writing the source database."""
    source = Path(db_path).expanduser().resolve()
    before = source.stat()
    reports = []
    with _open_read_only(source) as conn:
        selected = _select_session_ids(conn, session_ids, top)
        for session_id in selected:
            messages = _load_messages(conn, session_id)
            projection = project_messages_shadow(
                messages,
                protect_last_tool_results=protect_last_tool_results,
                min_text_chars=min_text_chars,
                preview_chars=preview_chars,
            )
            candidates = projection["candidates"]
            reports.append(
                {
                    "session_id": session_id,
                    "message_count": len(messages),
                    "original_chars": projection["original_chars"],
                    "projected_chars": projection["projected_chars"],
                    "estimated_reduction_chars": projection["estimated_reduction_chars"],
                    "candidate_count": len(candidates),
                    "recoverable_candidate_count": sum(
                        1 for candidate in candidates if candidate["recoverable"]
                    ),
                    "would_apply_count": sum(
                        1 for candidate in candidates if candidate["would_apply"]
                    ),
                    "candidate_kinds": {
                        kind: sum(1 for candidate in candidates if candidate["kind"] == kind)
                        for kind in sorted({candidate["kind"] for candidate in candidates})
                    },
                    "retirement_blockers": {
                        blocker: sum(
                            1
                            for candidate in candidates
                            if candidate.get("retirement_blocker") == blocker
                        )
                        for blocker in sorted(
                            {
                                candidate["retirement_blocker"]
                                for candidate in candidates
                                if candidate.get("retirement_blocker")
                            }
                        )
                    },
                }
            )
    after = source.stat()
    return {
        "schema_version": 1,
        "source_db": str(source),
        "source_open_mode": "read_only",
        "source_integrity_scope": "main_file_stat_only",
        "source_sidecars_checked": False,
        "source_main_file_metadata_unchanged": (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
        ),
        "source_size_bytes": after.st_size,
        "protect_last_tool_results": protect_last_tool_results,
        "min_text_chars": min_text_chars,
        "preview_chars": preview_chars,
        "sessions": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--top", type=int, default=2)
    parser.add_argument("--protect-last-tool-results", type=int, default=1)
    parser.add_argument("--min-text-chars", type=int, default=4096)
    parser.add_argument("--preview-chars", type=int, default=256)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = replay_sessions(
        args.db,
        session_ids=args.session_id,
        top=args.top,
        protect_last_tool_results=args.protect_last_tool_results,
        min_text_chars=args.min_text_chars,
        preview_chars=args.preview_chars,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
