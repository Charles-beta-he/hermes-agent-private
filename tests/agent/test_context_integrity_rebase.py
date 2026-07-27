"""Focused regression tests for recursive-summary generation governance."""

from pathlib import Path
import json
import sqlite3
from unittest.mock import MagicMock, patch

from agent.context_compressor import ContextCompressor, SUMMARY_PREFIX
from hermes_state import SessionDB


def _compressor(*, max_generations=2):
    with patch("agent.context_compressor.get_model_context_length", return_value=100_000):
        return ContextCompressor(
            model="test/model",
            protect_first_n=1,
            protect_last_n=2,
            quiet_mode=True,
            max_summary_generations=max_generations,
        )


def _messages(*, with_summary=False):
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original requirement"},
        {"role": "assistant", "content": "working"},
        {"role": "user", "content": "decision A"},
        {"role": "assistant", "content": "evidence /tmp/evidence.txt"},
        {"role": "user", "content": "recent ask"},
        {"role": "assistant", "content": "recent answer"},
    ]
    if with_summary:
        messages.insert(
            1,
            {"role": "user", "content": f"{SUMMARY_PREFIX} recursive generation 2"},
        )
    return messages


def test_compaction_state_and_archive_are_committed_together(tmp_path: Path):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    db.create_session("s1", "cli", model="test/model")
    db.append_message("s1", "user", "immutable original")

    db.archive_and_compact(
        "s1",
        [{"role": "user", "content": f"{SUMMARY_PREFIX} fresh summary"}],
        compaction_count=7,
        summary_generation=2,
    )

    assert db.get_compaction_state("s1") == {
        "compaction_count": 7,
        "summary_generation": 2,
    }
    assert [m["content"] for m in db.get_compaction_archived_messages("s1")] == [
        "immutable original"
    ]
    db.close()

    reopened = SessionDB(db_path=db_path)
    assert reopened.get_compaction_state("s1") == {
        "compaction_count": 7,
        "summary_generation": 2,
    }
    reopened.close()


def test_rotation_lineage_rebase_reads_ancestor_archives_and_final_active_tail(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("parent", "cli", model="test/model")
    db.append_message("parent", "user", "parent immutable original")
    db.archive_and_compact(
        "parent",
        [
            {"role": "user", "content": f"{SUMMARY_PREFIX} parent summary"},
            {"role": "assistant", "content": "parent final active tail"},
        ],
        compaction_count=1,
        summary_generation=1,
    )
    db.create_session(
        "child", "cli", model="test/model", parent_session_id="parent"
    )
    db.append_message("child", "user", "child archived original")
    db.archive_and_compact(
        "child",
        [{"role": "user", "content": f"{SUMMARY_PREFIX} child live summary"}],
        compaction_count=2,
        summary_generation=2,
    )

    evidence = [
        msg["content"] for msg in db.get_compaction_rebase_messages("child")
    ]
    assert "parent immutable original" in evidence
    assert "parent final active tail" in evidence
    assert "child archived original" in evidence
    assert all("child live summary" not in content for content in evidence)
    db.close()


def test_bind_session_state_hydrates_generation_after_resume(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("resume", "cli", model="test/model")
    db.update_compaction_state(
        "resume", compaction_count=11, summary_generation=6
    )

    compressor = _compressor()
    compressor.bind_session_state(db, "resume")

    assert compressor.compression_count == 11
    assert compressor.summary_generation == 6
    db.close()


def test_generation_limit_rebases_from_raw_archive_and_resets_generation():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [
                {"role": "user", "content": "immutable original"},
                {"role": "assistant", "content": "raw evidence A"},
                {"role": "user", "content": "raw evidence B"},
                # The next archived batch contains the same text again. Without
                # durable row provenance it must be preserved: content equality
                # cannot distinguish a carried tail from an intentional repeat.
                {"role": "user", "content": f"{SUMMARY_PREFIX} stale summary"},
                {"role": "assistant", "content": "raw evidence A"},
                {"role": "user", "content": "raw evidence B"},
                {"role": "user", "content": "new evidence"},
                # An intentional same-content repeat within one batch remains.
                {"role": "user", "content": "new evidence"},
            ]

    compressor = _compressor(max_generations=2)
    compressor._session_db = Archive()
    compressor._session_id = "s1"
    compressor._previous_summary = f"{SUMMARY_PREFIX} recursive generation 2"
    compressor.summary_generation = 2
    captured = {}

    def fake_summary(turns, focus_topic=None):
        captured["turns"] = turns
        captured["previous_during_call"] = compressor._previous_summary
        compressor._previous_summary = f"{SUMMARY_PREFIX} rebased"
        return compressor._previous_summary

    with (
        patch.object(compressor, "_find_tail_cut_by_tokens", return_value=5),
        patch.object(compressor, "_generate_summary", side_effect=fake_summary),
    ):
        result = compressor.compress(_messages(with_summary=True), force=True)

    evidence = [m["content"] for m in captured["turns"]]
    assert captured["previous_during_call"] is None
    assert evidence.count("raw evidence A") == 2
    assert evidence.count("raw evidence B") == 2
    assert evidence.count("new evidence") == 2
    assert "immutable original" in evidence
    assert all("stale summary" not in value for value in evidence)
    assert result != _messages()
    assert compressor.compression_count == 1
    assert compressor.summary_generation == 1


def test_rebase_budget_bounds_combined_archive_and_current_head_tail():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [
                {"role": "user", "content": "EARLIEST_REQUIREMENT_" + "a" * 70},
                {"role": "assistant", "content": "MIDDLE_EVIDENCE_" + "b" * 70},
                {"role": "user", "content": "LATE_ARCHIVE_" + "c" * 70},
            ]

    compressor = _compressor(max_generations=2)
    compressor.threshold_tokens = 100  # 240-char combined rebase budget
    compressor._session_db = Archive()
    compressor._session_id = "s1"
    compressor._previous_summary = f"{SUMMARY_PREFIX} stale"
    captured = {}

    def fake_summary(turns, focus_topic=None):
        captured["turns"] = turns
        compressor._previous_summary = f"{SUMMARY_PREFIX} rebased"
        return compressor._previous_summary

    latest = {"role": "assistant", "content": "LATEST_CURRENT_" + "z" * 70}
    with patch.object(compressor, "_generate_summary", side_effect=fake_summary):
        assert compressor._rebase_summary_from_archive([latest]) is not None

    serialized = compressor._serialize_for_summary(captured["turns"])
    assert "EARLIEST_REQUIREMENT_" in serialized
    assert "LATEST_CURRENT_" in serialized
    assert "MIDDLE_EVIDENCE_" not in serialized
    assert len(serialized) <= 240


def test_rebase_budget_truncates_one_oversized_message_and_keeps_both_ends():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [
                {
                    "role": "user",
                    "content": "EARLIEST_OVERSIZED_" + "x" * 1_000 + "_LATEST_OVERSIZED",
                }
            ]

    compressor = _compressor(max_generations=1)
    compressor.threshold_tokens = 100  # 240-char complete serialized budget
    compressor._session_db = Archive()
    compressor._session_id = "s1"
    captured = {}

    def fake_summary(turns, focus_topic=None):
        captured["turns"] = turns
        compressor._previous_summary = f"{SUMMARY_PREFIX} rebased"
        return compressor._previous_summary

    with patch.object(compressor, "_generate_summary", side_effect=fake_summary):
        assert compressor._rebase_summary_from_archive([]) is not None

    serialized = compressor._serialize_for_summary(captured["turns"])
    assert "EARLIEST_OVERSIZED_" in serialized
    assert "_LATEST_OVERSIZED" in serialized
    assert len(serialized) <= 240


def test_rebase_budget_reserves_distinct_latest_endpoint():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [{"role": "user", "content": "EARLIEST_" + "x" * 1_000}]

    compressor = _compressor(max_generations=1)
    compressor.threshold_tokens = 100
    compressor._session_db = Archive()
    compressor._session_id = "s1"
    captured = {}

    def fake_summary(turns, focus_topic=None):
        captured["turns"] = turns
        return f"{SUMMARY_PREFIX} rebased"

    latest = {"role": "assistant", "content": "LATEST_SENTINEL"}
    with patch.object(compressor, "_generate_summary", side_effect=fake_summary):
        assert compressor._rebase_summary_from_archive([latest]) is not None

    serialized = compressor._serialize_for_summary(captured["turns"])
    assert "EARLIEST_" in serialized
    assert "LATEST_SENTINEL" in serialized
    assert len(serialized) <= 240


def test_rebase_complete_provider_envelope_fits_and_dispatches():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [
                {"role": "user", "content": "EARLIEST_PROVIDER_EVIDENCE"},
                {"role": "assistant", "content": "middle evidence " * 80},
            ]

    compressor = _compressor(max_generations=1)
    compressor.threshold_tokens = 5_000
    compressor._session_db = Archive()
    compressor._session_id = "s1"
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "rebased checkpoint"

    latest = {"role": "assistant", "content": "LATEST_PROVIDER_EVIDENCE"}
    with patch(
        "agent.context_compressor.call_llm", return_value=response
    ) as call:
        result = compressor._rebase_summary_from_archive(
            [latest], focus_topic="provider envelope"
        )

    assert result is not None
    assert result.startswith(SUMMARY_PREFIX)
    assert result.endswith("rebased checkpoint")
    call.assert_called_once()
    kwargs = call.call_args.kwargs
    prompt = kwargs["messages"][0]["content"]
    framed_chars = len(
        json.dumps(kwargs["messages"], ensure_ascii=False, separators=(",", ":"))
    )
    assert "Create a structured checkpoint summary" in prompt
    assert "## Goal" in prompt
    assert 'FOCUS TOPIC: "provider envelope"' in prompt
    assert "EARLIEST_PROVIDER_EVIDENCE" in prompt
    assert "LATEST_PROVIDER_EVIDENCE" in prompt
    assert framed_chars + kwargs["max_tokens"] * 4 <= compressor.threshold_tokens * 4


def test_rebase_complete_provider_envelope_fails_closed_before_call():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [{"role": "user", "content": "immutable evidence"}]

    compressor = _compressor(max_generations=1)
    compressor.threshold_tokens = 100
    compressor._session_db = Archive()
    compressor._session_id = "s1"

    with patch("agent.context_compressor.call_llm") as call:
        assert compressor._rebase_summary_from_archive([]) is None

    call.assert_not_called()
    assert "complete rebase provider envelope exceeds" in (compressor._last_summary_error or "")


def test_hydration_failure_with_persisted_summary_fails_closed():
    class BrokenState:
        @staticmethod
        def get_compaction_state(_session_id):
            raise sqlite3.OperationalError("db unavailable")

    compressor = _compressor(max_generations=1)
    compressor.bind_session_state(BrokenState(), "s1")
    messages = _messages(with_summary=True)

    with (
        patch.object(compressor, "_find_tail_cut_by_tokens", return_value=5),
        patch("agent.context_compressor.call_llm") as call,
    ):
        result = compressor.compress(messages, force=True)

    assert result == messages
    assert compressor._last_compress_aborted is True
    assert "durable compaction state unavailable" in (compressor._last_summary_error or "")
    call.assert_not_called()


def test_real_session_end_resets_both_lineage_counters():
    compressor = _compressor()
    compressor.compression_count = 9
    compressor.summary_generation = 4

    compressor.on_session_end("old", [])
    compressor.bind_session_state(None, "new")

    assert compressor.compression_count == 0
    assert compressor.summary_generation == 0


def test_generation_limit_archive_read_failure_fails_closed():
    class BrokenArchive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            raise sqlite3.OperationalError("simulated archive read failure")

    compressor = _compressor(max_generations=1)
    compressor._previous_summary = f"{SUMMARY_PREFIX} generation 1"
    compressor.summary_generation = 1
    compressor._session_db = BrokenArchive()
    compressor._session_id = "s1"
    messages = _messages(with_summary=True)

    with (
        patch.object(compressor, "_find_tail_cut_by_tokens", return_value=5),
        patch.object(compressor, "_generate_summary") as generate,
    ):
        result = compressor.compress(messages, force=True)

    assert result == messages
    assert compressor._last_compress_aborted is True
    assert compressor._last_summary_error is not None
    assert "simulated archive read failure" in compressor._last_summary_error
    assert compressor.compression_count == 0
    assert compressor.summary_generation == 1
    generate.assert_not_called()


def test_generation_limit_provider_failure_restores_previous_summary_and_fails_closed():
    class Archive:
        @staticmethod
        def get_compaction_archived_messages(_session_id):
            return [{"role": "user", "content": "immutable evidence"}]

    compressor = _compressor(max_generations=1)
    prior = f"{SUMMARY_PREFIX} generation 1"
    compressor._previous_summary = prior
    compressor.summary_generation = 1
    compressor._session_db = Archive()
    compressor._session_id = "s1"
    messages = _messages(with_summary=True)

    with (
        patch.object(compressor, "_find_tail_cut_by_tokens", return_value=5),
        patch.object(compressor, "_generate_summary", return_value=None),
    ):
        result = compressor.compress(messages, force=True)

    assert result == messages
    assert compressor._last_compress_aborted is True
    assert compressor._previous_summary == prior
    assert compressor.compression_count == 0
    assert compressor.summary_generation == 1


def test_generation_limit_without_raw_archive_fails_closed():
    compressor = _compressor(max_generations=2)
    compressor._previous_summary = f"{SUMMARY_PREFIX} recursive generation 2"
    compressor.summary_generation = 2
    before = _messages(with_summary=True)

    with (
        patch.object(compressor, "_find_tail_cut_by_tokens", return_value=5),
        patch.object(compressor, "_generate_summary") as generate,
    ):
        result = compressor.compress(before, force=True)

    assert result == before
    assert compressor.compression_count == 0
    assert compressor.summary_generation == 2
    assert compressor._last_compress_aborted is True
    assert compressor._last_summary_dropped_count == 0
    assert compressor._last_summary_error is not None
    assert "cannot rebase" in compressor._last_summary_error
    generate.assert_not_called()
