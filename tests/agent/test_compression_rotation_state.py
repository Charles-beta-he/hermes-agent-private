"""Compression rotation hardening — state-loss fixes at the compaction boundary.

When auto-compression rotates ``agent.session_id`` to a continuation child,
three pieces of state used to be lost or corrupted:

  * #33618 — a persistent ``/goal`` did not follow the rotation (``load_goal``
    is a flat per-session lookup with no lineage walk), so it silently died.
  * #33906/#33907 — if the child ``create_session`` raised, the outer handler
    only warned and let the agent continue on the NEW (un-indexed) id,
    producing an orphan session missing from state.db.
  * #27633 — the compaction-boundary ``on_session_start`` notification omitted
    the ``platform`` kwarg, so context-engine plugins saw ``source=unknown``
    for every message after the boundary.

These tests drive the real ``compress_context`` path against a real SessionDB.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from hermes_state import SessionDB


def _build_agent_with_db(db: SessionDB, session_id: str, platform: str = "telegram"):
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            platform=platform,
            quiet_mode=True,
            session_db=db,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
        )

    compressor = MagicMock()
    compressor.compress.return_value = [
        {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
        {"role": "user", "content": "tail"},
    ]
    compressor.compression_count = 1
    compressor.last_prompt_tokens = 0
    compressor.last_completion_tokens = 0
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_summary_auth_failure = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    agent.context_compressor = compressor
    # ROTATION fallback path — pin in_place=False so these keep covering fork
    # rotation regardless of the global default (flipped to True in #38763).
    agent.compression_in_place = False
    return agent


def _msgs(n=20):
    return [{"role": "user", "content": f"m{i}"} for i in range(n)]


class TestSmartRoutingRuntimeMigratesOnRotation:
    def test_smart_route_binds_to_compression_child(self, tmp_path: Path):
        db = SessionDB(db_path=tmp_path / "state.db")
        parent = "PARENT_SMART_ROUTE_ROT"
        db.create_session(parent, source="cli")
        agent = _build_agent_with_db(db, parent)
        runtime = {
            "session_id": parent,
            "provider": "openrouter",
            "model": "test/model",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "tier": "MEDIUM",
        }
        setattr(agent, "_smart_routing_runtime", runtime)
        getattr(agent, "_session_init_model_config")["smart_routing_runtime"] = dict(runtime)

        agent._compress_context(_msgs(), "sys", approx_tokens=120_000)

        child = getattr(agent, "session_id")
        row = db.get_session(child)
        assert row is not None
        config = json.loads(row["model_config"])
        assert child != parent
        assert getattr(agent, "_smart_routing_runtime")["session_id"] == child
        assert config["smart_routing_runtime"]["session_id"] == child


class TestGoalMigratesOnRotation:
    def test_goal_follows_compression_rotation(self, tmp_path: Path):
        db = SessionDB(db_path=tmp_path / "state.db")
        parent = "PARENT_GOAL_ROT"
        db.create_session(parent, source="cli")
        agent = _build_agent_with_db(db, parent)

        # Set a persistent goal on the parent via the real persistence path.
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
            (tmp_path / ".hermes").mkdir(exist_ok=True)
            import hermes_cli.goals as goals
            goals._DB_CACHE.clear()
            # Point the goal DB at the same state.db the agent uses.
            with patch.object(goals, "_get_session_db", return_value=db):
                goals.save_goal(parent, goals.GoalState(goal="finish the migration"))

                agent._compress_context(_msgs(), "sys", approx_tokens=120_000)
                child = agent.session_id
                assert child != parent  # rotation happened

                migrated = goals.load_goal(child)
                assert migrated is not None
                assert migrated.goal == "finish the migration"
            goals._DB_CACHE.clear()


class TestOrphanRollbackOnCreateFailure:
    def test_rolls_back_transcript_and_compressor_state_when_child_create_fails(
        self, tmp_path: Path
    ):
        db_path = tmp_path / "state.db"
        db = SessionDB(db_path=db_path)
        parent = "PARENT_ORPHAN_ROT"
        db.create_session(parent, source="cli")
        agent = _build_agent_with_db(db, parent)
        route = {
            "session_id": parent,
            "provider": "openrouter",
            "model": "test/model",
            "tier": "MEDIUM",
        }
        setattr(agent, "_smart_routing_runtime", route)
        getattr(agent, "_session_init_model_config")["smart_routing_runtime"] = dict(route)
        original = _msgs(8)

        compressor = agent.context_compressor
        compressor._previous_summary = "prior durable summary"
        compressor.compression_count = 4
        compressor.summary_generation = 3
        db.update_compaction_state(
            parent, compaction_count=4, summary_generation=3
        )
        setattr(agent, "_cached_system_prompt", "cached prompt before rotation")
        memory_manager = MagicMock()
        memory_manager.build_system_prompt.return_value = None
        setattr(agent, "_memory_manager", memory_manager)
        setattr(agent, "event_callback", MagicMock())

        def _compress(*_args, **_kwargs):
            compressor._previous_summary = "new uncommitted summary"
            compressor.compression_count = 5
            compressor.summary_generation = 4
            return [
                {"role": "user", "content": "[CONTEXT COMPACTION] uncommitted"},
                {"role": "assistant", "content": "recent reply"},
            ]

        compressor.compress.side_effect = _compress

        # commit_memory_session() ends the old compressor lifecycle before the
        # child row is written. Model the real reset so this regression catches
        # failure to restore the pre-compression state.
        def _reset_on_end(*_args, **_kwargs):
            compressor._previous_summary = None
            compressor.compression_count = 0
            compressor.summary_generation = 0
            compressor._last_compress_aborted = False

        compressor.on_session_end.side_effect = _reset_on_end

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated child create failure")

        with patch.object(db, "create_session", side_effect=_boom):
            returned, returned_prompt = agent._compress_context(
                original, "sys", approx_tokens=120_000
            )

        # No durable boundary committed: live transcript and compressor state
        # must still describe the reopened parent exactly.
        assert returned is original
        assert returned_prompt == "cached prompt before rotation"
        assert getattr(agent, "_cached_system_prompt") == (
            "cached prompt before rotation"
        )
        assert agent.session_id == parent
        assert getattr(agent, "_smart_routing_runtime")["session_id"] == parent
        assert db.get_session(parent) is not None
        assert db.get_session(parent)["end_reason"] is None
        assert compressor._previous_summary == "prior durable summary"
        assert compressor.compression_count == 4
        assert compressor.summary_generation == 3
        assert compressor._last_compress_aborted is True
        assert "rotation persistence failed" in compressor._last_summary_error

        durable_parent = db.get_session(parent)
        assert durable_parent is not None
        assert durable_parent["compaction_count"] == 4
        assert durable_parent["summary_generation"] == 3
        active_contents = [
            message["content"]
            for message in db.get_messages_as_conversation(parent)
        ]
        assert active_contents == [f"m{i}" for i in range(8)]
        compressor.on_session_start.assert_not_called()
        getattr(agent, "_memory_manager").on_session_switch.assert_not_called()
        getattr(agent, "event_callback").assert_not_called()

        child_count = db._conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE parent_session_id = ?", (parent,)
        ).fetchone()[0]
        assert child_count == 0

        # Reopen the DB to prove the parent remains the only resumable session.
        db.close()
        reopened = SessionDB(db_path=db_path)
        assert reopened.get_session(parent)["end_reason"] is None
        assert reopened._conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE parent_session_id = ?", (parent,)
        ).fetchone()[0] == 0
        reopened.close()


class TestPlatformForwardedAtBoundary:
    def test_on_session_start_receives_platform(self, tmp_path: Path):
        db = SessionDB(db_path=tmp_path / "state.db")
        parent = "PARENT_PLATFORM_ROT"
        db.create_session(parent, source="telegram")
        agent = _build_agent_with_db(db, parent, platform="telegram")

        agent._compress_context(_msgs(), "sys", approx_tokens=120_000)

        # The boundary notify must forward the platform so context-engine
        # plugins don't fall back to source=unknown (#27633).
        calls = [c for c in agent.context_compressor.on_session_start.call_args_list]
        assert calls, "on_session_start was not called at the boundary"
        kwargs = calls[-1].kwargs
        assert kwargs.get("platform") == "telegram"
        assert kwargs.get("boundary_reason") == "compression"
