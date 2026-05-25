from __future__ import annotations

import time

import pytest

from agent.norm_governance import (
    LeaseConflict,
    NormConflict,
    NormGovernanceStore,
    normalize_resource_key,
)


def test_norm_snapshot_is_versioned_and_detects_updates_from_other_sessions(tmp_path):
    store = NormGovernanceStore(tmp_path / "norms.db")

    first = store.upsert_norm(
        key="global:evidence-first",
        content="Evidence before completion claims.",
        scope="global",
        source="test",
        expected_revision=0,
    )
    snapshot = store.build_snapshot(scopes=["global"])

    assert snapshot["schema"] == "hermes_norm_snapshot_v1"
    assert snapshot["registry_revision"] == first.revision
    assert snapshot["norms"]["global:evidence-first"]["revision"] == first.revision
    assert snapshot["norms"]["global:evidence-first"]["content_hash"] == first.content_hash

    second = store.upsert_norm(
        key="global:evidence-first",
        content="Evidence before completion claims; model agreement is not evidence.",
        scope="global",
        source="test",
        expected_revision=first.revision,
    )

    stale = store.detect_stale_snapshot(snapshot)
    assert stale == [
        {
            "key": "global:evidence-first",
            "snapshot_revision": first.revision,
            "current_revision": second.revision,
            "reason": "revision_changed",
        }
    ]


def test_norm_write_uses_compare_and_swap_to_prevent_silent_overwrite(tmp_path):
    store = NormGovernanceStore(tmp_path / "norms.db")
    first = store.upsert_norm("project:uvisa:red-lines", "Do not break H5.", expected_revision=0)

    store.upsert_norm("project:uvisa:red-lines", "Do not break H5 or PAD.", expected_revision=first.revision)

    with pytest.raises(NormConflict) as exc:
        store.upsert_norm("project:uvisa:red-lines", "Old session overwrite.", expected_revision=first.revision)

    assert exc.value.key == "project:uvisa:red-lines"
    assert exc.value.expected_revision == first.revision
    assert exc.value.current_revision == first.revision + 1


def test_resource_lease_blocks_concurrent_writers_and_allows_expiry(tmp_path):
    store = NormGovernanceStore(tmp_path / "norms.db")
    resource = normalize_resource_key("skill", "autonomous-ai-agents/hermes-agent", fragment="Pitfalls")

    first = store.acquire_lease(
        resource_key=resource,
        owner_session_id="session-a",
        task_id="task-1",
        ttl_seconds=30,
        base_hash="abc",
        intent="patch pitfalls",
        write_mode="patch",
        now=1000.0,
    )
    assert first.resource_key == "skill:autonomous-ai-agents/hermes-agent#Pitfalls"
    assert first.owner_session_id == "session-a"

    with pytest.raises(LeaseConflict) as exc:
        store.acquire_lease(
            resource_key=resource,
            owner_session_id="session-b",
            task_id="task-2",
            ttl_seconds=30,
            base_hash="abc",
            intent="patch same section",
            write_mode="patch",
            now=1005.0,
        )
    assert exc.value.resource_key == resource
    assert exc.value.active_owner_session_id == "session-a"

    renewed = store.acquire_lease(
        resource_key=resource,
        owner_session_id="session-b",
        task_id="task-2",
        ttl_seconds=30,
        base_hash="abc",
        intent="patch after expiry",
        write_mode="patch",
        now=1031.0,
    )
    assert renewed.owner_session_id == "session-b"


def test_fusion_record_preserves_union_acceptance_and_overlapping_write_sets(tmp_path):
    store = NormGovernanceStore(tmp_path / "norms.db")
    write_sets = [
        {
            "session_id": "session-a",
            "task_id": "task-a",
            "resources": ["file:/repo/a.py", "skill:hermes-agent#Pitfalls"],
        },
        {
            "session_id": "session-b",
            "task_id": "task-b",
            "resources": ["skill:hermes-agent#Pitfalls", "memory:user:preferences"],
        },
    ]

    fusion = store.create_fusion_record(
        parent_task_ids=["task-a", "task-b"],
        write_sets=write_sets,
        norm_revisions={"global:evidence-first": 3},
        evidence_refs=[{"id": "E-1"}, {"id": "E-2"}],
        acceptance_criteria=["tests pass", "skill docs updated", "tests pass"],
        active_writer="session-a",
    )

    assert fusion["schema"] == "hermes_task_fusion_record_v1"
    assert fusion["status"] == "open"
    assert fusion["active_writer"] == "session-a"
    assert fusion["acceptance_criteria"] == ["tests pass", "skill docs updated"]
    assert fusion["conflicts"] == [
        {
            "resource_key": "skill:hermes-agent#Pitfalls",
            "sessions": ["session-a", "session-b"],
            "task_ids": ["task-a", "task-b"],
            "reason": "overlapping_write_set",
        }
    ]

    loaded = store.get_fusion_record(fusion["fusion_id"])
    assert loaded == fusion
