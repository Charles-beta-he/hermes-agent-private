from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import threading

import pytest

import agent.task_runtime as task_runtime_module
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


@pytest.mark.parametrize("source_id", [1, True, "", "   "])
def test_create_task_rejects_non_native_or_blank_original_source_identity_before_mutation(
    tmp_path, source_id
):
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")

    with pytest.raises(ValueError, match="original_source_identity_invalid"):
        store.create_task(
            title="invalid source identity",
            original_sources=[
                {"source_id": source_id, "kind": "user_message", "content": "source"}
            ],
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_create_task_rejects_str_subclass_original_source_identity_before_mutation(tmp_path):
    class SourceId(str):
        pass

    store = TaskRuntimeStore(tmp_path / "task-runtime.db")

    with pytest.raises(ValueError, match="original_source_identity_invalid"):
        store.create_task(
            title="invalid source identity",
            original_sources=[
                {"source_id": SourceId("S1"), "kind": "user_message", "content": "source"}
            ],
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_create_task_rejects_duplicate_original_source_identity_before_mutation(tmp_path):
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")

    with pytest.raises(ValueError, match="original_source_identity_duplicate"):
        store.create_task(
            title="duplicate source identity",
            original_sources=[
                {"source_id": "S1", "kind": "user_message", "content": "first"},
                {"source_id": "S1", "kind": "user_message", "content": "second"},
            ],
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("content", [1, True, ""])
def test_create_task_rejects_non_native_or_empty_original_source_content_before_mutation(
    tmp_path, content
):
    store = TaskRuntimeStore(tmp_path / "task-runtime.db")

    with pytest.raises(ValueError, match="original_source_content_invalid"):
        store.create_task(
            title="invalid source content",
            original_sources=[{"source_id": "S1", "content": content}],
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


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


def test_legacy_source_event_identity_is_not_coerced_during_migration(tmp_path):
    path = tmp_path / "legacy-source-event.db"
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
                'legacy-source-event', 'hermes.task-record.v1', 'legacy source event',
                'draft', '[]', '[{"event_id":1,"content":"legacy correction"}]',
                '{}', '[]', '[]', '{}', 0, 0, 1, '', 1.0, 1.0
            );
            """
        )

    with pytest.raises(ValueError, match="source_event_identity_invalid"):
        TaskRuntimeStore(path, owner_profile="profile-a")
    with sqlite3.connect(path) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_events'"
        ).fetchone()
        if table_exists:
            assert conn.execute("SELECT COUNT(*) FROM source_events").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("source_events_json", "error"),
    [
        ('[{"event_id":"valid-event","content":"valid"},7]', "source_event_invalid"),
        (
            '[{"event_id":"same-event","content":"first"},'
            '{"event_id":"same-event","content":"second"}]',
            "source_event_id_conflict",
        ),
        (
            '[{"event_id":"typed-event","content":1},'
            '{"event_id":"typed-event","content":true}]',
            "source_event_id_conflict",
        ),
        ('[{"event_id":"non-finite","content":NaN}]', "source_event_payload_invalid"),
        (
            '[{"event_id":"duplicate-member","content":1,"content":true}]',
            "source_event_payload_invalid",
        ),
        (
            '[{"event_id":"duplicate-id","event_id":"other","content":"x"}]',
            "source_event_payload_invalid",
        ),
        (
            '[{"event_id":"nested-duplicate","content":{"k":1,"k":2}}]',
            "source_event_payload_invalid",
        ),
        ('[{"event_id":"missing-content"}]', "source_event_content_required"),
        (
            '[{"event_id":"numeric-recorded-at","content":"x","recorded_at":1}]',
            "source_event_recorded_at_invalid",
        ),
        (
            '[{"event_id":"empty-recorded-at","content":"x","recorded_at":""}]',
            "source_event_recorded_at_invalid",
        ),
        ('"scalar-event"', "source_event_collection_invalid"),
        ('{"event_id":"object-root"}', "source_event_collection_invalid"),
        ("", "source_event_collection_invalid"),
        ("null", "source_event_collection_invalid"),
    ],
)
def test_legacy_source_event_collection_rejects_invalid_shapes_during_migration(
    tmp_path, source_events_json, error
):
    path = tmp_path / "legacy-source-event-invalid-shape.db"
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
                "legacy-source-event-invalid-shape",
                "hermes.task-record.v1",
                "legacy source event invalid shape",
                "draft",
                "[]",
                source_events_json,
                "{}",
                "[]",
                "[]",
                "{}",
                0,
                0,
                1,
                "",
                1.0,
                1.0,
            ),
        )

    with pytest.raises(ValueError, match=error):
        TaskRuntimeStore(path, owner_profile="profile-a")
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_events'"
        ).fetchone() is None
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "owner_profile" not in columns


@pytest.mark.parametrize(
    ("row_event_id", "payload_json", "row_recorded_at", "error"),
    [
        (
            "non-finite",
            '{"event_id":"non-finite","content":NaN,"recorded_at":"2026-01-01T00:00:00Z"}',
            "2026-01-01T00:00:00Z",
            "source_event_payload_invalid",
        ),
        (
            "duplicate-member",
            '{"event_id":"duplicate-member","content":1,"content":true,'
            '"recorded_at":"2026-01-01T00:00:00Z"}',
            "2026-01-01T00:00:00Z",
            "source_event_payload_invalid",
        ),
        (
            "7",
            '{"event_id":7,"content":"x","recorded_at":"2026-01-01T00:00:00Z"}',
            "2026-01-01T00:00:00Z",
            "source_event_identity_invalid",
        ),
        (
            "row-id",
            '{"event_id":"payload-id","content":"x",'
            '"recorded_at":"2026-01-01T00:00:00Z"}',
            "2026-01-01T00:00:00Z",
            "source_event_id_conflict",
        ),
        (
            "missing-content",
            '{"event_id":"missing-content","recorded_at":"2026-01-01T00:00:00Z"}',
            "2026-01-01T00:00:00Z",
            "source_event_content_required",
        ),
        (
            "blank-recorded-at",
            '{"event_id":"blank-recorded-at","content":"x","recorded_at":""}',
            "",
            "source_event_recorded_at_invalid",
        ),
        (
            "recorded-at-mismatch",
            '{"event_id":"recorded-at-mismatch","content":"x",'
            '"recorded_at":"2026-01-01T00:00:00Z"}',
            "2026-01-02T00:00:00Z",
            "source_event_payload_invalid",
        ),
    ],
)
def test_existing_normalized_source_event_is_revalidated_on_upgrade(
    tmp_path, row_event_id, payload_json, row_recorded_at, error
):
    path = tmp_path / "normalized-source-event-upgrade.db"
    store = TaskRuntimeStore(path, owner_profile="profile-a")
    task = store.create_task(
        title="normalized source event upgrade",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO source_events(task_id, event_id, payload_json, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (task["task_id"], row_event_id, payload_json, row_recorded_at),
        )

    with pytest.raises(ValueError, match=error):
        TaskRuntimeStore(path, owner_profile="profile-a")
    with sqlite3.connect(path) as conn:
        retained = conn.execute(
            "SELECT event_id, payload_json, recorded_at FROM source_events"
        ).fetchall()
    assert retained == [(row_event_id, payload_json, row_recorded_at)]


@pytest.mark.parametrize(
    ("sources_json", "error"),
    [
        ('[{"source_id":7,"content":"original"}]', "original_source_identity_invalid"),
        ('[{"source_id":true,"content":"original"}]', "original_source_identity_invalid"),
        ('[{"source_id":" ","content":"original"}]', "original_source_identity_invalid"),
        ('[{"source_id":"S0","content":7}]', "original_source_content_invalid"),
        (
            '[{"source_id":"S0","content":"first"},'
            '{"source_id":"S0","content":"second"}]',
            "original_source_identity_duplicate",
        ),
    ],
)
def test_existing_original_source_identity_is_revalidated_on_upgrade(
    tmp_path, sources_json, error
):
    path = tmp_path / "original-source-upgrade.db"
    store = TaskRuntimeStore(path, owner_profile="profile-a")
    task = store.create_task(
        title="original source upgrade",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE tasks SET original_sources_json = ? WHERE task_id = ?",
            (sources_json, task["task_id"]),
        )

    with pytest.raises(ValueError, match=error):
        TaskRuntimeStore(path, owner_profile="profile-a")
    with sqlite3.connect(path) as conn:
        retained = conn.execute(
            "SELECT original_sources_json FROM tasks WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()[0]
    assert retained == sources_json


def test_legacy_source_event_reopen_rejects_normalized_identity_conflict(tmp_path):
    path = tmp_path / "legacy-source-event-reopen-conflict.db"
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
                'legacy-reopen-conflict', 'hermes.task-record.v1', 'legacy reopen conflict',
                'draft', '[]', '[{"event_id":"same-event","content":1}]',
                '{}', '[]', '[]', '{}', 0, 0, 1, '', 1.0, 1.0
            );
            """
        )

    TaskRuntimeStore(path, owner_profile="profile-a")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE tasks SET source_events_json = ? WHERE task_id = ?",
            ('[{"event_id":"same-event","content":true}]', "legacy-reopen-conflict"),
        )

    with pytest.raises(ValueError, match="source_event_id_conflict"):
        TaskRuntimeStore(path, owner_profile="profile-a")
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM source_events WHERE task_id = ?",
            ("legacy-reopen-conflict",),
        ).fetchall()
    assert len(rows) == 1
    content = json.loads(rows[0][0])["content"]
    assert type(content) is int
    assert content == 1


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


def test_legacy_governed_plan_requires_fresh_freeze_and_plan_before_claim(tmp_path):
    path = tmp_path / "legacy-governed-planned.db"
    spec = _spec_for_sources("S1")
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
                "legacy-governed-planned",
                "hermes.task-record.v1",
                "legacy governed planned",
                "planned",
                '[{"source_id":"S1","content":"legacy"}]',
                "[]",
                json.dumps(spec),
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
    task = migrated.get_task("legacy-governed-planned")
    review = migrated.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="legacy-reviewer",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    assert review["verdict"] == "approved"
    reviewed = migrated.get_task(task["task_id"])
    fresh_slice = _slice("fresh-slice")
    fresh_slice["source_refs"] = ["S1"]
    with pytest.raises(ValueError, match="current_spec_freeze_required"):
        migrated.set_plan(
            task["task_id"],
            [fresh_slice],
            expected_state_revision=reviewed["state_revision"],
        )
    with pytest.raises(ValueError, match="current_spec_plan_admission_required"):
        migrated.claim_next_slice(
            task["task_id"], worker_session_id="worker-a", lease_seconds=60
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


def test_plan_rejects_numeric_source_ref_that_stringifies_to_known_source(tmp_path):
    store = TaskRuntimeStore(tmp_path / "numeric-plan-source-ref.db")
    task = store.create_task(
        title="numeric plan source ref",
        original_sources=[{"source_id": "1", "content": "source"}],
    )
    spec = _governed_spec("1")
    task = _review_and_freeze(
        store,
        task["task_id"],
        spec,
        expected_state_revision=task["state_revision"],
    )
    definition = _slice("s1")
    definition["source_refs"] = [1]

    with pytest.raises(ValueError, match="slice_source_ref_unknown"):
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


@pytest.mark.parametrize(
    ("malformed_field", "expected_error"),
    [
        ("source_authority", "spec_source_authority_invalid"),
        ("oracle_refs", "spec_oracle_refs_invalid"),
        ("claim_source_refs", "spec_claim_source_refs_invalid"),
        ("discovery_source_refs", "spec_discovery_preflight_invalid"),
        ("closure_criteria", "spec_closure_coverage_incomplete"),
    ],
)
def test_governed_spec_rejects_string_shaped_collection_fields(
    tmp_path, malformed_field, expected_error
):
    store = TaskRuntimeStore(tmp_path / f"malformed-{malformed_field}.db")
    task = store.create_task(
        title="malformed governed collection",
        original_sources=[{"source_id": "A", "kind": "user_message", "content": "source"}],
    )
    spec = _spec_for_sources("A")
    spec["required_clauses"][0]["id"] = "P"
    spec["negative_requirements"][0]["id"] = "N"
    spec["governance"]["source_coverage"] = {"A": ["P", "N"]}
    spec["closure_criteria"] = ["P", "N"]
    if malformed_field == "source_authority":
        spec["governance"]["source_authority"] = "A"
    elif malformed_field == "oracle_refs":
        spec["governance"]["oracle_refs"] = "A"
    elif malformed_field == "claim_source_refs":
        spec["required_clauses"][0]["source_refs"] = "A"
    elif malformed_field == "discovery_source_refs":
        spec["governance"]["discovery"]["project_evidence"]["source_refs"] = "A"
    else:
        spec["closure_criteria"] = "PN"

    with pytest.raises(ValueError, match=expected_error):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id=f"malformed-{malformed_field}-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )


def test_governed_spec_rejects_non_string_claim_and_coverage_ids(tmp_path):
    store = TaskRuntimeStore(tmp_path / "numeric-claim-id.db")
    task = store.create_task(
        title="numeric governed claim",
        original_sources=[{"source_id": "A", "kind": "user_message", "content": "source"}],
    )
    spec = _spec_for_sources("A")
    spec["required_clauses"][0]["id"] = 1
    spec["governance"]["source_coverage"] = {"A": ["1", "N1"]}
    spec["closure_criteria"] = ["1", "N1"]

    with pytest.raises(ValueError, match="spec_claim_identity_invalid"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="numeric-claim-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )

    spec = _spec_for_sources("A")
    spec["governance"]["source_coverage"] = {"A": [1, "N1"]}
    with pytest.raises(ValueError, match="spec_source_coverage_incomplete"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="numeric-coverage-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )


def test_governed_spec_rejects_summary_oracle_reordered_authority_and_bodyless_claims(
    tmp_path,
):
    summary_store = TaskRuntimeStore(tmp_path / "summary-oracle.db")
    summary_task = summary_store.create_task(
        title="summary-only oracle",
        original_sources=[
            {"source_id": "S-summary", "kind": "summary", "content": "derived index"}
        ],
    )
    with pytest.raises(ValueError, match="spec_oracle_refs_not_authoritative"):
        summary_store.record_spec_review(
            summary_task["task_id"],
            _governed_spec("S-summary"),
            reviewer_session_id="summary-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=summary_task["state_revision"],
        )

    ordered_store = TaskRuntimeStore(tmp_path / "authority-order.db")
    ordered_task = ordered_store.create_task(
        title="ordered authority",
        original_sources=[
            {"source_id": "S1", "kind": "user_message", "content": "highest"},
            {"source_id": "S2", "kind": "project_ssot", "content": "second"},
        ],
    )
    reversed_spec = _spec_for_sources("S2", "S1")
    with pytest.raises(ValueError, match="spec_source_authority_order_invalid"):
        ordered_store.record_spec_review(
            ordered_task["task_id"],
            reversed_spec,
            reviewer_session_id="order-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=ordered_task["state_revision"],
        )

    bodyless_spec = _spec_for_sources("S1", "S2")
    bodyless_spec["required_clauses"][0].pop("text")
    with pytest.raises(ValueError, match="spec_claim_body_required"):
        ordered_store.record_spec_review(
            ordered_task["task_id"],
            bodyless_spec,
            reviewer_session_id="body-reviewer",
            verdict="approved",
            findings=[],
            expected_state_revision=ordered_task["state_revision"],
        )


def test_governed_spec_rejects_target_derived_oracle_kinds(tmp_path):
    for derived_kind in (
        "agent_summary",
        "open_source_demo",
        "test_output",
        "model_agreement",
        "readme",
    ):
        store = TaskRuntimeStore(tmp_path / f"{derived_kind}-oracle.db")
        task = store.create_task(
            title=f"{derived_kind}-only oracle",
            original_sources=[
                {
                    "source_id": "S-derived",
                    "kind": derived_kind,
                    "content": "derived index or evidence",
                }
            ],
        )
        with pytest.raises(ValueError, match="spec_oracle_refs_not_authoritative"):
            store.record_spec_review(
                task["task_id"],
                _governed_spec("S-derived"),
                reviewer_session_id=f"{derived_kind}-reviewer",
                verdict="approved",
                findings=[],
                expected_state_revision=task["state_revision"],
            )


def test_derived_summary_cannot_masquerade_as_authoritative_source(tmp_path):
    store = TaskRuntimeStore(tmp_path / "derived-summary-authority.db")
    task = store.create_task(
        title="derived summary authority",
        original_sources=[
            {"source_id": "REQ", "kind": "user_message", "content": "original"},
            {"source_id": "SUM", "kind": "derived_summary", "content": "derived"},
        ],
    )
    spec = _spec_for_sources("REQ", "SUM")
    spec["governance"]["oracle_refs"] = ["REQ"]
    before_revision = task["state_revision"]
    with pytest.raises(ValueError, match="spec_source_authority_incomplete"):
        store.record_spec_review(
            task["task_id"], spec, reviewer_session_id="summary-authority-reviewer",
            verdict="approved", findings=[], expected_state_revision=before_revision,
        )
    rejected = store.get_task(task["task_id"])
    assert rejected["state_revision"] == before_revision
    assert rejected["spec_reviews"] == []


def test_derived_summary_cannot_be_oracle(tmp_path):
    store = TaskRuntimeStore(tmp_path / "derived-summary-oracle.db")
    task = store.create_task(
        title="derived summary oracle",
        original_sources=[
            {"source_id": "REQ", "kind": "user_message", "content": "original"},
            {"source_id": "SUM", "kind": "derived_summary", "content": "derived"},
        ],
    )
    spec = _spec_for_sources("REQ", "SUM")
    spec["governance"]["source_authority"] = ["REQ"]
    spec["governance"]["oracle_refs"] = ["SUM"]
    before_revision = task["state_revision"]
    with pytest.raises(ValueError, match="spec_oracle_refs_not_authoritative"):
        store.record_spec_review(
            task["task_id"], spec, reviewer_session_id="summary-oracle-reviewer",
            verdict="approved", findings=[], expected_state_revision=before_revision,
        )
    rejected = store.get_task(task["task_id"])
    assert rejected["state_revision"] == before_revision
    assert rejected["spec_reviews"] == []


def test_strict_ui_accepts_task_packet_source_kind_and_session_locator(tmp_path):
    sources = [
        _strict_source("REQ", "project_ssot", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    sources[0]["immutable_locator"] = "session://default/session-1/message-1"
    store = TaskRuntimeStore(tmp_path / "session-locator.db")
    task = store.create_task(title="session locator", original_sources=sources)
    reviewed = store.record_spec_review(
        task["task_id"], _strict_ui_spec(), reviewer_session_id="strict-reviewer",
        verdict="approved", findings=[], expected_state_revision=task["state_revision"],
    )
    assert reviewed["state_revision"] == task["state_revision"] + 1


def test_non_authoritative_research_note_cannot_enter_source_authority(tmp_path):
    sources = [
        _strict_source(
            "REQ", "research_note", "build exact results", 10,
            derived_from=["PROTO"],
        ),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    store = TaskRuntimeStore(tmp_path / "research-note-authority.db")
    task = store.create_task(title="research authority", original_sources=sources)
    with pytest.raises(ValueError, match="spec_source_authority_incomplete"):
        store.record_spec_review(
            task["task_id"], _strict_ui_spec(), reviewer_session_id="strict-reviewer",
            verdict="approved", findings=[], expected_state_revision=task["state_revision"],
        )


def test_non_authoritative_summary_cannot_replace_direct_claim_anchor(tmp_path):
    sources = [
        _strict_source("REQ", "original_requirement", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
        _strict_source("SUM", "summary", "derived index", 30, derived_from=["REQ"]),
    ]
    spec = _strict_ui_spec()
    spec["governance"]["source_coverage"]["SUM"] = ["R1"]
    required = spec["required_clauses"][0]
    required["source_refs"] = ["REQ", "PROTO", "SUM"]
    required["source_anchors"] = [
        {"source_ref": "SUM", "locator": sources[2]["immutable_locator"]}
    ]
    store = TaskRuntimeStore(tmp_path / "summary-anchor.db")
    task = store.create_task(title="summary anchor", original_sources=sources)
    with pytest.raises(ValueError, match="source_anchor_not_authoritative"):
        store.record_spec_review(
            task["task_id"], spec, reviewer_session_id="strict-reviewer",
            verdict="approved", findings=[], expected_state_revision=task["state_revision"],
        )


def _strict_source(source_id, source_kind, content, authority_rank, derived_from=None):
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "source_id": source_id,
        "kind": "summary" if source_kind == "summary" else "user_message",
        "source_kind": source_kind,
        "content": content,
        "immutable_locator": f"inline:{source_id}@{digest}",
        "content_digest": digest,
        "authority_rank": authority_rank,
        "derived_from": list(derived_from or []),
    }


def _strict_ui_spec():
    spec = _spec_for_sources("REQ", "PROTO")
    prototype_digest = "sha256:" + hashlib.sha256(
        b"results visual oracle"
    ).hexdigest()
    governance = spec["governance"]
    governance["target_profile"] = "strict_ui"
    governance["oracle_refs"] = ["PROTO"]
    governance["source_obligations"] = {
        "pages": ["list", "detail"],
        "states": [
            "list/loading",
            "list/success",
            "list/empty",
            "detail/success",
            "detail/error",
        ],
        "interactions": ["list-to-detail", "detail-back-to-list"],
        "viewports": ["1440x900"],
        "negative_cases": [
            "no-generic-list-empty",
            "no-generic-detail-error",
            "preserve-list-state-on-back",
        ],
        "fidelity_dimensions": ["visual", "interaction", "responsive"],
    }
    obligations = [
        f"{field}:{value}"
        for field, values in governance["source_obligations"].items()
        for value in values
    ]
    spec["required_clauses"][0].update(
        {
            "source_refs": ["REQ", "PROTO"],
            "source_anchors": [
                {
                    "source_ref": "PROTO",
                    "locator": f"inline:PROTO@{prototype_digest}#list-detail",
                }
            ],
            "scope_quantifier": "all",
            "obligation_refs": obligations,
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual", "interaction", "responsive"],
        }
    )
    spec["negative_requirements"][0].update(
        {
            "source_refs": ["REQ", "PROTO"],
            "source_anchors": [
                {
                    "source_ref": "PROTO",
                    "locator": f"inline:PROTO@{prototype_digest}#list-detail",
                }
            ],
            "scope_quantifier": "all",
            "obligation_refs": ["negative_cases:no-generic-detail-error"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual"],
        }
    )
    governance["source_coverage"] = {
        "REQ": ["R1", "N1"],
        "PROTO": ["R1", "N1"],
    }
    for lane in governance["discovery"].values():
        lane["source_refs"] = ["REQ"]
    return spec


def _strict_ui_spec_with_numeric_string_claim_ids():
    spec = _strict_ui_spec()
    spec["required_clauses"][0]["id"] = "1"
    spec["negative_requirements"][0]["id"] = "2"
    spec["closure_criteria"] = ["1", "2"]
    spec["governance"]["source_coverage"] = {
        "REQ": ["1", "2"],
        "PROTO": ["1", "2"],
    }
    return spec


def _numeric_claim_strict_packet(tmp_path, *, suffix, worker_session_id):
    store = TaskRuntimeStore(tmp_path / f"numeric-claim-{suffix}.db")
    task = store.create_task(
        title="numeric claim identity",
        original_sources=[
            _strict_source("REQ", "original_requirement", "build exact results", 10),
            _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
        ],
    )
    spec = _strict_ui_spec_with_numeric_string_claim_ids()
    task = _review_and_freeze(
        store, task["task_id"], spec, expected_state_revision=task["state_revision"]
    )
    definition = _slice("numeric-claim-slice")
    definition.update(
        {
            "required_clauses": ["1"],
            "negative_requirements": ["2"],
            "source_refs": ["REQ", "PROTO"],
            "obligation_refs": spec["required_clauses"][0]["obligation_refs"],
            "source_anchors": spec["required_clauses"][0]["source_anchors"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual", "interaction", "responsive"],
        }
    )
    store.set_plan(
        task["task_id"], [definition], expected_state_revision=task["state_revision"]
    )
    packet = store.claim_next_slice(
        task["task_id"], worker_session_id=worker_session_id, lease_seconds=60
    )
    assert packet is not None
    return store, task["task_id"], definition, packet


def test_strict_spec_review_rejects_string_subclass_claim_identity_before_mutation(tmp_path):
    class ClaimId(str):
        pass

    store = TaskRuntimeStore(tmp_path / "strict-subclass-claim.db")
    task = store.create_task(
        title="strict subclass claim",
        original_sources=[
            _strict_source("REQ", "original_requirement", "build exact results", 10),
            _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
        ],
    )
    spec = _strict_ui_spec()
    spec["required_clauses"][0]["id"] = ClaimId("R1")

    with pytest.raises(ValueError, match="spec_claim_identity_invalid"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="reviewer-native-identity",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )

    unchanged = store.get_task(task["task_id"])
    assert unchanged["state_revision"] == task["state_revision"]
    assert unchanged["spec_reviews"] == []


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("numeric_claim", "slice_claim_identity_invalid"),
        ("numeric_slice", "slice_ids_must_be_unique_and_nonempty"),
    ],
)
def test_strict_plan_rejects_non_native_claim_and_slice_identity_before_mutation(
    tmp_path, mutation, error
):
    store = TaskRuntimeStore(tmp_path / f"strict-plan-{mutation}.db")
    task = store.create_task(
        title="strict plan identity",
        original_sources=[
            _strict_source("REQ", "original_requirement", "build exact results", 10),
            _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
        ],
    )
    spec = _strict_ui_spec_with_numeric_string_claim_ids()
    task = _review_and_freeze(
        store, task["task_id"], spec, expected_state_revision=task["state_revision"]
    )
    definition = _slice("strict-native-plan")
    definition.update(
        {
            "required_clauses": ["1"],
            "negative_requirements": ["2"],
            "source_refs": ["REQ", "PROTO"],
            "obligation_refs": spec["required_clauses"][0]["obligation_refs"],
            "source_anchors": spec["required_clauses"][0]["source_anchors"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual", "interaction", "responsive"],
        }
    )
    if mutation == "numeric_claim":
        definition["required_clauses"] = [1]
    else:
        definition["slice_id"] = 1

    with pytest.raises(ValueError, match=error):
        store.set_plan(
            task["task_id"], [definition], expected_state_revision=task["state_revision"]
        )

    unchanged = store.get_task(task["task_id"])
    assert unchanged["state_revision"] == task["state_revision"]
    assert unchanged["implementation_graph"] == []


def _numeric_claim_evidence(packet, definition, *, claim_ids=("1", "2")):
    base = {
        "kind": "ui-fidelity",
        "oracle_refs": ["PROTO"],
        "candidate_digest": "sha256:numeric-claim",
        "source_digest": packet["source_digest"],
        "spec_digest": packet["spec_digest"],
    }
    return [
        {
            **base,
            "ref": "uimax:numeric-1",
            "claim_id": claim_ids[0],
            "obligation_refs": definition["obligation_refs"],
            "fidelity_dimensions": definition["fidelity_dimensions"],
            "source_anchors": packet["claim_contracts"]["1"]["source_anchors"],
        },
        {
            **base,
            "ref": "uimax:numeric-2",
            "claim_id": claim_ids[1],
            "obligation_refs": ["negative_cases:no-generic-detail-error"],
            "fidelity_dimensions": ["visual"],
            "source_anchors": packet["claim_contracts"]["2"]["source_anchors"],
        },
    ]


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing_source_kind", "source_artifact_kind_required"),
        ("illegal_source_kind", "source_artifact_kind_invalid"),
        ("digest_mismatch", "source_artifact_digest_mismatch"),
        ("mutable_locator", "source_artifact_locator_not_immutable"),
        ("mutable_session_locator", "source_artifact_locator_not_immutable"),
        ("authority_empty", "spec_source_authority_incomplete"),
        ("obligation_incomplete", "source_obligation_scope_incomplete"),
        ("quantifier_weakened", "source_quantifier_weakened"),
        ("unbound_claim", "source_obligation_scope_incomplete"),
        ("detached_claim_anchor", "source_anchor_not_immutable"),
        ("duplicate_source_identity", "original_source_identity_duplicate"),
    ],
)
def test_strict_ui_source_and_obligation_admission_fails_closed(tmp_path, mutation, error):
    sources = [
        _strict_source("REQ", "original_requirement", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    spec = _strict_ui_spec()
    if mutation == "missing_source_kind":
        sources[0].pop("source_kind")
    elif mutation == "illegal_source_kind":
        sources[0]["source_kind"] = "model_guess"
    elif mutation == "digest_mismatch":
        sources[0]["content_digest"] = "sha256:" + "0" * 64
    elif mutation == "mutable_locator":
        sources[0]["immutable_locator"] = "https://example.test/latest"
    elif mutation == "mutable_session_locator":
        sources[0]["immutable_locator"] = (
            "session://default/session-1/message-1?revision=latest"
        )
    elif mutation == "authority_empty":
        spec["governance"]["source_authority"] = []
    elif mutation == "obligation_incomplete":
        spec["required_clauses"][0]["obligation_refs"].remove("states:detail/error")
    elif mutation == "quantifier_weakened":
        spec["required_clauses"][0]["scope_quantifier"] = "main"
    elif mutation == "unbound_claim":
        for field in (
            "obligation_refs",
            "source_anchors",
            "scope_quantifier",
            "oracle_refs",
            "fidelity_dimensions",
        ):
            spec["negative_requirements"][0].pop(field)
    elif mutation == "detached_claim_anchor":
        spec["required_clauses"][0]["source_anchors"][0]["locator"] = (
            "inline:PROTO#list-detail"
        )
    elif mutation == "duplicate_source_identity":
        sources.insert(1, dict(sources[0]))
        spec["governance"]["source_authority"] = ["REQ", "REQ", "PROTO"]
    store = TaskRuntimeStore(tmp_path / f"strict-{mutation}.db")
    if mutation == "duplicate_source_identity":
        with pytest.raises(ValueError, match=error):
            store.create_task(title="strict ui", original_sources=sources)
        with store._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        return
    task = store.create_task(title="strict ui", original_sources=sources)
    before_revision = task["state_revision"]
    with pytest.raises(ValueError, match=error):
        store.record_spec_review(
            task["task_id"], spec, reviewer_session_id="strict-reviewer",
            verdict="approved", findings=[], expected_state_revision=before_revision,
        )
    rejected = store.get_task(task["task_id"])
    assert rejected["state_revision"] == before_revision
    assert rejected["spec_reviews"] == []


def test_strict_ui_context_packet_preserves_semantic_fidelity_contract(tmp_path):
    sources = [
        _strict_source("REQ", "original_requirement", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    store = TaskRuntimeStore(tmp_path / "strict-context.db")
    task = store.create_task(title="strict context", original_sources=sources)
    spec = _strict_ui_spec()
    task = _review_and_freeze(
        store, task["task_id"], spec, expected_state_revision=task["state_revision"]
    )
    definition = _slice("s1")
    definition["source_refs"] = ["REQ", "PROTO"]
    definition.update(
        {
            "obligation_refs": spec["required_clauses"][0]["obligation_refs"],
            "source_anchors": spec["required_clauses"][0]["source_anchors"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual", "interaction", "responsive"],
        }
    )
    task = store.set_plan(
        task["task_id"], [definition], expected_state_revision=task["state_revision"]
    )
    packet = store.claim_next_slice(
        task["task_id"], worker_session_id="strict-worker", lease_seconds=60
    )
    assert packet is not None
    for field in ("obligation_refs", "source_anchors", "oracle_refs", "fidelity_dimensions"):
        assert packet[field] == definition[field]
    assert packet["source_digest"].startswith("sha256:")
    assert packet["spec_digest"].startswith("sha256:")


def test_strict_ui_plan_cannot_weaken_semantic_fidelity_contract(tmp_path):
    sources = [
        _strict_source("REQ", "original_requirement", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    store = TaskRuntimeStore(tmp_path / "strict-plan-contract.db")
    task = store.create_task(title="strict plan contract", original_sources=sources)
    spec = _strict_ui_spec()
    task = _review_and_freeze(
        store, task["task_id"], spec, expected_state_revision=task["state_revision"]
    )

    def strict_definition():
        definition = _slice("strict-plan-slice")
        definition["source_refs"] = ["REQ", "PROTO"]
        definition.update(
            {
                "obligation_refs": list(
                    spec["required_clauses"][0]["obligation_refs"]
                ),
                "source_anchors": list(
                    spec["required_clauses"][0]["source_anchors"]
                ),
                "oracle_refs": ["PROTO"],
                "fidelity_dimensions": ["visual", "interaction", "responsive"],
            }
        )
        return definition

    mutations = []
    missing_obligation = strict_definition()
    missing_obligation["obligation_refs"] = missing_obligation["obligation_refs"][:-1]
    mutations.append(missing_obligation)
    wrong_oracle = strict_definition()
    wrong_oracle["oracle_refs"] = ["REQ"]
    mutations.append(wrong_oracle)
    missing_anchor = strict_definition()
    missing_anchor["source_anchors"] = []
    mutations.append(missing_anchor)
    missing_dimension = strict_definition()
    missing_dimension["fidelity_dimensions"] = ["visual", "interaction"]
    mutations.append(missing_dimension)
    for definition in mutations:
        with pytest.raises(
            ValueError, match="slice_semantic_fidelity_contract_mismatch"
        ):
            store.set_plan(
                task["task_id"], [definition],
                expected_state_revision=task["state_revision"],
            )


def test_strict_ui_each_evidence_item_requires_semantic_binding(tmp_path):
    sources = [
        _strict_source("REQ", "original_requirement", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    store = TaskRuntimeStore(tmp_path / "strict-evidence-binding.db")
    task = store.create_task(title="strict evidence binding", original_sources=sources)
    spec = _strict_ui_spec()
    task = _review_and_freeze(
        store, task["task_id"], spec, expected_state_revision=task["state_revision"]
    )
    definition = _slice("strict-evidence-slice")
    definition.update(
        {
            "source_refs": ["REQ", "PROTO"],
            "obligation_refs": spec["required_clauses"][0]["obligation_refs"],
            "source_anchors": spec["required_clauses"][0]["source_anchors"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual", "interaction", "responsive"],
        }
    )
    task = store.set_plan(
        task["task_id"], [definition], expected_state_revision=task["state_revision"]
    )
    packet = store.claim_next_slice(
        task["task_id"], worker_session_id="strict-evidence-worker", lease_seconds=60
    )
    assert packet is not None
    result = _result(packet, candidate="sha256:strict-evidence")
    result["evidence_refs"] = [
        {
            "kind": "ui-fidelity",
            "ref": "uimax:obligations-only",
            "claim_id": "R1",
            "obligation_refs": definition["obligation_refs"],
            "oracle_refs": [],
            "fidelity_dimensions": [],
        },
        {
            "kind": "ui-fidelity",
            "ref": "uimax:oracle-only",
            "claim_id": "R1",
            "obligation_refs": [],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": definition["fidelity_dimensions"],
        },
    ]
    submitted = store.submit_result(
        task["task_id"], worker_session_id="strict-evidence-worker",
        packet_id=packet["packet_id"], result=result,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_incomplete" in submitted["reasons"]

    missing_digests = _result(packet, candidate="sha256:strict-evidence")
    missing_digests["evidence_refs"] = [
        {
            "kind": "ui-fidelity",
            "ref": "uimax:r1-without-digests",
            "claim_id": "R1",
            "obligation_refs": definition["obligation_refs"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": definition["fidelity_dimensions"],
        },
        {
            "kind": "ui-fidelity",
            "ref": "uimax:n1-without-digests",
            "claim_id": "N1",
            "obligation_refs": ["negative_cases:no-generic-detail-error"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual"],
        },
    ]
    submitted = store.submit_result(
        task["task_id"], worker_session_id="strict-evidence-worker",
        packet_id=packet["packet_id"], result=missing_digests,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_incomplete" in submitted["reasons"]


def test_strict_worker_rejects_coerced_numeric_claim_identities(tmp_path):
    class SpoofedString(str):
        pass

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="worker", worker_session_id="numeric-worker-1"
    )
    numeric_claims = _result(packet, candidate="sha256:numeric-claim")
    numeric_claims["claims"] = [1, 2]
    numeric_claims["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    submitted = store.submit_result(
        task_id,
        worker_session_id="numeric-worker-1",
        packet_id=packet["packet_id"],
        result=numeric_claims,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_claims_invalid" in submitted["reasons"]

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="worker-evidence", worker_session_id="numeric-worker-2"
    )
    numeric_evidence = _result(packet, candidate="sha256:numeric-claim")
    numeric_evidence["claims"] = ["1", "2"]
    numeric_evidence["evidence_refs"] = _numeric_claim_evidence(
        packet, definition, claim_ids=(1, 2)
    )
    submitted = store.submit_result(
        task_id,
        worker_session_id="numeric-worker-2",
        packet_id=packet["packet_id"],
        result=numeric_evidence,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_incomplete" in submitted["reasons"]

    subclass_claim = _result(packet, candidate="sha256:numeric-claim")
    subclass_claim["claims"] = [SpoofedString("1"), "2"]
    subclass_claim["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    submitted = store.submit_result(
        task_id,
        worker_session_id="numeric-worker-2",
        packet_id=packet["packet_id"],
        result=subclass_claim,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_claims_invalid" in submitted["reasons"]

    subclass_evidence = _result(packet, candidate="sha256:numeric-claim")
    subclass_evidence["claims"] = ["1", "2"]
    subclass_evidence["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    subclass_evidence["evidence_refs"][0]["claim_id"] = SpoofedString("1")
    submitted = store.submit_result(
        task_id,
        worker_session_id="numeric-worker-2",
        packet_id=packet["packet_id"],
        result=subclass_evidence,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_incomplete" in submitted["reasons"]

    unassigned_claim = _result(packet, candidate="sha256:numeric-claim")
    unassigned_claim["claims"] = ["1", "2", "EXTRA"]
    unassigned_claim["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    submitted = store.submit_result(
        task_id,
        worker_session_id="numeric-worker-2",
        packet_id=packet["packet_id"],
        result=unassigned_claim,
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_claims_unassigned" in submitted["reasons"]

    replay_store, replay_task_id, replay_definition, replay_packet = (
        _numeric_claim_strict_packet(
            tmp_path, suffix="worker-replay", worker_session_id="numeric-worker-replay"
        )
    )
    valid_result = _result(replay_packet, candidate="sha256:numeric-claim")
    valid_result["claims"] = ["1", "2"]
    valid_result["evidence_refs"] = _numeric_claim_evidence(
        replay_packet, replay_definition
    )
    accepted = replay_store.submit_result(
        replay_task_id,
        worker_session_id="numeric-worker-replay",
        packet_id=replay_packet["packet_id"],
        result=valid_result,
    )
    assert accepted["disposition"] == "accepted_for_integration"
    spoofed_replay = dict(valid_result)
    spoofed_replay["claims"] = [SpoofedString("1"), "2"]
    rejected = replay_store.submit_result(
        replay_task_id,
        worker_session_id="numeric-worker-replay",
        packet_id=replay_packet["packet_id"],
        result=spoofed_replay,
    )
    assert rejected["disposition"] == "rejected"
    assert "worker_claims_invalid" in rejected["reasons"]

    for field in (
        "claims",
        "changed_artifacts",
        "evidence_refs",
        "verification_results",
        "risks",
        "unknowns",
    ):
        tuple_replay = dict(valid_result)
        tuple_replay[field] = tuple(valid_result[field])
        rejected = replay_store.submit_result(
            replay_task_id,
            worker_session_id="numeric-worker-replay",
            packet_id=replay_packet["packet_id"],
            result=tuple_replay,
        )
        assert rejected["disposition"] == "rejected"
        assert "worker_result_invalid_field_types" in rejected["reasons"]

    class SpoofedDict(dict):
        pass

    mapping_replay = dict(valid_result)
    mapping_replay["state_delta_proposal"] = SpoofedDict(
        valid_result["state_delta_proposal"]
    )
    rejected = replay_store.submit_result(
        replay_task_id,
        worker_session_id="numeric-worker-replay",
        packet_id=replay_packet["packet_id"],
        result=mapping_replay,
    )
    assert rejected["disposition"] == "rejected"
    assert "worker_result_invalid_field_types" in rejected["reasons"]

    class SpoofedList(list):
        pass

    nested_replays = []

    top_level_mapping = SpoofedDict(valid_result)
    nested_replays.append(top_level_mapping)

    evidence_mapping = dict(valid_result)
    evidence_mapping["evidence_refs"] = list(valid_result["evidence_refs"])
    evidence_mapping["evidence_refs"][0] = SpoofedDict(
        evidence_mapping["evidence_refs"][0]
    )
    nested_replays.append(evidence_mapping)

    verification_mapping = dict(valid_result)
    verification_mapping["verification_results"] = list(
        valid_result["verification_results"]
    )
    verification_mapping["verification_results"][0] = SpoofedDict(
        verification_mapping["verification_results"][0]
    )
    nested_replays.append(verification_mapping)

    nested_list = copy.deepcopy(valid_result)
    nested_list["evidence_refs"][0]["obligation_refs"] = SpoofedList(
        nested_list["evidence_refs"][0]["obligation_refs"]
    )
    nested_replays.append(nested_list)

    nested_string = copy.deepcopy(valid_result)
    nested_string["evidence_refs"][0]["obligation_refs"][0] = SpoofedString(
        nested_string["evidence_refs"][0]["obligation_refs"][0]
    )
    nested_replays.append(nested_string)

    nested_mapping = copy.deepcopy(valid_result)
    nested_mapping["evidence_refs"][0]["source_anchors"][0] = SpoofedDict(
        nested_mapping["evidence_refs"][0]["source_anchors"][0]
    )
    nested_replays.append(nested_mapping)

    non_finite = copy.deepcopy(valid_result)
    non_finite["risks"] = [float("nan")]
    nested_replays.append(non_finite)

    for hostile_replay in nested_replays:
        rejected = replay_store.submit_result(
            replay_task_id,
            worker_session_id="numeric-worker-replay",
            packet_id=replay_packet["packet_id"],
            result=hostile_replay,
        )
        assert rejected["disposition"] == "rejected"
        assert rejected.get("idempotent_replay") is not True
        assert "worker_result_invalid_field_types" in rejected["reasons"]


def test_strict_closure_rejects_coerced_numeric_claim_identities(tmp_path):
    class SpoofedString(str):
        pass

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="closure", worker_session_id="numeric-worker"
    )
    result = _result(packet, candidate="sha256:numeric-claim")
    result["claims"] = ["1", "2"]
    evidence = _numeric_claim_evidence(packet, definition)
    result["evidence_refs"] = evidence
    submitted = store.submit_result(
        task_id,
        worker_session_id="numeric-worker",
        packet_id=packet["packet_id"],
        result=result,
    )
    assert submitted["disposition"] == "accepted_for_integration"
    store.integrate_result(
        task_id, submitted["result_id"], integrator_session_id="numeric-integrator"
    )
    closure_packet = store.build_closure_packet(
        task_id, verifier_session_id="numeric-verifier"
    )
    verification_results = [
        {
            "command": definition["verification_commands"][0],
            "exit_code": 0,
            "candidate_digest": "sha256:numeric-claim",
        }
    ]
    with pytest.raises(ValueError, match="closure_claim_identity_invalid"):
        store.close_task(
            task_id,
            candidate_digest="sha256:numeric-claim",
            verifier_session_id="numeric-verifier",
            claims={1: "pass", 2: "pass"},
            closure_packet=closure_packet,
            evidence_refs=evidence,
            verification_results=verification_results,
        )
    numeric_evidence = [dict(item, claim_id=index) for index, item in enumerate(evidence, 1)]
    with pytest.raises(ValueError, match="closure_evidence_claim_binding_invalid"):
        store.close_task(
            task_id,
            candidate_digest="sha256:numeric-claim",
            verifier_session_id="numeric-verifier",
            claims={"1": "pass", "2": "pass"},
            closure_packet=closure_packet,
            evidence_refs=numeric_evidence,
            verification_results=verification_results,
        )

    with pytest.raises(ValueError, match="closure_claim_identity_invalid"):
        store.close_task(
            task_id,
            candidate_digest="sha256:numeric-claim",
            verifier_session_id="numeric-verifier",
            claims={SpoofedString("1"): "pass", "2": "pass"},
            closure_packet=closure_packet,
            evidence_refs=evidence,
            verification_results=verification_results,
        )

    subclass_evidence = [dict(item) for item in evidence]
    subclass_evidence[0]["claim_id"] = SpoofedString("1")
    with pytest.raises(ValueError, match="closure_evidence_claim_binding_invalid"):
        store.close_task(
            task_id,
            candidate_digest="sha256:numeric-claim",
            verifier_session_id="numeric-verifier",
            claims={"1": "pass", "2": "pass"},
            closure_packet=closure_packet,
            evidence_refs=subclass_evidence,
            verification_results=verification_results,
        )

    with pytest.raises(ValueError, match="closure_claims_unknown"):
        store.close_task(
            task_id,
            candidate_digest="sha256:numeric-claim",
            verifier_session_id="numeric-verifier",
            claims={"1": "pass", "2": "pass", "EXTRA": "pass"},
            closure_packet=closure_packet,
            evidence_refs=evidence,
            verification_results=verification_results,
        )

    closed = store.close_task(
        task_id,
        candidate_digest="sha256:numeric-claim",
        verifier_session_id="numeric-verifier",
        claims={"1": "pass", "2": "pass"},
        closure_packet=closure_packet,
        evidence_refs=evidence,
        verification_results=verification_results,
    )
    assert closed["status"] == "closed"
    with pytest.raises(ValueError, match="closure_claim_identity_invalid"):
        store.close_task(
            task_id,
            candidate_digest="sha256:numeric-claim",
            verifier_session_id="numeric-verifier",
            claims={SpoofedString("1"): "pass", "2": "pass"},
            closure_packet=closure_packet,
            evidence_refs=evidence,
            verification_results=verification_results,
        )


def _strict_task_ready_for_closure(
    tmp_path, *, partial=False, uncovered_claim=False, suffix="full"
):
    sources = [
        _strict_source("REQ", "original_requirement", "build exact results", 10),
        _strict_source("PROTO", "approved_prototype", "results visual oracle", 20),
    ]
    store = TaskRuntimeStore(tmp_path / f"strict-closure-{suffix}.db")
    task = store.create_task(title="strict closure", original_sources=sources)
    spec = _strict_ui_spec()
    task = _review_and_freeze(
        store, task["task_id"], spec, expected_state_revision=task["state_revision"]
    )
    definition = _slice("s1")
    definition.update(
        {
            "source_refs": ["REQ", "PROTO"],
            "obligation_refs": spec["required_clauses"][0]["obligation_refs"],
            "source_anchors": spec["required_clauses"][0]["source_anchors"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual", "interaction", "responsive"],
        }
    )
    task = store.set_plan(
        task["task_id"], [definition], expected_state_revision=task["state_revision"]
    )
    packet = store.claim_next_slice(
        task["task_id"], worker_session_id="strict-worker", lease_seconds=60
    )
    assert packet is not None
    result = _result(packet, candidate="sha256:strict-candidate")
    evidence_obligations = definition["obligation_refs"]
    if partial:
        evidence_obligations = ["states:list/success"]
    semantic_evidence = [
        {
            "kind": "ui-fidelity",
            "ref": "uimax:strict-r1-pass",
            "claim_id": "R1",
            "obligation_refs": evidence_obligations,
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": definition["fidelity_dimensions"],
            "source_anchors": packet["claim_contracts"]["R1"]["source_anchors"],
            "candidate_digest": "sha256:strict-candidate",
            "source_digest": packet["source_digest"],
            "spec_digest": packet["spec_digest"],
        },
        {
            "kind": "ui-fidelity",
            "ref": "uimax:strict-n1-pass",
            "claim_id": "N1",
            "obligation_refs": ["negative_cases:no-generic-detail-error"],
            "oracle_refs": ["PROTO"],
            "fidelity_dimensions": ["visual"],
            "source_anchors": packet["claim_contracts"]["N1"]["source_anchors"],
            "candidate_digest": "sha256:strict-candidate",
            "source_digest": packet["source_digest"],
            "spec_digest": packet["spec_digest"],
        },
    ]
    worker_evidence = [dict(item) for item in semantic_evidence]
    result["evidence_refs"] = worker_evidence
    submitted = store.submit_result(
        task["task_id"], worker_session_id="strict-worker",
        packet_id=packet["packet_id"], result=result,
    )
    if partial:
        return store, task["task_id"], submitted, semantic_evidence, []
    store.integrate_result(
        task["task_id"], submitted["result_id"], integrator_session_id="strict-integrator"
    )
    closure_packet = store.build_closure_packet(
        task["task_id"], verifier_session_id="strict-verifier"
    )
    verification = [
        {
            "command": packet["verification_commands"][0],
            "exit_code": 0,
            "candidate_digest": "sha256:strict-candidate",
        }
    ]
    closure_evidence = semantic_evidence[:1] if uncovered_claim else semantic_evidence
    return store, task["task_id"], closure_packet, closure_evidence, verification


def test_strict_ui_closure_requires_obligation_oracle_and_candidate_binding(tmp_path):
    store, task_id, packet, evidence, verification = _strict_task_ready_for_closure(tmp_path)
    assert packet["obligation_digest"].startswith("sha256:")
    assert packet["oracle_refs"] == ["PROTO"]
    without_obligations = dict(evidence[0])
    without_obligations.pop("obligation_refs")
    with pytest.raises(ValueError, match="closure_obligation_evidence_incomplete"):
        store.close_task(
            task_id, candidate_digest="sha256:strict-candidate",
            verifier_session_id="strict-verifier", claims={"R1": "pass", "N1": "pass"},
            closure_packet=packet, evidence_refs=[without_obligations, evidence[1]],
            verification_results=verification,
        )
    wrong_oracle = dict(evidence[0], oracle_refs=["REQ"])
    with pytest.raises(ValueError, match="closure_oracle_binding_invalid"):
        store.close_task(
            task_id, candidate_digest="sha256:strict-candidate",
            verifier_session_id="strict-verifier", claims={"R1": "pass", "N1": "pass"},
            closure_packet=packet, evidence_refs=[wrong_oracle, evidence[1]],
            verification_results=verification,
        )
    closed = store.close_task(
        task_id, candidate_digest="sha256:strict-candidate",
        verifier_session_id="strict-verifier", claims={"R1": "pass", "N1": "pass"},
        closure_packet=packet, evidence_refs=evidence, verification_results=verification,
    )
    assert closed["status"] == "closed"
    assert closed["closure"]["obligation_digest"] == packet["obligation_digest"]
    assert closed["closure"]["oracle_refs"] == ["PROTO"]
    assert closed["closure"]["source_digest"] == packet["source_digest"]
    assert closed["closure"]["spec_digest"] == packet["spec_digest"]


def test_strict_ui_partial_evidence_is_rejected_before_integration(tmp_path):
    store, task_id, submitted, _evidence, _verification = _strict_task_ready_for_closure(
        tmp_path, partial=True, suffix="partial"
    )
    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_claim_contract_mismatch" in submitted["reasons"]
    current = store.get_task(task_id)
    assert current["status"] == "running"
    assert current["candidate_digest"] == ""
    assert current["evidence_graph"] == []


def test_strict_ui_each_required_claim_needs_closure_evidence(tmp_path):
    store, task_id, packet, evidence, verification = _strict_task_ready_for_closure(
        tmp_path, uncovered_claim=True, suffix="uncovered-claim"
    )
    with pytest.raises(ValueError, match="closure_evidence_claim_binding_incomplete"):
        store.close_task(
            task_id, candidate_digest="sha256:strict-candidate",
            verifier_session_id="strict-verifier", claims={"R1": "pass", "N1": "pass"},
            closure_packet=packet, evidence_refs=evidence,
            verification_results=verification,
        )


def test_strict_ui_worker_rejects_swapped_per_claim_semantic_contracts(tmp_path):
    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="swapped-worker-contract", worker_session_id="swap-worker"
    )
    result = _result(packet, candidate="sha256:swapped-contract")
    evidence = _numeric_claim_evidence(packet, definition)
    first_contract = {
        key: list(evidence[0][key])
        for key in ("obligation_refs", "oracle_refs", "fidelity_dimensions")
    }
    second_contract = {
        key: list(evidence[1][key])
        for key in ("obligation_refs", "oracle_refs", "fidelity_dimensions")
    }
    for key, value in second_contract.items():
        evidence[0][key] = value
    for key, value in first_contract.items():
        evidence[1][key] = value
    for item in evidence:
        item["candidate_digest"] = "sha256:swapped-contract"
    result["claims"] = ["1", "2"]
    result["evidence_refs"] = evidence

    submitted = store.submit_result(
        task_id,
        worker_session_id="swap-worker",
        packet_id=packet["packet_id"],
        result=result,
    )

    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_claim_contract_mismatch" in submitted["reasons"]


def test_strict_ui_worker_rejects_claim_source_anchor_drift(tmp_path):
    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="source-anchor-drift", worker_session_id="anchor-worker"
    )
    result = _result(packet, candidate="sha256:anchor-drift")
    evidence = _numeric_claim_evidence(packet, definition)
    for item in evidence:
        item["candidate_digest"] = "sha256:anchor-drift"
    evidence[0]["source_anchors"] = [
        {
            **evidence[0]["source_anchors"][0],
            "locator": "inline:PROTO@sha256:tampered#partial",
        }
    ]
    result["claims"] = ["1", "2"]
    result["evidence_refs"] = evidence

    submitted = store.submit_result(
        task_id,
        worker_session_id="anchor-worker",
        packet_id=packet["packet_id"],
        result=result,
    )

    assert submitted["disposition"] == "rejected"
    assert "worker_semantic_evidence_claim_contract_mismatch" in submitted["reasons"]


def test_strict_ui_closure_rejects_swapped_per_claim_semantic_contracts(tmp_path):
    store, task_id, packet, evidence, verification = _strict_task_ready_for_closure(
        tmp_path, suffix="swapped-closure-contract"
    )
    first_contract = {
        key: list(evidence[0][key])
        for key in ("obligation_refs", "oracle_refs", "fidelity_dimensions")
    }
    second_contract = {
        key: list(evidence[1][key])
        for key in ("obligation_refs", "oracle_refs", "fidelity_dimensions")
    }
    for key, value in second_contract.items():
        evidence[0][key] = value
    for key, value in first_contract.items():
        evidence[1][key] = value

    with pytest.raises(ValueError, match="closure_evidence_claim_contract_mismatch"):
        store.close_task(
            task_id,
            candidate_digest="sha256:strict-candidate",
            verifier_session_id="strict-verifier",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=packet,
            evidence_refs=evidence,
            verification_results=verification,
        )


def test_worker_rejects_non_native_governed_result_identities_before_replay(tmp_path):
    class SpoofedString(str):
        pass

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="governed-identities", worker_session_id="1"
    )
    valid = _result(packet, candidate="1")
    valid["claims"] = ["1", "2"]
    valid["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    for item in valid["evidence_refs"]:
        item["candidate_digest"] = 1

    rejected = store.submit_result(
        task_id,
        worker_session_id=SpoofedString("1"),
        packet_id=SpoofedString(packet["packet_id"]),
        result=dict(valid, candidate_digest=1),
    )

    assert rejected["disposition"] == "rejected"
    assert "worker_result_identity_invalid" in rejected["reasons"]
    with store._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM worker_results WHERE task_id = ? AND disposition != 'rejected'",
            (task_id,),
        ).fetchone()[0] == 0


def test_closure_rejects_non_native_governed_identities(tmp_path):
    class SpoofedString(str):
        pass

    store, task_id, packet, evidence, verification = _strict_task_ready_for_closure(
        tmp_path, suffix="closure-governed-identities"
    )
    evidence = [dict(item) for item in evidence]
    verification = [dict(item) for item in verification]
    for item in evidence:
        item["candidate_digest"] = SpoofedString(item["candidate_digest"])
    verification[0]["candidate_digest"] = SpoofedString(
        verification[0]["candidate_digest"]
    )

    with pytest.raises(ValueError, match="closure_identity_invalid"):
        store.close_task(
            task_id,
            candidate_digest=SpoofedString("sha256:strict-candidate"),
            verifier_session_id=SpoofedString("strict-verifier"),
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=packet,
            evidence_refs=evidence,
            verification_results=verification,
        )
    assert store.get_task(task_id)["status"] != "closed"


def test_context_packet_digest_requires_exact_native_identity(tmp_path):
    class SpoofedString(str):
        pass

    class SpoofedDict(dict):
        pass

    store = TaskRuntimeStore(tmp_path / "packet-digest-identity.db")
    payload = {"schema": "probe", "packet_id": "P"}
    digest = task_runtime_module._digest(payload)
    valid_packet = {**payload, "packet_digest": digest}

    assert store.verify_context_packet(valid_packet) is True
    assert (
        store.verify_context_packet(
            {**payload, "packet_digest": SpoofedString(digest)}
        )
        is False
    )
    assert store.verify_context_packet(SpoofedDict(valid_packet)) is False
    assert (
        store.verify_context_packet(
            {
                "schema": SpoofedString("probe"),
                "packet_id": "P",
                "packet_digest": digest,
            }
        )
        is False
    )


def test_context_packet_verification_rejects_cycles_and_excessive_depth(tmp_path):
    store = TaskRuntimeStore(tmp_path / "packet-bounds.db")
    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert (
        store.verify_context_packet(
            {"packet_digest": "sha256:not-reached", "payload": cyclic}
        )
        is False
    )

    deeply_nested: object = "leaf"
    for _ in range(110):
        deeply_nested = [deeply_nested]
    assert (
        store.verify_context_packet(
            {"packet_digest": "sha256:not-reached", "payload": deeply_nested}
        )
        is False
    )
    assert (
        store.verify_context_packet(
            {"packet_digest": "sha256:not-reached", "payload": [None] * 10_001}
        )
        is False
    )


def test_worker_result_rejects_cycles_and_excessive_depth_before_replay(tmp_path):
    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="worker-bounds", worker_session_id="bounded-worker"
    )
    valid_result = _result(packet, candidate="sha256:numeric-claim")
    valid_result["claims"] = ["1", "2"]
    valid_result["evidence_refs"] = _numeric_claim_evidence(packet, definition)

    invalid_results = []
    cyclic: list[object] = []
    cyclic.append(cyclic)
    cyclic_result = dict(valid_result)
    cyclic_result["risks"] = cyclic
    invalid_results.append(cyclic_result)

    deeply_nested: object = "leaf"
    for _ in range(110):
        deeply_nested = [deeply_nested]
    deep_result = dict(valid_result)
    deep_result["state_delta_proposal"] = {"nested": deeply_nested}
    invalid_results.append(deep_result)

    wide_result = dict(valid_result)
    wide_result["risks"] = [None] * 10_001
    invalid_results.append(wide_result)

    before_revision = store.get_task(task_id)["state_revision"]
    for result in invalid_results:
        rejected = store.submit_result(
            task_id,
            worker_session_id="bounded-worker",
            packet_id=packet["packet_id"],
            result=result,
        )
        assert rejected["disposition"] == "rejected"
        assert rejected.get("idempotent_replay") is not True
        assert "worker_result_invalid_field_types" in rejected["reasons"]

    assert store.get_task(task_id)["state_revision"] == before_revision
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM worker_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0


@pytest.mark.parametrize("field", ["slice_id", "input_digest"])
def test_worker_rejects_non_native_packet_binding_fields(tmp_path, field):
    class SpoofedString(str):
        pass

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix=f"worker-binding-{field}", worker_session_id="binding-worker"
    )
    result = _result(packet, candidate="sha256:numeric-claim")
    result["claims"] = ["1", "2"]
    result["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    result[field] = SpoofedString(result[field])

    rejected = store.submit_result(
        task_id,
        worker_session_id="binding-worker",
        packet_id=packet["packet_id"],
        result=result,
    )

    assert rejected["disposition"] == "rejected"
    assert "worker_result_identity_invalid" in rejected["reasons"]


def test_worker_and_closure_reject_non_native_evidence_ref(tmp_path):
    class SpoofedString(str):
        pass

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="worker-evidence-ref", worker_session_id="evidence-worker"
    )
    result = _result(packet, candidate="sha256:numeric-claim")
    result["claims"] = ["1", "2"]
    result["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    result["evidence_refs"][0]["ref"] = SpoofedString(
        result["evidence_refs"][0]["ref"]
    )
    rejected = store.submit_result(
        task_id,
        worker_session_id="evidence-worker",
        packet_id=packet["packet_id"],
        result=result,
    )
    assert rejected["disposition"] == "rejected"
    assert "worker_evidence_refs_invalid" in rejected["reasons"]

    closure_store, closure_task_id, closure_packet, evidence, verification = (
        _strict_task_ready_for_closure(tmp_path, suffix="closure-evidence-ref")
    )
    evidence = [dict(item) for item in evidence]
    evidence[0]["ref"] = SpoofedString(evidence[0]["ref"])
    with pytest.raises(ValueError, match="closure_evidence_required"):
        closure_store.close_task(
            closure_task_id,
            candidate_digest="sha256:strict-candidate",
            verifier_session_id="strict-verifier",
            claims={"R1": "pass", "N1": "pass"},
            closure_packet=closure_packet,
            evidence_refs=evidence,
            verification_results=verification,
        )
    assert closure_store.get_task(closure_task_id)["status"] != "closed"


def test_integrator_rejects_non_native_result_identity_before_mutation(tmp_path):
    class SpoofedString(str):
        pass

    store, task_id, definition, packet = _numeric_claim_strict_packet(
        tmp_path, suffix="integrator-result-id", worker_session_id="result-worker"
    )
    result = _result(packet, candidate="sha256:numeric-claim")
    result["claims"] = ["1", "2"]
    result["evidence_refs"] = _numeric_claim_evidence(packet, definition)
    accepted = store.submit_result(
        task_id,
        worker_session_id="result-worker",
        packet_id=packet["packet_id"],
        result=result,
    )
    assert accepted["disposition"] == "accepted_for_integration"
    before = store.get_task(task_id)

    with pytest.raises(ValueError, match="result_id_required"):
        store.integrate_result(
            task_id,
            SpoofedString(accepted["result_id"]),
            integrator_session_id="independent-integrator",
        )

    after = store.get_task(task_id)
    assert after["state_revision"] == before["state_revision"]
    assert after["status"] == before["status"]


def test_failed_legacy_owner_upgrade_rolls_back_schema_and_retries_deterministically(tmp_path):
    path = tmp_path / "legacy-owner-transaction.db"
    seed = TaskRuntimeStore(path)
    task = seed.create_task(
        title="legacy owner migration",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE tasks DROP COLUMN owner_profile")
        conn.execute("DROP TRIGGER task_state_events_no_delete")
        conn.execute("DELETE FROM task_state_events")
        conn.execute(
            "UPDATE tasks SET original_sources_json = ? WHERE task_id = ?",
            ('[{"source_id":1,"content":"invalid"}]', task["task_id"]),
        )

    with pytest.raises(ValueError, match="original_source_identity_invalid"):
        TaskRuntimeStore(path, owner_profile="profile-a")

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "owner_profile" not in columns
        conn.execute(
            "UPDATE tasks SET original_sources_json = ? WHERE task_id = ?",
            ('[{"source_id":"S0","content":"original"}]', task["task_id"]),
        )

    reopened = TaskRuntimeStore(path, owner_profile="profile-a")
    assert reopened.get_task(task["task_id"])["owner_profile"] == "profile-a"


def test_sql_script_transactional_executor_handles_multiple_statements_per_line():
    with sqlite3.connect(":memory:") as conn:
        task_runtime_module._execute_sql_script_transactionally(
            conn,
            "CREATE TABLE items (value INTEGER); "
            "INSERT INTO items VALUES (1); "
            "CREATE TRIGGER no_more BEFORE INSERT ON items BEGIN "
            "SELECT RAISE(ABORT, 'no_more'); END;",
        )
        assert conn.execute("SELECT value FROM items").fetchall() == [(1,)]
        with pytest.raises(sqlite3.IntegrityError, match="no_more"):
            conn.execute("INSERT INTO items VALUES (2)")


def test_legacy_upgrade_rolls_back_schema_and_data_when_post_ddl_backfill_fails(tmp_path):
    path = tmp_path / "legacy-post-ddl-failure.db"
    seed = TaskRuntimeStore(path)
    task = seed.create_task(
        title="post-DDL migration failure",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER task_state_events_no_delete")
        conn.execute("DELETE FROM task_state_events")
        conn.execute("DROP TABLE spec_reviews")
        conn.execute("ALTER TABLE tasks DROP COLUMN owner_profile")
        conn.execute(
            "UPDATE tasks SET source_events_json = ? WHERE task_id = ?",
            ('[{"event_id":"E1","source_id":"S0","content":"correction"}]', task["task_id"]),
        )
        conn.execute(
            "CREATE TRIGGER abort_source_event_backfill "
            "BEFORE INSERT ON source_events BEGIN "
            "SELECT RAISE(ABORT, 'injected_backfill_failure'); END"
        )
        before = tuple(conn.iterdump())

    with pytest.raises(sqlite3.IntegrityError, match="injected_backfill_failure"):
        TaskRuntimeStore(path, owner_profile="profile-a")

    with sqlite3.connect(path) as conn:
        after = tuple(conn.iterdump())
        assert after == before
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "owner_profile" not in columns
        assert conn.execute("SELECT COUNT(*) FROM source_events").fetchone()[0] == 0
        conn.execute("DROP TRIGGER abort_source_event_backfill")

    reopened = TaskRuntimeStore(path, owner_profile="profile-a")
    migrated = reopened.get_task(task["task_id"])
    assert migrated["owner_profile"] == "profile-a"
    assert [event["event_id"] for event in migrated["source_events"]] == ["E1"]


def test_legacy_backfill_without_recorded_at_is_deterministic_across_identical_copies(
    tmp_path, monkeypatch
):
    seed_path = tmp_path / "legacy-deterministic-seed.db"
    seed = TaskRuntimeStore(seed_path)
    task = seed.create_task(
        title="deterministic legacy event",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with sqlite3.connect(seed_path) as conn:
        conn.execute(
            "UPDATE tasks SET source_events_json = ? WHERE task_id = ?",
            (
                '[{"event_id":"E2","content":false},'
                '{"event_id":"E1","content":0}]',
                task["task_id"],
            ),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    paths = [tmp_path / "legacy-copy-a.db", tmp_path / "legacy-copy-b.db"]
    for copy_path in paths:
        shutil.copyfile(seed_path, copy_path)

    snapshots = []
    for index, copy_path in enumerate(paths, start=1):
        monkeypatch.setattr(
            task_runtime_module,
            "_utc_now",
            lambda index=index: f"2026-08-28T00:00:0{index}Z",
        )
        reopened = TaskRuntimeStore(copy_path)
        with sqlite3.connect(copy_path) as conn:
            conn.row_factory = sqlite3.Row
            task_row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()
            assert task_row is not None
            snapshot = reopened._source_snapshot(conn, task_row)
        snapshots.append(snapshot)

    assert snapshots[0] == snapshots[1]
    assert task_runtime_module._digest(snapshots[0]) == task_runtime_module._digest(
        snapshots[1]
    )
    assert [event["event_id"] for event in snapshots[0]["source_events"]] == [
        "E2",
        "E1",
    ]
    migrated_contents = [event["content"] for event in snapshots[0]["source_events"]]
    assert type(migrated_contents[0]) is bool and migrated_contents[0] is False
    assert type(migrated_contents[1]) is int and migrated_contents[1] == 0


def test_legacy_upgrade_rejects_source_artifact_event_identity_collision_before_schema_mutation(
    tmp_path,
):
    path = tmp_path / "legacy-source-namespace-collision.db"
    seed = TaskRuntimeStore(path)
    task = seed.create_task(
        title="legacy source namespace collision",
        original_sources=[{"source_id": "SAME", "content": "original"}],
    )
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE spec_reviews")
        conn.execute("ALTER TABLE tasks DROP COLUMN owner_profile")
        conn.execute(
            "UPDATE tasks SET source_events_json = ? WHERE task_id = ?",
            ('[{"event_id":"SAME","content":"correction"}]', task["task_id"]),
        )
        before = tuple(conn.iterdump())

    with pytest.raises(ValueError, match="task_source_identity_invalid"):
        TaskRuntimeStore(path, owner_profile="profile-a")

    with sqlite3.connect(path) as conn:
        assert tuple(conn.iterdump()) == before
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "owner_profile" not in columns
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'spec_reviews'"
        ).fetchone()[0] == 0


def test_concurrent_fresh_initializers_share_one_atomic_schema(tmp_path):
    initializer_count = 12
    for round_index in range(8):
        path = tmp_path / f"concurrent-fresh-init-{round_index}.db"
        barrier = threading.Barrier(initializer_count)

        def initialize(_: int) -> str:
            barrier.wait(timeout=10)
            return TaskRuntimeStore(path).owner_profile

        with ThreadPoolExecutor(max_workers=initializer_count) as pool:
            owners = list(pool.map(initialize, range(initializer_count)))

        assert len(set(owners)) == 1
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()[0] == 1


def test_post_commit_wal_failure_is_recoverable_without_schema_divergence(
    tmp_path, monkeypatch
):
    path = tmp_path / "post-commit-wal-retry.db"
    original = TaskRuntimeStore._ensure_wal_mode
    calls = 0

    def fail_once(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("injected_wal_failure")
        return original(self)

    monkeypatch.setattr(TaskRuntimeStore, "_ensure_wal_mode", fail_once)
    with pytest.raises(sqlite3.OperationalError, match="injected_wal_failure"):
        TaskRuntimeStore(path)

    recovered = TaskRuntimeStore(path)
    assert recovered.owner_profile
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()[0] == 1


def test_legacy_nonempty_source_collection_requires_native_content_on_upgrade(tmp_path):
    path = tmp_path / "legacy-missing-content.db"
    seed = TaskRuntimeStore(path)
    task = seed.create_task(
        title="legacy source content",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE tasks SET original_sources_json = ? WHERE task_id = ?",
            ('[{"source_id":"S0"}]', task["task_id"]),
        )

    with pytest.raises(ValueError, match="original_source_content_invalid"):
        TaskRuntimeStore(path)

    with sqlite3.connect(path) as conn:
        persisted = conn.execute(
            "SELECT original_sources_json FROM tasks WHERE task_id = ?", (task["task_id"],)
        ).fetchone()[0]
    assert persisted == '[{"source_id":"S0"}]'


def test_source_event_source_ref_requires_native_existing_artifact_before_mutation(tmp_path):
    class SpoofedString(str):
        pass

    store = TaskRuntimeStore(tmp_path / "source-event-source-ref.db")
    task = store.create_task(
        title="source event source ref",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    for source_id, error in (
        (1, "source_event_source_identity_invalid"),
        (True, "source_event_source_identity_invalid"),
        ("", "source_event_source_identity_invalid"),
        ("   ", "source_event_source_identity_invalid"),
        (SpoofedString("S0"), "source_event_source_identity_invalid"),
        ("missing", "source_event_source_ref_unknown"),
    ):
        with pytest.raises(ValueError, match=error):
            store.append_source_event(
                task["task_id"],
                {"event_id": f"E-{type(source_id).__name__}-{source_id!r}", "source_id": source_id, "content": "correction"},
            )
    current = store.get_task(task["task_id"])
    assert current["state_revision"] == task["state_revision"]
    assert current["source_events"] == []

    accepted = store.append_source_event(
        task["task_id"],
        {"event_id": "E-valid", "source_id": "S0", "content": "valid correction"},
    )
    assert accepted["state_revision"] == task["state_revision"] + 1
    assert accepted["source_events"][0]["source_id"] == "S0"


def test_source_event_identity_cannot_collide_with_source_artifact_before_mutation(tmp_path):
    store = TaskRuntimeStore(tmp_path / "source-namespace-collision.db")
    task = store.create_task(
        title="source namespace collision",
        original_sources=[{"source_id": "SAME", "content": "original"}],
    )

    with pytest.raises(ValueError, match="task_source_identity_invalid"):
        store.append_source_event(
            task["task_id"], {"event_id": "SAME", "content": "correction"}
        )

    unchanged = store.get_task(task["task_id"])
    assert unchanged["state_revision"] == task["state_revision"]
    assert unchanged["source_events"] == []


def test_source_event_identity_rejects_non_string_before_mutation(tmp_path):
    class SpoofedString(str):
        pass

    store = TaskRuntimeStore(tmp_path / "source-event-identity.db")
    task = store.create_task(
        title="source event identity",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    for invalid_event_id in (1, SpoofedString("E1")):
        with pytest.raises(ValueError, match="source_event_identity_invalid"):
            store.append_source_event(
                task["task_id"],
                {"event_id": invalid_event_id, "content": "correction"},
            )
    current = store.get_task(task["task_id"])
    assert current["state_revision"] == task["state_revision"]
    assert current["source_events"] == []


def test_source_event_live_admission_preserves_false_and_zero_scalar_content(tmp_path):
    store = TaskRuntimeStore(tmp_path / "source-event-false-zero-content.db")
    task = store.create_task(
        title="source event scalar content parity",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )

    for index, content in enumerate((False, 0), start=1):
        updated = store.append_source_event(
            task["task_id"],
            {"event_id": f"E{index}", "content": content},
        )
        observed = updated["source_events"][-1]["content"]
        assert type(observed) is type(content)
        assert observed == content


def test_source_event_replay_uses_json_type_sensitive_semantics(tmp_path):
    store = TaskRuntimeStore(tmp_path / "source-event-type-sensitive-replay.db")
    task = store.create_task(
        title="source event type-sensitive replay",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    appended = store.append_source_event(
        task["task_id"], {"event_id": "E1", "content": 1}
    )
    with pytest.raises(ValueError, match="source_event_id_conflict"):
        store.append_source_event(
            task["task_id"], {"event_id": "E1", "content": True}
        )
    current = store.get_task(task["task_id"])
    assert current["state_revision"] == appended["state_revision"]
    assert len(current["source_events"]) == 1
    assert type(current["source_events"][0]["content"]) is int
    assert current["source_events"][0]["content"] == 1


@pytest.mark.parametrize(
    "content",
    [
        {1: "coerced-key"},
        ("coerced-sequence",),
        float("nan"),
    ],
    ids=["non-string-object-key", "tuple-to-array", "non-finite-number"],
)
def test_source_event_rejects_non_native_json_payload_before_mutation(
    tmp_path, content
):
    store = TaskRuntimeStore(tmp_path / "source-event-native-json.db")
    task = store.create_task(
        title="source event native JSON",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    with pytest.raises(ValueError, match="source_event_payload_invalid"):
        store.append_source_event(
            task["task_id"], {"event_id": "E1", "content": content}
        )
    current = store.get_task(task["task_id"])
    assert current["state_revision"] == task["state_revision"]
    assert current["source_events"] == []


def test_source_event_rejects_explicit_null_identity_and_malformed_timestamp_before_mutation(
    tmp_path,
):
    class SpoofedString(str):
        pass

    invalid_events = [
        {"event_id": None, "content": "null identity"},
        {"event_id": "E1", "content": "numeric timestamp", "recorded_at": 7},
        {"event_id": "E2", "content": "blank timestamp", "recorded_at": ""},
        {
            "event_id": "E3",
            "content": "subclass timestamp",
            "recorded_at": SpoofedString("2026-08-28T00:00:00Z"),
        },
    ]

    for index, event in enumerate(invalid_events):
        store = TaskRuntimeStore(tmp_path / f"live-source-admission-{index}.db")
        task = store.create_task(
            title="live source admission",
            original_sources=[{"source_id": "S0", "content": "original"}],
        )
        expected = (
            "source_event_identity_invalid"
            if event.get("event_id", object()) is None
            else "source_event_recorded_at_invalid"
        )
        with pytest.raises(ValueError, match=expected):
            store.append_source_event(task["task_id"], event)
        current = store.get_task(task["task_id"])
        assert current["state_revision"] == task["state_revision"]
        assert current["source_events"] == []


def test_source_event_authority_uses_append_order_not_caller_timestamp(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        task_runtime_module, "_utc_now", lambda: "2026-08-27T00:00:00+00:00"
    )
    store = TaskRuntimeStore(tmp_path / "source-event-order.db")
    task = store.create_task(
        title="source event order",
        original_sources=[{"source_id": "S0", "content": "original"}],
    )
    task = store.append_source_event(
        task["task_id"],
        {
            "event_id": "E1",
            "content": "first correction",
            "recorded_at": "2999-01-01T00:00:00+00:00",
        },
    )
    task = store.append_source_event(
        task["task_id"],
        {
            "event_id": "E2",
            "content": "latest correction",
            "recorded_at": "1999-01-01T00:00:00+00:00",
        },
    )
    assert [item["event_id"] for item in task["source_events"]] == ["E1", "E2"]
    assert task["source_events"][0]["recorded_at"] != "2999-01-01T00:00:00+00:00"
    assert task["source_events"][1]["recorded_at"] != "1999-01-01T00:00:00+00:00"
    review = store.record_spec_review(
        task["task_id"],
        _spec_for_sources("E2", "E1", "S0"),
        reviewer_session_id="append-order-reviewer",
        verdict="approved",
        findings=[],
        expected_state_revision=task["state_revision"],
    )
    assert review["verdict"] == "approved"


def test_material_source_change_invalidates_semantic_chain(tmp_path):
    store, task_id, packet, evidence, verification = _strict_task_ready_for_closure(
        tmp_path, suffix="source-change"
    )
    closed = store.close_task(
        task_id, candidate_digest="sha256:strict-candidate",
        verifier_session_id="strict-verifier", claims={"R1": "pass", "N1": "pass"},
        closure_packet=packet, evidence_refs=evidence, verification_results=verification,
    )
    prior_review_ids = [item["review_id"] for item in closed["spec_reviews"]]
    new_source = _strict_source(
        "VIEWPORT390", "original_requirement", "also support 390x844 viewport", 15
    )
    new_source["event_id"] = new_source.pop("source_id")
    invalidated = store.append_source_event(task_id, new_source)
    assert invalidated["status"] == "draft"
    assert invalidated["implementation_graph"] == []
    assert invalidated["slices"] == []
    assert invalidated["candidate_digest"] == ""
    assert invalidated["evidence_graph"] == []
    assert invalidated["closure"] == {}
    payload = invalidated["state_events"][-1]["payload"]
    assert payload["event_id"] == "VIEWPORT390"
    assert payload["spec_invalidated"] is True
    assert payload["closure_invalidated"] is True
    assert [item["review_id"] for item in invalidated["spec_reviews"]] == prior_review_ids
    with pytest.raises(ValueError, match="task_not_claimable"):
        store.claim_next_slice(
            task_id, worker_session_id="stale-worker", lease_seconds=60
        )
    revised = _strict_ui_spec()
    revised["governance"]["source_authority"] = ["REQ", "VIEWPORT390", "PROTO"]
    revised["governance"]["source_coverage"]["VIEWPORT390"] = ["R1"]
    revised["required_clauses"][0]["source_refs"].append("VIEWPORT390")
    with pytest.raises(ValueError, match="independent_spec_review_required"):
        store.freeze_spec(
            task_id, revised, compiler_session_id="stale-compiler",
            expected_state_revision=invalidated["state_revision"],
        )


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
    spec["governance"]["discovery"]["open_source"]["evaluation"]["license"] = 1
    with pytest.raises(ValueError, match="open_source_evaluation_incomplete"):
        store.record_spec_review(
            task["task_id"],
            spec,
            reviewer_session_id="oss-reviewer-wrong-type",
            verdict="approved",
            findings=[],
            expected_state_revision=task["state_revision"],
        )
    spec["governance"]["discovery"]["open_source"]["evaluation"]["license"] = (
        "compatible license recorded"
    )
    for invalid_pin in (
        "exact version and integrity pin recorded",
        "tag:refs/heads/main",
        "version:HEAD~1",
        "version:latest+1",
        "1.2",
        "1.2.3.4",
        "version:1.2.3.4",
        "pkg==1.2",
        "commit:abcdef0",
        "1.2.3-..",
        "1٢.2.3",
        "1.2٢.3",
        "1.2.3-1٢",
        "version:1٢.2.3",
        "pkg==1.2٢.3",
    ):
        spec["governance"]["discovery"]["open_source"]["evaluation"][
            "version_pin"
        ] = invalid_pin
        with pytest.raises(ValueError, match="open_source_version_pin_invalid"):
            store.record_spec_review(
                task["task_id"],
                spec,
                reviewer_session_id=f"oss-reviewer-invalid-pin-{invalid_pin}",
                verdict="approved",
                findings=[],
                expected_state_revision=task["state_revision"],
            )
    spec["governance"]["discovery"]["open_source"]["evaluation"][
        "version_pin"
    ] = "version:1.2.3"
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


def test_source_events_use_runtime_append_order_and_reject_coerced_identity(tmp_path):
    store = TaskRuntimeStore(tmp_path / "source-event-order.db")
    task = store.create_task(
        title="source event order",
        original_sources=[
            {"source_id": "S0", "kind": "user_message", "content": "original"}
        ],
    )
    with pytest.raises(ValueError, match="source_event_identity_invalid"):
        store.append_source_event(
            task["task_id"],
            {"event_id": 1, "content": "numeric identity"},
        )

    store.append_source_event(
        task["task_id"],
        {
            "event_id": "E-first",
            "kind": "user_correction",
            "content": "first append",
            "recorded_at": "2999-01-01T00:00:00+00:00",
        },
    )
    latest = store.append_source_event(
        task["task_id"],
        {
            "event_id": "E-latest",
            "kind": "user_correction",
            "content": "latest append",
            "recorded_at": "1900-01-01T00:00:00+00:00",
        },
    )
    assert latest["source_events"][0]["event_id"] == "E-first"
    assert latest["source_events"][1]["event_id"] == "E-latest"
    assert latest["source_events"][0]["recorded_at"] != "2999-01-01T00:00:00+00:00"
    assert latest["source_events"][1]["recorded_at"] != "1900-01-01T00:00:00+00:00"

    spec = _spec_for_sources("E-latest", "E-first", "S0")
    approved = store.record_spec_review(
        task["task_id"],
        spec,
        reviewer_session_id="source-order-reviewer",
        verdict="approved",
        findings=[],
        expected_state_revision=latest["state_revision"],
    )
    assert approved["verdict"] == "approved"
