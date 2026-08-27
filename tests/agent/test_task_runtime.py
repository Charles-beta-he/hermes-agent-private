from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

from agent.task_runtime import TaskRuntimeStore
from agent.task_runtime_process import (
    TaskCleanupBinding,
    VerifiedTaskCleanup,
    run_owned_task_process,
)


SPEC = {
    "spec_id": "spec-1",
    "required_clauses": [{"id": "R1", "text": "preserve original source"}],
    "negative_requirements": [{"id": "N1", "text": "reject stale results"}],
    "closure_criteria": ["R1", "N1"],
}


def _governed_spec(source_id: str = "S-U1") -> dict:
    return {
        "spec_id": "spec-governed-1",
        "governance": {
            "task_class": "complex",
            "source_authority": [source_id],
            "oracle_refs": [source_id],
            "source_coverage": {source_id: ["R1", "N1"]},
            "discovery": {
                "project_evidence": {
                    "status": "completed",
                    "source_refs": [source_id],
                    "decision": "current project and user source inspected",
                },
                "prototype": {
                    "status": "not_applicable",
                    "source_refs": [source_id],
                    "decision": "no prototype or reference implementation supplied",
                },
                "competitor": {
                    "status": "not_applicable",
                    "source_refs": [source_id],
                    "decision": "product direction is not being selected in this task",
                },
                "open_source": {
                    "status": "not_applicable",
                    "source_refs": [source_id],
                    "decision": "no mature component slot is introduced by this task",
                },
            },
            "blocking_unknowns": [],
        },
        "required_clauses": [
            {"id": "R1", "text": "preserve original source", "source_refs": [source_id]}
        ],
        "negative_requirements": [
            {"id": "N1", "text": "reject stale results", "source_refs": [source_id]}
        ],
        "closure_criteria": ["R1", "N1"],
    }


SPEC = _governed_spec()


def _review_and_freeze(
    store: TaskRuntimeStore,
    task_id: str,
    spec: dict,
    *,
    expected_state_revision: int,
    reviewer_session_id: str = "spec-reviewer-test",
    compiler_session_id: str = "spec-compiler-test",
) -> dict:
    review = store.record_spec_review(
        task_id,
        spec,
        reviewer_session_id=reviewer_session_id,
        verdict="approved",
        findings=[],
        expected_state_revision=expected_state_revision,
    )
    return store.freeze_spec(
        task_id,
        spec,
        compiler_session_id=compiler_session_id,
        expected_state_revision=review["state_revision"],
    )


def _spec_with_extra_clause(clause_id: str, text: str) -> dict:
    spec = json.loads(json.dumps(SPEC))
    spec["required_clauses"].append(
        {"id": clause_id, "text": text, "source_refs": ["S-U1"]}
    )
    spec["closure_criteria"].append(clause_id)
    spec["governance"]["source_coverage"]["S-U1"].append(clause_id)
    return spec


def _spec_for_sources(*source_ids: str) -> dict:
    spec = _governed_spec(source_ids[0])
    spec["governance"]["source_authority"] = list(source_ids)
    spec["governance"]["source_coverage"] = {
        source_id: ["R1", "N1"] for source_id in source_ids
    }
    spec["governance"]["discovery"]["project_evidence"]["source_refs"] = list(
        source_ids
    )
    spec["required_clauses"][0]["source_refs"] = list(source_ids)
    spec["negative_requirements"][0]["source_refs"] = list(source_ids)
    return spec


def _termination_proof(worker: str, packet: dict) -> dict:
    return {
        "kind": "runner_not_started",
        "worker_session_id": worker,
        "packet_id": packet["packet_id"],
        "lease_token": packet["lease"]["token"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
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
    _review_and_freeze(
        store, task_id, SPEC, expected_state_revision=task["state_revision"]
    )
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


def test_legacy_planned_task_cannot_bypass_governed_spec_on_claim(tmp_path):
    path = tmp_path / "legacy-planned.db"
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
            """
        )
        conn.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-planned",
                "hermes.task-record.v1",
                "legacy planned",
                "planned",
                '[{"source_id":"S1","content":"legacy"}]',
                "[]",
                json.dumps(
                    {
                        "required_clauses": [{"id": "R1", "text": "legacy"}],
                        "negative_requirements": [
                            {"id": "N1", "text": "legacy negative"}
                        ],
                        "closure_criteria": ["R1", "N1"],
                    }
                ),
                '[{"slice_id":"s1","objective":"legacy"}]',
                "[]",
                "{}",
                1,
                1,
                1,
                "",
                1.0,
                1.0,
            ),
        )

    migrated = TaskRuntimeStore(path, owner_profile="profile-a")
    with pytest.raises(ValueError, match="spec_governance_required"):
        migrated.claim_next_slice(
            "legacy-planned", worker_session_id="worker-a", lease_seconds=60
        )


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


def test_default_hermes_home_preserves_legacy_default_owner(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(Path.home() / ".hermes"))
    assert TaskRuntimeStore(tmp_path / "default.db").owner_profile == "default"


def test_task_owner_is_derived_from_home_when_profile_env_is_absent(tmp_path, monkeypatch):
    shared = tmp_path / "shared.db"
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "alpha-home"))
    alpha = TaskRuntimeStore(shared)
    task = alpha.create_task(
        title="alpha secret",
        original_sources=[{"source_id": "S1", "content": "secret"}],
    )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "beta-home"))
    beta = TaskRuntimeStore(shared)
    assert alpha.owner_profile != beta.owner_profile
    with pytest.raises(PermissionError, match="task_owner_profile_mismatch"):
        beta.get_task(task["task_id"])


def test_same_named_profiles_under_distinct_roots_do_not_share_owner(tmp_path, monkeypatch):
    shared = tmp_path / "shared-profiles.db"
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "root-a" / "profiles" / "shared"))
    first = TaskRuntimeStore(shared)
    task = first.create_task(
        title="root-a secret",
        original_sources=[{"source_id": "S1", "content": "secret"}],
    )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "root-b" / "profiles" / "shared"))
    second = TaskRuntimeStore(shared)
    assert first.owner_profile != second.owner_profile
    with pytest.raises(PermissionError, match="task_owner_profile_mismatch"):
        second.get_task(task["task_id"])


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
    task = _review_and_freeze(
        store,
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


def test_spec_freeze_requires_evidence_bound_discovery_and_independent_review(tmp_path):
    store = TaskRuntimeStore(tmp_path / "governed-spec.db")
    task = store.create_task(
        title="governed spec",
        original_sources=[{"source_id": "S-U1", "content": "build the complete flow"}],
    )
    spec = _governed_spec()

    review = store.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="spec-reviewer-fresh",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    assert review["spec_digest"].startswith("sha256:")
    assert review["source_digest"].startswith("sha256:")
    replayed_review = store.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="spec-reviewer-fresh",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    assert replayed_review == review

    with pytest.raises(ValueError, match="spec_compiler_and_reviewer_must_be_independent"):
        store.freeze_spec(
            task["task_id"],
            spec,
            compiler_session_id="spec-reviewer-fresh",
            expected_state_revision=review["state_revision"],
        )

    frozen = store.freeze_spec(
        task["task_id"],
        spec,
        compiler_session_id="spec-compiler-fresh",
        expected_state_revision=review["state_revision"],
    )
    assert frozen["status"] == "specified"
    assert frozen["spec_reviews"][-1]["reviewer_session_id"] == "spec-reviewer-fresh"


def test_discovery_gate_fails_closed_for_missing_preflight_and_source_coverage(tmp_path):
    store = TaskRuntimeStore(tmp_path / "discovery-gate.db")
    task = store.create_task(
        title="discovery gate",
        original_sources=[{"source_id": "S-U1", "content": "build a workflow editor"}],
    )
    valid = _governed_spec()
    legacy_spec = {
        "required_clauses": [{"id": "R1", "text": "legacy"}],
        "negative_requirements": [{"id": "N1", "text": "legacy negative"}],
        "closure_criteria": ["R1", "N1"],
    }
    with pytest.raises(ValueError, match="spec_governance_required"):
        store.freeze_spec(
            task["task_id"],
            legacy_spec,
            compiler_session_id="legacy-bypass-attempt",
            expected_state_revision=task["state_revision"],
        )
    invalid_cases = [
        ({key: value for key, value in valid.items() if key != "governance"}, "spec_governance_required"),
        (
            {
                **valid,
                "governance": {**valid["governance"], "source_coverage": {}},
            },
            "spec_source_coverage_incomplete",
        ),
        (
            {
                **valid,
                "governance": {
                    **valid["governance"],
                    "task_class": "mature_capability",
                },
            },
            "open_source_preflight_required_for_task_class",
        ),
    ]
    for index, (spec, expected_error) in enumerate(invalid_cases):
        with pytest.raises(ValueError, match=expected_error):
            store.record_spec_review(
                task["task_id"],
                spec,
                reviewer_session_id=f"reviewer-{index}",
                verdict="approved",
                findings=[],
                expected_state_revision=task["state_revision"],
            )
    unchanged = store.get_task(task["task_id"])
    assert unchanged["state_revision"] == task["state_revision"]
    assert unchanged["spec_reviews"] == []


def test_governed_spec_negative_contract_matrix(tmp_path):
    store = TaskRuntimeStore(tmp_path / "governed-negative-matrix.db")
    task = store.create_task(
        title="governed negative matrix",
        original_sources=[{"source_id": "S-U1", "content": "source"}],
    )

    def changed(mutator):
        candidate = json.loads(json.dumps(_governed_spec()))
        mutator(candidate)
        return candidate

    cases = [
        (
            changed(lambda spec: spec["governance"].update(task_class="unknown")),
            "spec_task_class_invalid",
        ),
        (
            changed(lambda spec: spec.update(required_clauses=[])),
            "spec_required_clauses_required",
        ),
        (
            changed(lambda spec: spec.update(negative_requirements=[])),
            "spec_negative_requirements_required",
        ),
        (
            changed(lambda spec: spec.update(closure_criteria=["R1"])),
            "spec_closure_coverage_incomplete",
        ),
        (
            changed(
                lambda spec: spec["governance"]["discovery"]["project_evidence"].update(
                    status="not_applicable"
                )
            ),
            "project_evidence_preflight_required",
        ),
        (
            changed(lambda spec: spec["governance"].update(source_authority=[])),
            "spec_source_authority_incomplete",
        ),
        (
            changed(lambda spec: spec["governance"].update(oracle_refs=[])),
            "spec_oracle_refs_invalid",
        ),
        (
            changed(
                lambda spec: spec["governance"]["source_coverage"].update(
                    {"S-U1": ["R-UNKNOWN"]}
                )
            ),
            "spec_source_coverage_incomplete",
        ),
        (
            changed(lambda spec: spec["required_clauses"][0].update(source_refs=[])),
            "spec_claim_source_refs_invalid",
        ),
        (
            changed(lambda spec: spec["governance"]["discovery"].pop("prototype")),
            "spec_discovery_preflight_incomplete",
        ),
        (
            changed(
                lambda spec: spec["governance"].update(
                    task_class="mature_capability"
                )
            ),
            "open_source_preflight_required_for_task_class",
        ),
        (
            changed(
                lambda spec: spec["governance"].update(task_class="product_design")
            ),
            "competitor_preflight_required_for_task_class",
        ),
        (
            changed(
                lambda spec: spec["governance"].update(
                    task_class="reference_reimplementation"
                )
            ),
            "prototype_preflight_required_for_task_class",
        ),
        (
            changed(
                lambda spec: spec["governance"].update(
                    blocking_unknowns=["unresolved authority"]
                )
            ),
            "spec_blocking_unknowns_unresolved",
        ),
    ]
    for index, (spec, expected_error) in enumerate(cases):
        with pytest.raises(ValueError, match=expected_error):
            store.record_spec_review(
                task["task_id"],
                spec,
                reviewer_session_id=f"negative-reviewer-{index}",
                verdict="approved",
                findings=[],
                expected_state_revision=task["state_revision"],
            )
    unchanged = store.get_task(task["task_id"])
    assert unchanged["state_revision"] == task["state_revision"]
    assert unchanged["spec_reviews"] == []


def test_reference_reimplementation_binds_prototype_evidence_as_oracle(tmp_path):
    store = TaskRuntimeStore(tmp_path / "prototype-oracle.db")
    task = store.create_task(
        title="reference reimplementation",
        original_sources=[
            {"source_id": "S-U1", "kind": "user_message", "content": "reproduce exactly"},
            {"source_id": "S-P1", "kind": "prototype", "content": "frozen prototype"},
        ],
    )
    spec = _spec_for_sources("S-U1", "S-P1")
    spec["governance"]["task_class"] = "reference_reimplementation"
    spec["governance"]["oracle_refs"] = ["S-U1"]
    spec["governance"]["discovery"]["prototype"].update(
        status="completed", source_refs=["S-P1"]
    )

    with pytest.raises(ValueError, match="prototype_oracle_binding_required"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="prototype-reviewer-incomplete",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )

    spec["governance"]["oracle_refs"] = ["S-U1", "S-P1"]
    approved = store.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="prototype-reviewer-complete",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    assert approved["verdict"] == "approved"


def test_mature_capability_requires_complete_open_source_due_diligence(tmp_path):
    store = TaskRuntimeStore(tmp_path / "open-source-evaluation.db")
    task = store.create_task(
        title="mature capability",
        original_sources=[{"source_id": "S-U1", "kind": "user_message", "content": "editor"}],
    )
    spec = json.loads(json.dumps(SPEC))
    spec["governance"]["task_class"] = "mature_capability"
    spec["governance"]["discovery"]["open_source"].update(status="completed")

    with pytest.raises(ValueError, match="open_source_evaluation_incomplete"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="oss-reviewer-incomplete",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )

    spec["governance"]["discovery"]["open_source"]["evaluation"] = {
        "real_closure": "validated against the required end-to-end journey",
        "license": "compatible license recorded",
        "maintenance": "release and maintainer health recorded",
        "security": "threat and dependency posture recorded",
        "technical_fit": "adapter and runtime fit recorded",
        "data_ownership": "storage and export ownership recorded",
        "version_pin": "exact version and integrity pin recorded",
        "rollback": "removal and fallback path recorded",
        "lifecycle_cost": "adoption, upgrade and replacement cost recorded",
    }
    approved = store.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="oss-reviewer-complete",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    assert approved["verdict"] == "approved"


def test_spec_review_is_exact_bound_append_only_and_conflict_safe(tmp_path):
    store = TaskRuntimeStore(tmp_path / "spec-review-ledger.db")
    task = store.create_task(
        title="review ledger",
        original_sources=[{"source_id": "S-U1", "content": "source"}],
    )
    review = store.record_spec_review(
        task["task_id"],
        SPEC,
        reviewer_session_id="reviewer-ledger",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    replay = store.record_spec_review(
        task["task_id"],
        SPEC,
        reviewer_session_id="reviewer-ledger",
        verdict="approved",
        findings=[],
        expected_state_revision=review["state_revision"],
    )
    assert replay["review_id"] == review["review_id"]
    assert replay["state_revision"] == review["state_revision"]

    with pytest.raises(ValueError, match="spec_review_identity_conflict"):
        store.record_spec_review(
            task["task_id"],
            SPEC,
            reviewer_session_id="reviewer-ledger",
            verdict="rejected",
            findings=["conflicting replay"],
            expected_state_revision=review["state_revision"],
        )

    mutated = _spec_with_extra_clause("R2", "unreviewed mutation")
    with pytest.raises(ValueError, match="independent_spec_review_required"):
        store.freeze_spec(
            task["task_id"],
            mutated,
            compiler_session_id="compiler-ledger",
            expected_state_revision=review["state_revision"],
        )

    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="spec_reviews_append_only"):
            conn.execute(
                "UPDATE spec_reviews SET verdict = 'rejected' WHERE review_id = ?",
                (review["review_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="spec_reviews_append_only"):
            conn.execute(
                "DELETE FROM spec_reviews WHERE review_id = ?",
                (review["review_id"],),
            )


def test_source_coverage_edges_must_match_claim_source_refs_bidirectionally(tmp_path):
    store = TaskRuntimeStore(tmp_path / "edge-coverage.db")
    task = store.create_task(
        title="edge coverage",
        original_sources=[
            {"source_id": "S1", "kind": "user_message", "content": "one"},
            {"source_id": "S2", "kind": "project_ssot", "content": "two"},
        ],
    )
    spec = _spec_for_sources("S1", "S2")
    spec["required_clauses"][0]["source_refs"] = ["S1"]

    with pytest.raises(ValueError, match="spec_source_coverage_edge_mismatch"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="edge-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )


def test_new_source_invalidates_spec_plan_candidate_and_requires_fresh_review(tmp_path):
    store = TaskRuntimeStore(tmp_path / "source-invalidation.db")
    task = store.create_task(
        title="source invalidation",
        original_sources=[{"source_id": "S-U1", "content": "original requirement"}],
    )
    spec = _governed_spec()
    review = store.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="reviewer-before-correction",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    task = store.freeze_spec(
        task["task_id"],
        spec,
        compiler_session_id="compiler-before-correction",
        expected_state_revision=review["state_revision"],
    )
    task = store.set_plan(
        task["task_id"],
        [_slice("s1")],
        expected_state_revision=task["state_revision"],
    )

    invalidated = store.append_source_event(
        task["task_id"],
        {"event_id": "S-U2", "content": "new requirement changes the journey"},
    )
    assert invalidated["status"] == "draft"
    assert invalidated["implementation_graph"] == []
    assert invalidated["slices"] == []
    assert invalidated["candidate_digest"] == ""
    assert invalidated["evidence_graph"] == []
    assert invalidated["closure"] == {}
    assert invalidated["state_events"][-1]["payload"]["spec_invalidated"] is True

    revised = _governed_spec()
    revised["governance"]["source_authority"] = ["S-U2", "S-U1"]
    revised["governance"]["source_coverage"]["S-U2"] = ["R1"]
    revised["governance"]["discovery"]["project_evidence"]["source_refs"] = ["S-U1", "S-U2"]
    revised["required_clauses"][0]["source_refs"] = ["S-U1", "S-U2"]
    with pytest.raises(ValueError, match="independent_spec_review_required"):
        store.freeze_spec(
            task["task_id"],
            revised,
            compiler_session_id="compiler-after-correction",
            expected_state_revision=invalidated["state_revision"],
        )


def test_claim_heartbeat_is_packet_owner_and_fence_bound(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(
        task_id, worker_session_id="worker-a", lease_seconds=5, now=100.0
    )
    assert packet is not None

    heartbeat = store.heartbeat_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        lease_seconds=7,
        now=105.0,
    )
    assert heartbeat["lease_token"] == packet["lease"]["token"]
    assert heartbeat["lease_expires_at"] == 112.0
    task = store.get_task(task_id)
    assert task["slices"][0]["lease_expires_at"] == 112.0

    with pytest.raises(ValueError, match="lease_owner_mismatch"):
        store.heartbeat_claim(
            task_id,
            worker_session_id="worker-b",
            packet_id=packet["packet_id"],
            lease_seconds=7,
        )
    with pytest.raises(ValueError, match="packet_not_found"):
        store.heartbeat_claim(
            task_id,
            worker_session_id="worker-a",
            packet_id="missing-packet",
            lease_seconds=7,
        )

    store.release_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        reason="runner_not_started",
        termination_proof=_termination_proof("worker-a", packet),
    )
    with pytest.raises(ValueError, match="packet_not_active"):
        store.heartbeat_claim(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            lease_seconds=7,
        )


def test_quarantine_recovery_requires_controller_issued_packet_fenced_cleanup(tmp_path):
    store, task_id = _ready_store(tmp_path)
    packet = store.claim_next_slice(
        task_id, worker_session_id="worker-a", lease_seconds=60
    )
    assert packet is not None
    store.quarantine_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        reason="runner_exit_termination_unproven",
    )
    binding = TaskCleanupBinding(
        task_id=task_id,
        slice_id=packet["slice_id"],
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        lease_token=packet["lease"]["token"],
    )
    controlled = run_owned_task_process(
        [sys.executable, "-c", "pass"],
        binding=binding,
        cwd=tmp_path,
        timeout_seconds=2,
    )
    assert controlled.cleanup is not None

    with pytest.raises(TypeError, match="controller_issued"):
        VerifiedTaskCleanup(binding, controlled.receipt, _issuer=object())
    with pytest.raises(ValueError, match="verified_process_cleanup_required"):
        store.recover_quarantined_claim(task_id, cleanup=controlled.receipt.to_dict())

    recovered = store.recover_quarantined_claim(
        task_id, cleanup=controlled.cleanup
    )
    assert recovered["status"] == "recovered"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"
    with pytest.raises(ValueError, match="packet_not_quarantined"):
        store.recover_quarantined_claim(task_id, cleanup=controlled.cleanup)


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
    revised = _spec_with_extra_clause("R2", "new requirement")
    _review_and_freeze(
        store, task_id, revised, expected_state_revision=current["state_revision"]
    )

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
            revised = _spec_with_extra_clause("R2", "new")
            _review_and_freeze(
                store,
                task_id,
                revised,
                expected_state_revision=current["state_revision"],
            )
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
    store.release_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        reason="worker_returned_after_expiry",
        termination_proof=_termination_proof("worker-a", packet),
    )

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
            {"evidence_refs": [{"kind": "test", "ref": "pytest:passed", "authority": "self"}]},
            "worker_evidence_refs_unknown_fields",
        ),
        (
            {
                "verification_results": [
                    {
                        "command": packet["verification_commands"][0],
                        "exit_code": 0,
                        "authority_granted": True,
                    }
                ]
            },
            "worker_verification_unknown_fields",
        ),
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
        ({"authority_granted": True}, "worker_result_unknown_fields"),
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


def test_lease_expiry_quarantines_without_termination_proof(tmp_path):
    store, task_id = _ready_store(tmp_path)
    first = store.claim_next_slice(
        task_id, worker_session_id="worker-old", lease_seconds=10, now=100.0
    )
    assert first is not None

    second = store.claim_next_slice(
        task_id, worker_session_id="worker-new", lease_seconds=10, now=111.0
    )
    assert second is None
    task = store.get_task(task_id)
    assert task["slices"][0]["status"] == "quarantined"
    expiry_event = task["state_events"][-1]
    assert expiry_event["event_type"] == "lease_expired_quarantined"
    assert expiry_event["payload"]["expired_slice_ids"] == [first["slice_id"]]

    stale = store.submit_result(
        task_id,
        worker_session_id="worker-old",
        packet_id=first["packet_id"],
        result=_result(first, candidate="sha256:stale"),
        now=112.0,
    )
    assert stale["disposition"] == "rejected"
    assert "lease_expired" in stale["reasons"]
    assert any(reason.startswith("packet_not_active") for reason in stale["reasons"])


def test_abandoned_packet_cannot_submit_after_same_worker_reclaims(tmp_path):
    store, task_id = _ready_store(tmp_path)
    first = store.claim_next_slice(
        task_id, worker_session_id="worker-a", lease_seconds=60, now=100.0
    )
    assert first is not None
    store.release_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=first["packet_id"],
        reason="runner returned",
        termination_proof=_termination_proof("worker-a", first),
    )
    second = store.claim_next_slice(
        task_id, worker_session_id="worker-a", lease_seconds=60, now=101.0
    )
    assert second is not None and second["packet_id"] != first["packet_id"]

    stale = store.submit_result(
        task_id,
        worker_session_id="worker-a",
        packet_id=first["packet_id"],
        result=_result(first, candidate="sha256:stale"),
        now=102.0,
    )
    assert stale["disposition"] == "rejected"
    assert "packet_not_active:abandoned" in stale["reasons"]
    assert "current_lease_fence_mismatch" in stale["reasons"]
    current = store.get_task(task_id)["slices"][0]
    assert current["status"] == "claimed"
    assert current["lease_owner"] == "worker-a"


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

    with pytest.raises(ValueError, match="task_projection_digest_invalid"):
        store.submit_result(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            result=_result(packet, candidate="sha256:tamper"),
            now=101.0,
        )
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM worker_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0


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
    assert packet is not None
    with pytest.raises(ValueError, match="termination_proof_required"):
        store.release_claim(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            reason="unproven return",
        )
    assert store.get_task(task_id)["slices"][0]["status"] == "claimed"
    bad_proofs = [
        ({**_termination_proof("worker-a", packet), "authority": "self_asserted"}, "termination_proof_unknown_fields"),
        ({**_termination_proof("worker-a", packet), "kind": "lease_expired"}, "termination_proof_kind_invalid"),
        (_termination_proof("worker-b", packet), "termination_proof_worker_mismatch"),
        ({**_termination_proof("worker-a", packet), "packet_id": "wrong"}, "termination_proof_packet_mismatch"),
        ({**_termination_proof("worker-a", packet), "observed_at": "not-a-date"}, "termination_proof_observed_at_invalid"),
        ({**_termination_proof("worker-a", packet), "lease_token": "wrong"}, "termination_proof_lease_fence_mismatch"),
    ]
    for proof, expected_error in bad_proofs:
        with pytest.raises(ValueError, match=expected_error):
            store.release_claim(
                task_id,
                worker_session_id="worker-a",
                packet_id=packet["packet_id"],
                reason="invalid proof",
                termination_proof=proof,
            )
    assert store.get_task(task_id)["slices"][0]["status"] == "claimed"
    with pytest.raises(ValueError, match="lease_owner_mismatch"):
        store.release_claim(
            task_id,
            worker_session_id="worker-b",
            packet_id=packet["packet_id"],
            reason="wrong owner",
            termination_proof=_termination_proof("worker-b", packet),
        )
    assert store.get_task(task_id)["slices"][0]["status"] == "claimed"
    receipt = store.release_claim(
        task_id,
        worker_session_id="worker-a",
        packet_id=packet["packet_id"],
        reason="worker failed",
        termination_proof=_termination_proof("worker-a", packet),
    )
    assert receipt["status"] == "abandoned"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"
    with pytest.raises(ValueError, match="packet_not_active"):
        store.release_claim(
            task_id,
            worker_session_id="worker-a",
            packet_id=packet["packet_id"],
            reason="stale replay",
            termination_proof=_termination_proof("worker-a", packet),
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

    changed = _review_and_freeze(
        store,
        task_id,
        SPEC,
        expected_state_revision=before["state_revision"],
    )
    assert changed["slices"] == []
    assert changed["implementation_graph"] == []
    assert changed["candidate_digest"] == ""
    assert changed["evidence_graph"] == []
    assert changed["closure"] == {}
    assert store.claim_next_slice(
        task_id, worker_session_id="worker-b", lease_seconds=60
    ) is None
    replanned = store.set_plan(
        task_id,
        [_slice("s1"), _slice("s2", dependencies=["s1"])],
        expected_state_revision=changed["state_revision"],
    )
    assert replanned["plan_revision"] > before["plan_revision"]
    assert store.claim_next_slice(
        task_id, worker_session_id="worker-b", lease_seconds=60
    )["slice_id"] == "s1"


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
        termination_proof=_termination_proof("worker-rejected", rejected_packet),
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
                {"kind": "test", "ref": "pytest:passed", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q tests/s2.py",
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
                {"kind": "test", "ref": "pytest:passed", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q tests/s2.py",
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
    with pytest.raises(ValueError, match="closure_evidence_not_admitted"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[
                {
                    "kind": "test",
                    "ref": "DOES-NOT-EXIST",
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
            verification_results=[
                {
                    "command": "NEVER-RAN",
                    "exit_code": 0,
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
        )

    store.bind_entrypoint(
        task_id,
        entrypoint="cli",
        session_id="closure-fresh-state-change",
    )
    with pytest.raises(ValueError, match="closure_packet_stale"):
        store.close_task(
            task_id,
            candidate_digest="sha256:candidate-final",
            verifier_session_id="closure-fresh",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[
                {"kind": "test", "ref": "pytest:passed", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q tests/s2.py",
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
            {"kind": "test", "ref": "pytest:passed", "candidate_digest": "sha256:candidate-final"}
        ],
        verification_results=[
            {
                "command": "pytest -q tests/s2.py",
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
            {"kind": "test", "ref": "pytest:passed", "candidate_digest": "sha256:candidate-final"}
        ],
        verification_results=[
            {
                "command": "pytest -q tests/s2.py",
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
                {"kind": "test", "ref": "pytest:passed", "candidate_digest": "sha256:candidate-final"}
            ],
            verification_results=[
                {
                    "command": "pytest -q tests/s2.py",
                    "exit_code": 0,
                    "candidate_digest": "sha256:candidate-final",
                }
            ],
        )

    invalidated = store.append_source_event(
        task_id,
        {
            "event_id": "post-close-correction",
            "content": "new source invalidates the closed candidate",
        },
    )
    assert invalidated["status"] == "draft"
    assert invalidated["closure"] == {}
    assert invalidated["candidate_digest"] == ""
    assert invalidated["evidence_graph"] == []
    assert invalidated["implementation_graph"] == []
    assert invalidated["slices"] == []
    assert invalidated["state_events"][-1]["payload"]["closure_invalidated"] is True
    with pytest.raises(ValueError, match="task_not_claimable"):
        store.claim_next_slice(
            task_id,
            worker_session_id="stale-plan-worker",
            lease_seconds=60,
        )
    respecified = _review_and_freeze(
        store,
        task_id,
        _spec_for_sources("S-U1", "post-close-correction"),
        expected_state_revision=invalidated["state_revision"],
        reviewer_session_id="spec-reviewer-after-correction",
        compiler_session_id="spec-compiler-after-correction",
    )
    assert respecified["implementation_graph"] == []
    assert respecified["slices"] == []
    assert store.claim_next_slice(
        task_id,
        worker_session_id="still-stale-plan-worker",
        lease_seconds=60,
    ) is None


def test_closure_rejects_candidate_evidence_without_all_required_claims(tmp_path):
    store = TaskRuntimeStore(tmp_path / "candidate-claims.db")
    task = store.create_task(
        title="candidate claims",
        original_sources=[{"source_id": "S-U1", "content": "source"}],
    )
    task = _review_and_freeze(
        store,
        task["task_id"],
        SPEC,
        expected_state_revision=task["state_revision"],
    )
    first_definition = _slice("s1")
    first_definition["negative_requirements"] = []
    second_definition = _slice("s2", dependencies=["s1"])
    second_definition["required_clauses"] = []
    task = store.set_plan(
        task["task_id"],
        [first_definition, second_definition],
        expected_state_revision=task["state_revision"],
    )
    task_id = task["task_id"]

    first = store.claim_next_slice(
        task_id, worker_session_id="worker-first", lease_seconds=60, now=100.0
    )
    assert first is not None
    first_result = _result(first, candidate="sha256:old-candidate")
    first_result["claims"] = ["R1"]
    first_receipt = store.submit_result(
        task_id,
        worker_session_id="worker-first",
        packet_id=first["packet_id"],
        result=first_result,
        now=101.0,
    )
    store.integrate_result(
        task_id, first_receipt["result_id"], integrator_session_id="integrator"
    )

    second = store.claim_next_slice(
        task_id, worker_session_id="worker-second", lease_seconds=60, now=102.0
    )
    assert second is not None
    second_result = _result(second, candidate="sha256:final-candidate")
    second_result["claims"] = ["N1"]
    second_receipt = store.submit_result(
        task_id,
        worker_session_id="worker-second",
        packet_id=second["packet_id"],
        result=second_result,
        now=103.0,
    )
    store.integrate_result(
        task_id, second_receipt["result_id"], integrator_session_id="integrator"
    )
    closure_packet = store.build_closure_packet(
        task_id, verifier_session_id="independent-verifier"
    )
    with pytest.raises(ValueError, match="candidate_evidence_claims_incomplete"):
        store.close_task(
            task_id,
            candidate_digest="sha256:final-candidate",
            verifier_session_id="independent-verifier",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=[
                {
                    "kind": "test",
                    "ref": "pytest:passed",
                    "candidate_digest": "sha256:final-candidate",
                }
            ],
            verification_results=[
                {
                    "command": "pytest -q tests/s2.py",
                    "exit_code": 0,
                    "candidate_digest": "sha256:final-candidate",
                }
            ],
        )


def test_spec_review_schema_migrates_without_rewriting_projection_history(tmp_path):
    path = tmp_path / "pre-spec-review.db"
    store = TaskRuntimeStore(path)
    created = store.create_task(
        title="pre-review schema",
        original_sources=[{"source_id": "S1", "content": "source"}],
    )
    original_events = created["state_events"]
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER spec_reviews_no_update")
        conn.execute("DROP TRIGGER spec_reviews_no_delete")
        conn.execute("DROP TABLE spec_reviews")

    migrated = TaskRuntimeStore(path).get_task(created["task_id"])
    assert migrated["spec_reviews"] == []
    assert migrated["state_events"] == original_events


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
    with pytest.raises(ValueError, match="task_projection_digest_invalid"):
        store.bind_entrypoint(task_id, entrypoint="cli", session_id="cli:must-not-reanchor")
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entrypoint_bindings WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0
    with pytest.raises(ValueError, match="task_projection_digest_invalid"):
        store.get_task(task_id)
