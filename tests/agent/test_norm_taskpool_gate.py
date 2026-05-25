from __future__ import annotations

from agent.norm_governance import NormGovernanceStore, normalize_resource_key
from agent.norm_taskpool_gate import evaluate_completion_gate


def test_completion_gate_blocks_stale_norm_snapshot(tmp_path):
    db = tmp_path / "norms.db"
    store = NormGovernanceStore(db)
    store.upsert_norm(key="global:evidence", content="v1", expected_revision=0)
    snapshot = store.create_snapshot(scopes=["global"])
    store.upsert_norm(key="global:evidence", content="v2", expected_revision=1)

    result = evaluate_completion_gate(
        task_id="t1",
        norm_snapshot_hash=snapshot["snapshot_hash"],
        write_set=[],
        owner_session_id="session-a",
        store=store,
    )

    assert result.allowed is False
    assert result.reason == "norm_snapshot_stale"
    assert result.remediation == "refresh_norm_snapshot"
    assert result.details["snapshot_hash"] == snapshot["snapshot_hash"]


def test_completion_gate_blocks_active_write_conflict_without_fusion(tmp_path):
    db = tmp_path / "norms.db"
    store = NormGovernanceStore(db)
    snapshot = store.create_snapshot(scopes=["global"])
    resource = normalize_resource_key("file", "/repo/shared.py")
    store.acquire_lease(resource_key=resource, owner_session_id="session-b", task_id="t2", ttl_seconds=60)

    result = evaluate_completion_gate(
        task_id="t1",
        norm_snapshot_hash=snapshot["snapshot_hash"],
        write_set=[resource],
        owner_session_id="session-a",
        store=store,
    )

    assert result.allowed is False
    assert result.reason == "active_write_conflict"
    assert result.remediation == "create_fusion_record"
    assert result.details["conflicts"][0]["resource_key"] == resource
    assert result.details["conflicts"][0]["active_owner_session_id"] == "session-b"


def test_completion_gate_allows_active_writer_named_by_fusion_record(tmp_path):
    db = tmp_path / "norms.db"
    store = NormGovernanceStore(db)
    snapshot = store.create_snapshot(scopes=["global"])
    resource = normalize_resource_key("file", "/repo/shared.py")
    store.acquire_lease(resource_key=resource, owner_session_id="session-b", task_id="t2", ttl_seconds=60)
    fusion = store.create_fusion_record(
        parent_task_ids=["t1", "t2"],
        write_sets=[
            {"session_id": "session-a", "task_id": "t1", "resources": [resource]},
            {"session_id": "session-b", "task_id": "t2", "resources": [resource]},
        ],
        norm_revisions=snapshot["revision_vector"],
        evidence_refs=[{"kind": "test", "ref": "pytest"}],
        acceptance_criteria=["single active writer completes merged work"],
        active_writer="session-a",
    )

    result = evaluate_completion_gate(
        task_id="t1",
        norm_snapshot_hash=snapshot["snapshot_hash"],
        write_set=[resource],
        owner_session_id="session-a",
        fusion_id=fusion["fusion_id"],
        store=store,
    )

    assert result.allowed is True
    assert result.reason == "ok"
    assert result.details["fusion_id"] == fusion["fusion_id"]


def test_cleanup_expired_leases_removes_only_expired(tmp_path):
    db = tmp_path / "norms.db"
    store = NormGovernanceStore(db)
    expired = normalize_resource_key("file", "/repo/expired.py")
    active = normalize_resource_key("file", "/repo/active.py")
    store.acquire_lease(resource_key=expired, owner_session_id="session-a", ttl_seconds=1, now=100)
    store.acquire_lease(resource_key=active, owner_session_id="session-b", ttl_seconds=100, now=100)

    removed = store.cleanup_expired_leases(now=102)

    assert removed == 1
    assert [lease["resource_key"] for lease in store.active_leases(now=102)] == [active]
