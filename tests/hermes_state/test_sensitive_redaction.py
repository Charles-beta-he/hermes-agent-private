"""Regression tests for transcript secret redaction before persistence/FTS."""

import json

from agent.sensitive_redaction import REDACTION_PLACEHOLDER, contains_unredacted_secret
from hermes_state import SessionDB

STITCH_KEY = "AQ.Ab8RN6J0Bk92Vw1X5o2LgofASJMrJAVFeTVrmUfHoYC7utcTtQ"


def test_append_message_redacts_user_content_and_fts(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s", source="cli")

    mid = db.append_message(
        "s",
        role="user",
        content=f"workflow接入SDK，API密钥是{STITCH_KEY}",
    )

    assert db._conn is not None
    row = db._conn.execute("SELECT content FROM messages WHERE id=?", (mid,)).fetchone()
    assert STITCH_KEY not in row["content"]
    assert REDACTION_PLACEHOLDER in row["content"]
    fts_hits = db._conn.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE content LIKE ?",
        (f"%{STITCH_KEY}%",),
    ).fetchone()[0]
    assert fts_hits == 0


def test_append_message_redacts_tool_calls_and_codex_items(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s", source="cli")

    mid = db.append_message(
        "s",
        role="assistant",
        content="calling tool",
        tool_calls=[{"function": {"name": "write", "arguments": f"apiKey={STITCH_KEY}"}}],
        codex_message_items=[{"type": "message", "text": f"Bearer {STITCH_KEY}"}],
    )

    assert db._conn is not None
    row = db._conn.execute(
        "SELECT tool_calls, codex_message_items FROM messages WHERE id=?", (mid,)
    ).fetchone()
    serialized = json.dumps(dict(row), ensure_ascii=False)
    assert STITCH_KEY not in serialized
    assert REDACTION_PLACEHOLDER in serialized
    assert not contains_unredacted_secret(serialized)


def test_replace_messages_redacts_transcript_rewrites(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("s", source="cli")

    db.replace_messages(
        "s",
        [
            {"role": "user", "content": f"new key {STITCH_KEY}"},
            {"role": "assistant", "content": "ok"},
        ],
    )

    assert db._conn is not None
    rows = db._conn.execute("SELECT content FROM messages ORDER BY id").fetchall()
    combined = "\n".join(row["content"] or "" for row in rows)
    assert STITCH_KEY not in combined
    assert REDACTION_PLACEHOLDER in combined
