from __future__ import annotations

import json
import sqlite3
import threading

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


def _ready_store(tmp_path, *, owner_profile: str | None = None) -> tuple[TaskRuntimeStore, str]:
    store = TaskRuntimeStore(tmp_path / "task-runtime.db", owner_profile=owner_profile)
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


def test_binding_replay_is_idempotent_and_cross_task_rebind_is_rejected(tmp_path):
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")
    first = store.create_task(
        title="first",
        original_sources=[{"source_id": "S1", "kind": "user_message", "content": "first"}],
    )
    second = store.create_task(
        title="second",
        original_sources=[{"source_id": "S2", "kind": "user_message", "content": "second"}],
    )

    bound = store.bind_entrypoint(first["task_id"], entrypoint="telegram", session_id="tg:42:7")
    replay = store.bind_entrypoint(first["task_id"], entrypoint="telegram", session_id="tg:42:7")

    assert replay["state_revision"] == bound["state_revision"]
    with pytest.raises(ValueError, match="entrypoint_already_bound"):
        store.bind_entrypoint(second["task_id"], entrypoint="telegram", session_id="tg:42:7")
    resolved = store.resolve_entrypoint("telegram", "tg:42:7")
    assert resolved is not None and resolved["task_id"] == first["task_id"]


def test_default_path_is_profile_scoped(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile-a"
    monkeypatch.delenv("HERMES_TASK_RUNTIME_DB", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    assert TaskRuntimeStore.default_path() == (
        profile_home / "state" / "task-runtime" / "task-runtime.db"
    )


def test_explicit_runtime_db_overrides_profile_home(monkeypatch, tmp_path):
    explicit = tmp_path / "shared-for-test.db"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-a"))
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(explicit))

    assert TaskRuntimeStore.default_path() == explicit


def test_legacy_task_owner_is_migrated_to_opening_profile(tmp_path):
    path = tmp_path / "legacy-owner.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                schema_name TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                original_sources_json TEXT NOT NULL,
                source_events_json TEXT NOT NULL,
                spec_bundle_json TEXT NOT NULL,
                implementation_graph_json TEXT NOT NULL,
                evidence_graph_json TEXT NOT NULL,
                closure_json TEXT NOT NULL,
                spec_revision INTEGER NOT NULL,
                plan_revision INTEGER NOT NULL,
                state_revision INTEGER NOT NULL,
                candidate_digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO tasks VALUES (
                'legacy-task', 'hermes.task-record.v1', 'legacy', 'draft',
                '[]', '[]', '{}', '[]', '[]', '{}', 0, 0, 1, '', 1.0, 1.0
            );
            """
        )

    migrated = TaskRuntimeStore(path, owner_profile="profile-a")
    task = migrated.get_task("legacy-task")
    assert task["owner_profile"] == "profile-a"
    assert task["state_events"][0]["event_type"] == "legacy_projection_anchored"

    with pytest.raises(PermissionError, match="task_owner_profile_mismatch"):
        TaskRuntimeStore(path, owner_profile="profile-b").get_task("legacy-task")


def test_task_owner_profile_is_persisted_and_enforced_even_on_shared_db(tmp_path):
    path = tmp_path / "shared.db"
    owner_store = TaskRuntimeStore(path, owner_profile="profile-a")
    task = owner_store.create_task(
        title="owned",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    assert task["owner_profile"] == "profile-a"

    other_store = TaskRuntimeStore(path, owner_profile="profile-b")
    with pytest.raises(PermissionError, match="task_owner_profile_mismatch"):
        other_store.get_task(task["task_id"])
    with pytest.raises(PermissionError, match="task_owner_profile_mismatch"):
        other_store.bind_entrypoint(
            task["task_id"], entrypoint="cli", session_id="cli:foreign"
        )
    assert owner_store.get_task(task["task_id"])["bindings"] == []


def test_cross_profile_exact_result_replay_checks_owner_before_idempotency(tmp_path):
    owner_store, task_id = _ready_store(tmp_path, owner_profile="profile-a")
    packet = owner_store.claim_next_slice(
        task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0
    )
    assert packet is not None
    result = _result(packet, candidate="sha256:owned")
    accepted = owner_store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=result,
        now=101.0,
    )
    assert accepted["disposition"] == "accepted_for_integration"

    before_replay = owner_store.get_task(task_id)
    replay = owner_store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=result,
        now=102.0,
    )
    assert replay["idempotent_replay"] is True
    after_replay = owner_store.get_task(task_id)
    assert after_replay["state_revision"] == before_replay["state_revision"]
    assert after_replay["state_events"] == before_replay["state_events"]

    foreign_store = TaskRuntimeStore(
        tmp_path / "task-runtime.db", owner_profile="profile-b"
    )
    with pytest.raises(PermissionError, match="task_owner_profile_mismatch"):
        foreign_store.submit_result(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            result=result,
            now=103.0,
        )


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


def test_plan_must_cover_frozen_claims_sources_and_verification_contract(tmp_path):
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")
    task = store.create_task(
        title="plan contract",
        original_sources=[{"source_id": "S-U1", "content": "source"}],
    )
    task = store.freeze_spec(
        task["task_id"], SPEC, expected_state_revision=task["state_revision"]
    )
    valid = _slice("s1")
    invalid_cases = [
        (dict(valid, required_clauses=["R-UNKNOWN"]), "slice_claim_not_in_frozen_spec"),
        (
            dict(valid, negative_requirements=[]),
            "plan_closure_criteria_unassigned",
        ),
        (dict(valid, source_refs=["S-UNKNOWN"]), "slice_source_ref_unknown"),
        (dict(valid, verification_commands=[]), "slice_verification_commands_required"),
        (
            dict(valid, forbidden_changes=["src/s1.py"]),
            "slice_owned_and_forbidden_paths_overlap",
        ),
    ]
    for definition, expected_error in invalid_cases:
        with pytest.raises(ValueError, match=expected_error):
            store.set_plan(
                task["task_id"],
                [definition],
                expected_state_revision=task["state_revision"],
            )
    unchanged = store.get_task(task["task_id"])
    assert unchanged["state_revision"] == task["state_revision"]
    assert unchanged["plan_revision"] == 0
    assert unchanged["slices"] == []


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

    before_rejection = store.get_task(task_id)
    disposition = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:old"),
        now=110.0,
    )
    assert disposition["disposition"] == "rejected"
    assert "stale_spec_revision" in disposition["reasons"]
    after_rejection = store.get_task(task_id)
    assert after_rejection["state_revision"] == before_rejection["state_revision"] + 1
    assert after_rejection["state_events"][-1]["event_type"] == "worker_result_submitted"
    assert after_rejection["state_events"][-1]["payload"]["disposition"] == "rejected"


def test_accepted_result_cannot_integrate_after_spec_or_plan_change(tmp_path):
    for invalidation in ("spec", "plan"):
        case_dir = tmp_path / invalidation
        case_dir.mkdir()
        store, task_id = _ready_store(case_dir)
        packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0)
        assert packet is not None
        accepted = store.submit_result(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            result=_result(packet, candidate="sha256:old"),
            now=101.0,
        )
        assert accepted["disposition"] == "accepted_for_integration"

        current = store.get_task(task_id)
        if invalidation == "spec":
            revised = dict(SPEC)
            revised["required_clauses"] = SPEC["required_clauses"] + [{"id": "R2", "text": "new"}]
            store.freeze_spec(task_id, revised, expected_state_revision=current["state_revision"])
        else:
            store.set_plan(
                task_id,
                [_slice("s1"), _slice("s2", dependencies=["s1"])],
                expected_state_revision=current["state_revision"],
            )

        with pytest.raises(ValueError, match="result_not_accepted_for_integration"):
            store.integrate_result(task_id, accepted["result_id"], integrator_session_id="integrator")



def test_lease_owner_expiry_idempotency_and_owned_path_enforcement(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=10, now=100.0)
    assert packet is not None

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
    before_duplicate = store.get_task(task_id)
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
    after_duplicate = store.get_task(task_id)
    assert after_duplicate["state_revision"] == before_duplicate["state_revision"]
    assert after_duplicate["state_events"] == before_duplicate["state_events"]

    conflicting = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:different"),
        now=116.0,
    )
    assert conflicting["disposition"] == "rejected"
    assert "conflicting_result_for_packet" in conflicting["reasons"]
    assert store.get_task(task_id)["slices"][0]["result_id"] == accepted["result_id"]


def test_worker_result_schema_and_success_evidence_fail_closed(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(
        task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0
    )
    assert packet is not None
    cases = [
        ({"claims": "R1"}, "worker_result_invalid_field_types"),
        ({"claims": ["R1"]}, "worker_claims_incomplete"),
        ({"evidence_refs": []}, "worker_evidence_refs_invalid"),
        (
            {"verification_results": [{"command": "pytest -q wrong.py", "exit_code": 0}]},
            "worker_verification_incomplete",
        ),
        (
            {
                "verification_results": [
                    {"command": packet["verification_commands"][0], "exit_code": 1}
                ]
            },
            "worker_verification_failed",
        ),
        ({"state_delta_proposal": {"slice_status": "running"}}, "worker_state_delta_invalid"),
        ({"candidate_digest": ""}, "candidate_digest_required"),
    ]
    for index, (mutation, expected_reason) in enumerate(cases, start=1):
        result = _result(packet, candidate=f"sha256:case-{index}")
        result.update(mutation)
        receipt = store.submit_result(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            result=result,
            now=100.0 + index,
        )
        assert receipt["disposition"] == "rejected"
        assert expected_reason in receipt["reasons"]

    assert store.get_task(task_id)["slices"][0]["status"] == "claimed"


def test_lease_reclaim_rotates_fence_and_rejects_stale_owner(tmp_path):
    store, task_id = _ready_store(tmp_path)
    first = store.claim_next_slice(task_id, worker_session_id="worker-old", lease_seconds=10, now=100.0)
    assert first is not None
    second = store.claim_next_slice(task_id, worker_session_id="worker-new", lease_seconds=10, now=111.0)
    assert second is not None
    assert second["packet_id"] != first["packet_id"]
    assert second["lease"]["token"] != first["lease"]["token"]
    assert second["lease"]["owner"] == "worker-new"
    reclaim_event = store.get_task(task_id)["state_events"][-1]
    assert reclaim_event["event_type"] == "slice_claimed"
    assert reclaim_event["payload"]["expired_slice_ids"] == [first["slice_id"]]

    stale = store.submit_result(
        task_id,
        worker_session_id="worker-old",
        packet_id=first["packet_id"],
        result=_result(first, candidate="sha256:stale"),
        now=112.0,
    )
    assert stale["disposition"] == "rejected"
    assert "lease_expired" in stale["reasons"]
    with pytest.raises(ValueError, match="packet_not_active"):
        store.release_claim(
            task_id,
            worker_session_id="worker-old",
            packet_id=first["packet_id"],
            reason="stale worker",
        )

    accepted = store.submit_result(
        task_id,
        worker_session_id="worker-new",
        packet_id=second["packet_id"],
        result=_result(second, candidate="sha256:fresh"),
        now=112.0,
    )
    assert accepted["disposition"] == "accepted_for_integration"


def test_stored_context_packet_tamper_is_rejected(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0)
    assert packet is not None
    tampered = dict(packet)
    tampered["objective"] = "maliciously changed after packet freeze"
    with store._write() as conn:
        conn.execute(
            "UPDATE context_packets SET payload_json = ? WHERE packet_id = ?",
            (json.dumps(tampered, sort_keys=True, separators=(",", ":")), packet["packet_id"]),
        )

    receipt = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:tamper"),
        now=101.0,
    )
    assert receipt["disposition"] == "rejected"
    assert "stored_packet_digest_invalid" in receipt["reasons"]


def test_concurrent_claims_have_one_lease_owner(tmp_path):
    store, task_id = _ready_store(tmp_path)
    barrier = threading.Barrier(3)
    packets = []
    errors = []

    def claim(worker):
        try:
            barrier.wait(timeout=5)
            packets.append(store.claim_next_slice(task_id, worker_session_id=worker, lease_seconds=60))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=claim, args=(f"worker-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert errors == []
    claimed = [packet for packet in packets if packet is not None]
    assert len(claimed) == 1
    assert claimed[0]["lease"]["owner"] in {"worker-0", "worker-1"}


def test_release_claim_is_owner_and_fence_guarded(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60)
    with pytest.raises(ValueError, match="lease_owner_mismatch"):
        store.release_claim(
            task_id,
            worker_session_id="worker-b",
            packet_id=packet["packet_id"],
            reason="wrong owner",
        )
    assert store.get_task(task_id)["slices"][0]["status"] == "claimed"
    receipt = store.release_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        reason="worker failed",
    )
    assert receipt["status"] == "abandoned"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"
    with pytest.raises(ValueError, match="packet_not_active"):
        store.release_claim(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            reason="stale replay",
        )


def test_spec_change_invalidates_integrated_outputs_and_downstream_state(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0)
    accepted = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        result=_result(packet, candidate="sha256:old"),
        now=101.0,
    )
    store.integrate_result(task_id, accepted["result_id"], integrator_session_id="integrator")
    before = store.get_task(task_id)
    assert before["slices"][0]["status"] == "completed"
    assert before["candidate_digest"] == "sha256:old"

    changed = store.freeze_spec(
        task_id,
        SPEC,
        expected_state_revision=before["state_revision"],
    )
    assert [item["status"] for item in changed["slices"]] == ["pending", "pending"]
    assert changed["candidate_digest"] == ""
    assert changed["evidence_graph"] == []
    assert changed["closure"] == {}
    assert store.claim_next_slice(task_id, worker_session_id="worker-b", lease_seconds=60)["slice_id"] == "s1"


def test_deterministic_integration_unlocks_dependency_and_independent_closure(tmp_path):
    store, task_id = _ready_store(tmp_path)
    rejected_packet = store.claim_next_slice(
        task_id, worker_session_id="worker-rejected", lease_seconds=60, now=90.0
    )
    assert rejected_packet is not None
    rejected_payload = _result(rejected_packet, candidate="sha256:rejected")
    rejected_payload["input_digest"] = "sha256:wrong"
    rejected = store.submit_result(
        task_id,
        worker_session_id="worker-rejected",
        packet_id=rejected_packet["packet_id"],
        result=rejected_payload,
        now=91.0,
    )
    assert rejected["disposition"] == "rejected"
    store.release_claim(
        task_id,
        worker_session_id="worker-rejected",
        packet_id=rejected_packet["packet_id"],
        reason="rejected_attempt_ended",
    )

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

    closure_packet = store.build_closure_packet(
        task_id, verifier_session_id="closure-fresh"
    )
    assert closure_packet["candidate_digest"] == "sha256:candidate-final"
    assert closure_packet["packet_digest"].startswith("sha256:")

    with pytest.raises(ValueError, match="candidate_digest_mismatch"):
        store.close_task(
            task_id,
            candidate_digest="sha256:wrong",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[
                {"ref": "review", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
        )
    with pytest.raises(ValueError, match="closure_verifier_not_independent"):
        store.build_closure_packet(task_id, verifier_session_id="worker-a")
    with pytest.raises(ValueError, match="closure_verifier_not_independent"):
        store.build_closure_packet(task_id, verifier_session_id="worker-rejected")
    with pytest.raises(ValueError, match="closure_claims_incomplete"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[
                {"ref": "review", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
        )
    with pytest.raises(ValueError, match="closure_evidence_required"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[],
            verification_results=[],
        )

    store.append_source_event(
        task_id,
        {
            "event_id": "closure-correction",
            "entrypoint": "cli",
            "session_id": "closure-fresh",
            "content": "fresh source event invalidates prior closure context",
        },
    )
    with pytest.raises(ValueError, match="closure_packet_stale"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[
                {"ref": "review", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
        )
    closure_packet = store.build_closure_packet(
        task_id, verifier_session_id="closure-fresh"
    )
    closed = store.close_task(
        task_id,
        candidate_digest="sha256:candidate-final",
        verifier_session_id="closure-fresh",
        claims={"R1": "pass", "N1": "pass"},
        closure_packet=closure_packet,
        evidence_refs=[
            {"ref": "review", "candidate_digest": "sha256:candidate-final"}
        ],
        verification_results=[
            {
                "command": "pytest -q",
                "exit_code": 0,
                "candidate_digest": "sha256:candidate-final",
            }
        ],
    )
    assert closed["status"] == "closed"
    assert closed["closure"]["candidate_digest"] == "sha256:candidate-final"
    assert closed["closure"]["verifier_session_id"] == "closure-fresh"

    replayed = store.close_task(
        task_id,
        candidate_digest="sha256:candidate-final",
        verifier_session_id="closure-fresh",
        claims={"R1": "pass", "N1": "pass"},
        closure_packet=closure_packet,
        evidence_refs=[
            {"ref": "review", "candidate_digest": "sha256:candidate-final"}
        ],
        verification_results=[
            {
                "command": "pytest -q",
                "exit_code": 0,
                "candidate_digest": "sha256:candidate-final",
            }
        ],
    )
    assert replayed["state_revision"] == closed["state_revision"]
    assert replayed["state_events"] == closed["state_events"]
    assert replayed["closure"]["closed_at"] == closed["closure"]["closed_at"]

    with pytest.raises(ValueError, match="task_already_closed_conflict"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "fail"},
            closure_packet=closure_packet,
            evidence_refs=[
                {"ref": "review", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
        )


def test_legacy_projection_is_anchored_once_without_fabricating_history(tmp_path):
    path = tmp_path / "legacy.db"
    store = TaskRuntimeStore(path)
    task = store.create_task(
        title="legacy",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    task_id = task["task_id"]
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER task_state_events_no_delete")
        conn.execute("DELETE FROM task_state_events WHERE task_id = ?", (task_id,))
        conn.execute(
            "UPDATE tasks SET state_revision = 7, status = 'running' WHERE task_id = ?",
            (task_id,),
        )

    migrated = TaskRuntimeStore(path).get_task(task_id)
    assert [(item["state_revision"], item["event_type"]) for item in migrated["state_events"]] == [
        (7, "legacy_projection_anchored")
    ]
    reopened = TaskRuntimeStore(path).get_task(task_id)
    assert reopened["state_events"] == migrated["state_events"]


def test_state_event_ledger_is_revision_aligned_hash_chained_and_append_only(tmp_path):
    store = TaskRuntimeStore(tmp_path / "ledger.db")
    task = store.create_task(
        title="ledger",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    task_id = task["task_id"]
    store.bind_entrypoint(task_id, entrypoint="cli", session_id="cli:ledger")
    task = store.append_source_event(
        task_id,
        {"event_id": "event-1", "content": "correction"},
    )

    events = task["state_events"]
    assert [item["state_revision"] for item in events] == list(
        range(1, task["state_revision"] + 1)
    )
    assert events[0]["event_type"] == "task_created"
    assert events[0]["previous_event_digest"] == ""
    for previous, current in zip(events, events[1:]):
        assert current["previous_event_digest"] == previous["event_digest"]

    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="task_state_events_append_only"):
            conn.execute(
                "UPDATE task_state_events SET event_type = 'tampered' WHERE task_id = ?",
                (task_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="task_state_events_append_only"):
            conn.execute("DELETE FROM task_state_events WHERE task_id = ?", (task_id,))
        conn.execute("DROP TRIGGER task_state_events_no_update")
        conn.execute(
            "UPDATE task_state_events SET payload_json = '{\"tampered\":true}' "
            "WHERE task_id = ? AND state_revision = ?",
            (task_id, task["state_revision"]),
        )
    with pytest.raises(ValueError, match="task_state_event_ledger_invalid"):
        store.get_task(task_id)


def test_state_event_ledger_binds_authoritative_task_projection(tmp_path):
    store = TaskRuntimeStore(tmp_path / "projection-ledger.db")
    task = store.create_task(
        title="projection",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    task_id = task["task_id"]

    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE tasks SET status = 'closed', candidate_digest = 'sha256:forged' "
            "WHERE task_id = ?",
            (task_id,),
        )

    with pytest.raises(ValueError, match="task_projection_digest_invalid"):
        store.get_task(task_id)
