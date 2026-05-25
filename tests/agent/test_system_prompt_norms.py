"""Tests for active-norms injection into the system prompt volatile tier.

Covers:
  (a) Happy path — norm key/revision/content excerpt in volatile output.
  (b) Fail-open on DB error — no crash, no norm block.
  (c) Empty DB — no norm block.
  (d) Cache-tier placement — norms block has _hermes_cacheable=False.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_norm_record(key: str, content: str, revision: int = 1):
    """Return a NormRecord-like frozen dataclass."""
    from agent.norm_governance import NormRecord
    return NormRecord(
        key=key,
        content=content,
        scope="global",
        source="test",
        revision=revision,
        content_hash="abc123",
        updated_at=1700000000.0,
    )


def _make_agent(**overrides) -> Any:
    """Build a minimal agent stub for build_system_prompt_parts."""
    defaults = dict(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=set(),
        _tool_use_enforcement="false",
        provider="test",
        model="test-model",
        platform="cli",
        _memory_store=None,
        _memory_enabled=False,
        _user_profile_enabled=False,
        _memory_manager=None,
        pass_session_id=False,
        session_id="test-sid",
        _norm_governance_store=None,
        inject_norms_to_prompt=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestNormInjectionHappyPath:
    """Active norms appear in the volatile tier with key, revision, content excerpt."""

    def test_single_norm_injected(self):
        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {
                "project-preflight-profile-workpad": {
                    "revision": 1,
                    "scope": "global",
                    "source": "bootstrap",
                    "content_hash": "abc123",
                    "updated_at": 1700000000.0,
                }
            }
        }
        store.get_norm.return_value = _fake_norm_record(
            key="project-preflight-profile-workpad",
            content="When a user names a concrete project/repo or asks for screenshot/UI/product/arch guidance, activate this norm.",
            revision=1,
        )

        agent = _make_agent(_norm_governance_store=store)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        volatile = parts["volatile"]
        assert "project-preflight-profile-workpad" in volatile
        assert "rev 1" in volatile
        assert "When a user names a concrete project" in volatile

    def test_multiple_norms_sorted_by_key(self):
        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {
                "beta-norm": {"revision": 2, "scope": "global", "source": "", "content_hash": "", "updated_at": 0},
                "alpha-norm": {"revision": 1, "scope": "global", "source": "", "content_hash": "", "updated_at": 0},
            }
        }
        def get_norm_side_effect(key):
            records = {
                "alpha-norm": _fake_norm_record("alpha-norm", "Alpha content here", 1),
                "beta-norm": _fake_norm_record("beta-norm", "Beta content here", 2),
            }
            return records.get(key)
        store.get_norm.side_effect = get_norm_side_effect

        agent = _make_agent(_norm_governance_store=store)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        volatile = parts["volatile"]
        alpha_pos = volatile.index("alpha-norm")
        beta_pos = volatile.index("beta-norm")
        assert alpha_pos < beta_pos, "Norms should be sorted alphabetically by key"


class TestNormInjectionFailOpen:
    """If the norm DB is unavailable or errors, prompt builds without crash."""

    def test_store_constructor_raises(self):
        agent = _make_agent(_norm_governance_store=None)  # will trigger lazy init

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"), \
             patch("agent.system_prompt.NormGovernanceStore", side_effect=RuntimeError("no DB")):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        volatile = parts["volatile"]
        assert "Active norms" not in volatile
        # Prompt still built — no crash
        assert volatile  # at least timestamp line present

    def test_build_snapshot_raises(self):
        store = MagicMock()
        store.build_snapshot.side_effect = RuntimeError("corrupt DB")
        agent = _make_agent(_norm_governance_store=store)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        assert "Active norms" not in parts["volatile"]

    def test_injection_disabled_by_config(self):
        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {"test-norm": {"revision": 1, "scope": "global", "source": "", "content_hash": "", "updated_at": 0}}
        }
        store.get_norm.return_value = _fake_norm_record("test-norm", "Some content", 1)
        agent = _make_agent(_norm_governance_store=store, inject_norms_to_prompt=False)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        assert "test-norm" not in parts["volatile"]


class TestNormInjectionEmptyDB:
    """Empty DB produces no norm block."""

    def test_no_norms_in_snapshot(self):
        store = MagicMock()
        store.build_snapshot.return_value = {"norms": {}}
        agent = _make_agent(_norm_governance_store=store)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        assert "Active norms" not in parts["volatile"]


class TestNormCacheTierPlacement:
    """The norms block lands in volatile tier (not cacheable)."""

    def test_norms_block_not_cacheable(self):
        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {
                "test-norm": {"revision": 1, "scope": "global", "source": "", "content_hash": "", "updated_at": 0}
            }
        }
        store.get_norm.return_value = _fake_norm_record("test-norm", "Test norm content for cache verification.", 1)

        agent = _make_agent(_norm_governance_store=store)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts, build_system_prompt_content_blocks
            parts = build_system_prompt_parts(agent)
            agent._cached_system_prompt_parts = dict(parts)
            agent._cached_system_prompt = "\n\n".join(
                p for p in (parts["stable"], parts["context"], parts["volatile"]) if p
            )
            blocks = build_system_prompt_content_blocks(agent)

        # Find the volatile block — it should contain the norm key and NOT be cacheable
        assert isinstance(blocks, list)
        volatile_blocks = [
            b for b in blocks
            if "test-norm" in b.get("text", "")
        ]
        assert len(volatile_blocks) == 1
        assert volatile_blocks[0]["_hermes_cacheable"] is False


class TestNormDeterminism:
    """Same norms produce identical byte output across calls."""

    def test_deterministic_output(self):
        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {
                "my-norm": {"revision": 3, "scope": "global", "source": "", "content_hash": "", "updated_at": 0}
            }
        }
        store.get_norm.return_value = _fake_norm_record("my-norm", "Deterministic content.", 3)

        def build_parts():
            agent = _make_agent(_norm_governance_store=store)
            with patch("run_agent.load_soul_md", return_value=""), \
                 patch("run_agent.build_environment_hints", return_value=""), \
                 patch("run_agent.build_context_files_prompt", return_value=""), \
                 patch("run_agent.build_nous_subscription_prompt", return_value=""), \
                 patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
                from agent.system_prompt import build_system_prompt_parts
                return build_system_prompt_parts(agent)

        out1 = build_parts()
        out2 = build_parts()
        assert out1["volatile"] == out2["volatile"], "Volatile output should be byte-identical"


class TestNormBoundsAndProjectPreflightHint:
    """Rendered norm prompt is bounded and makes project-preflight norms operational."""

    def test_project_preflight_norm_adds_explicit_operational_hint(self):
        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {
                "project-preflight-profile-workpad": {
                    "revision": 1,
                    "scope": "project",
                    "source": "test",
                    "content_hash": "",
                    "updated_at": 0,
                }
            }
        }
        store.get_norm.return_value = _fake_norm_record(
            "project-preflight-profile-workpad",
            "Named project screenshot/UI/product/architecture/test tasks require profile and Workpad preflight.",
            1,
        )
        agent = _make_agent(_norm_governance_store=store)

        with patch("run_agent.load_soul_md", return_value=""), \
             patch("run_agent.build_environment_hints", return_value=""), \
             patch("run_agent.build_context_files_prompt", return_value=""), \
             patch("run_agent.build_nous_subscription_prompt", return_value=""), \
             patch("agent.file_safety._resolve_active_profile_name", return_value="default"):
            from agent.system_prompt import build_system_prompt_parts
            parts = build_system_prompt_parts(agent)

        assert "Project preflight trigger" in parts["volatile"]
        assert "project profile" in parts["volatile"]
        assert "Workpad" in parts["volatile"]

    def test_norm_count_and_content_are_bounded(self):
        from agent.system_prompt import _format_active_norms_prompt

        store = MagicMock()
        store.build_snapshot.return_value = {
            "norms": {
                f"norm-{idx:02d}": {"revision": idx, "scope": "global", "source": "", "content_hash": "", "updated_at": 0}
                for idx in range(15)
            }
        }

        def get_norm_side_effect(key):
            return _fake_norm_record(key, "x" * 80, int(key.rsplit("-", 1)[1]))

        store.get_norm.side_effect = get_norm_side_effect
        prompt = _format_active_norms_prompt(
            _make_agent(_norm_governance_store=store),
            max_norms=3,
            max_content_chars=20,
        )

        assert "norm-00" in prompt
        assert "norm-02" in prompt
        assert "norm-03" not in prompt
        assert "12 additional norm(s) omitted" in prompt
        assert "xxxxxxxxxxxxxxxxxxx…" in prompt
