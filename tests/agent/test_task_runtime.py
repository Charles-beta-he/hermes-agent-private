from __future__ import annotations

import json

import pytest

from agent.task_runtime import TaskRuntimeStore


SPEC = {
    "spec_id": "spec-1",
    "required_clauses": [{"id": "R1", "text": "preserve original source"}],
    "negative_requirements": [{"id": "N1", "text": "reject stale results"}],
    "closure_criteria": ["R1", "N1"],
}


def _slice(slice_id: str, *, dependencies: list[str] | None = None, owned_paths: list[str] | None = None):
    return {
        "slice_id": slice_id,
        "role": "implementer",
        "objective": f"implement {slice_id}",
        "dependencies": dependencies or [],
        "required_clauses": ["R1"],
        "negative_requirements": ["N1"],
        "source_refs": ["S-U1"],
        "architecture_invariants": ["single integration owner"],
        "owned_paths": owned_paths or [f"src/{slice_id}.py"],
        "forbidden_changes": ["config.yaml"],
        "verification_commands": [f"pytest -q tests/{slice_id}.py"],
        "authority": {"side_effect_ceiling": "local_reversible"},
    }


def _result(packet: dict, *, candidate: str, changed: list[str] | None = None):
    return {
        "slice_id": packet["slice_id"],
        "input_digest": packet["packet_digest"],
        "claims": ["R1", "N1"],
        "changed_artifacts": changed or list(packet["owned_paths"]),
        "evidence_refs": [{"kind": "test", "ref": "pytest:passed"}],
        "verification_results": [{"command": packet["verification_commands"][0], "exit_code": 0}],
        "risks": [],
        "unknowns": [],
        "state_delta_proposal": {"slice_status": "completed"},
        "candidate_digest": candidate,
    }


def _ready_store(tmp_path) -> tuple[TaskRuntimeStore, str]:
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")
    task = store.create_task(
        title="task-centric runtime",
        original_sources=[
            {"source_id": "S-U1", "kind": "user_message", "content": "loop推进，直至完成", "coordinates": {"turn": 1}}
        ],
    )
    task_id = task["task_id"]
    store.freeze_spec(task_id, SPEC, expected_state_revision=task["state_revision"])
    current = store.get_task(task_id)
    store.set_plan(task_id, [_slice("s1"), _slice("s2", dependencies=["s1"])], expected_state_revision=current["state_revision"])
    return store, task_id


def test_create_persists_original_source_and_cross_entrypoint_binding(tmp_path):
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")
    task = store.create_task(
        title="cross entrypoint",
        original_sources=[{"source_id": "S1", "kind": "user_message", "content": "verbatim source"}],
    )
    task_id = task["task_id"]

    store.bind_entrypoint(task_id, entrypoint="cli", session_id="cli-session")
    store.bind_entrypoint(task_id, entrypoint="telegram", session_id="telegram:42:7")

    assert store.resolve_entrypoint("cli", "cli-session")["task_id"] == task_id
    assert store.resolve_entrypoint("telegram", "telegram:42:7")["task_id"] == task_id
    reopened = TaskRuntimeStore(tmp_path / "task-runtime.db")
    restored = reopened.get_task(task_id)
    assert restored["original_sources"][0]["content"] == "verbatim source"
    assert restored["spec_revision"] == 0
    assert restored["plan_revision"] == 0


def test_context_packet_is_canonical_immutable_and_dependency_gated(tmp_path):
    store, task_id = _ready_store(tmp_path)

    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0)
    assert packet["slice_id"] == "s1"
    assert packet["task_id"] == task_id
    assert packet["spec_revision"] == 1
    assert packet["plan_revision"] == 1
    assert packet["packet_digest"].startswith("sha256:")
    assert store.verify_context_packet(packet) is True

    tampered = json.loads(json.dumps(packet))
    tampered["objective"] = "changed"
    assert store.verify_context_packet(tampered) is False

    # s2 cannot be claimed until s1 is integrated.
    assert store.claim_next_slice(task_id, worker_session_id="worker-b", lease_seconds=60, now=101.0) is None


def test_stale_revision_result_is_rejected_after_spec_change(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0)

    current = store.get_task(task_id)
    revised = dict(SPEC)
    revised["required_clauses"] = SPEC["required_clauses"] + [{"id": "R2", "text": "new requirement"}]
    store.freeze_spec(task_id, revised, expected_state_revision=current["state_revision"])

    disposition = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:old"),
        now=110.0,
    )
    assert disposition["disposition"] == "rejected"
    assert "stale_spec_revision" in disposition["reasons"]


def test_lease_owner_expiry_idempotency_and_owned_path_enforcement(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=10, now=100.0)

    wrong_owner = store.submit_result(
        task_id,
        worker_session_id="worker-b",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:c1"),
        now=105.0,
    )
    assert wrong_owner["disposition"] == "rejected"
    assert "lease_owner_mismatch" in wrong_owner["reasons"]

    expired = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:c1"),
        now=111.0,
    )
    assert expired["disposition"] == "rejected"
    assert "lease_expired" in expired["reasons"]

    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=20, now=112.0)
    outside_write_set = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:c2", changed=["config.yaml"]),
        now=113.0,
    )
    assert outside_write_set["disposition"] == "rejected"
    assert "changed_artifact_outside_owned_paths" in outside_write_set["reasons"]

    accepted = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:c2"),
        now=114.0,
    )
    duplicate = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:c2"),
        now=115.0,
    )
    assert accepted["disposition"] == "accepted_for_integration"
    assert duplicate["result_id"] == accepted["result_id"]
    assert duplicate["idempotent_replay"] is True


def test_deterministic_integration_unlocks_dependency_and_independent_closure(tmp_path):
    store, task_id = _ready_store(tmp_path)
    p1 = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0)
    r1 = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=p1["packet_id"],
        result=_result(p1, candidate="sha256:candidate-1"),
        now=101.0,
    )
    integrated1 = store.integrate_result(task_id, r1["result_id"], integrator_session_id="integrator")
    assert integrated1["slice_status"] == "completed"

    p2 = store.claim_next_slice(task_id, worker_session_id="worker-b", lease_seconds=60, now=102.0)
    assert p2["slice_id"] == "s2"
    r2 = store.submit_result(
        task_id,
        worker_session_id="worker-b",
        packet_id=p2["packet_id"],
        result=_result(p2, candidate="sha256:candidate-final"),
        now=103.0,
    )
    store.integrate_result(task_id, r2["result_id"], integrator_session_id="integrator")

    with pytest.raises(ValueError, match="candidate_digest_mismatch"):
        store.close_task(
            task_id,
            candidate_digest="sha256:wrong",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "pass"},
        )
    with pytest.raises(ValueError, match="closure_verifier_not_independent"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="worker-a",
            claims={"R1": "pass", "N1": "pass"},
        )
    with pytest.raises(ValueError, match="closure_claims_incomplete"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass"},
        )

    closed = store.close_task(
        task_id,
        candidate_digest="sha256:candidate-final",
        verifier_session_id="closure-fresh",
        claims={"R1": "pass", "N1": "pass"},
    )
    assert closed["status"] == "closed"
    assert closed["closure"]["candidate_digest"] == "sha256:candidate-final"
    assert closed["closure"]["verifier_session_id"] == "closure-fresh"
