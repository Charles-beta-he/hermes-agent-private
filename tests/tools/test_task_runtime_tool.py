from __future__ import annotations

import json

from toolsets import resolve_toolset
from tools.registry import registry
from tools.task_runtime_tool import task_runtime_tool


def _call(tmp_path, action: str, **kwargs):
    return json.loads(
        task_runtime_tool(
            action=action,
            db_path=tmp_path / "runtime.db",
            session_id=kwargs.pop("session_id", "cli-1"),
            entrypoint=kwargs.pop("entrypoint", "cli"),
            **kwargs,
        )
    )


def test_create_status_and_cross_entrypoint_bind_use_current_session(tmp_path):
    created = _call(
        tmp_path,
        "create",
        title="portable task",
        original_sources=[{"source_id": "S1", "kind": "user_message", "content": "do it"}],
    )
    task_id = created["task"]["task_id"]
    assert created["task"]["bindings"][0]["session_id"] == "cli-1"

    status = _call(tmp_path, "status")
    assert status["task"]["task_id"] == task_id

    bound = _call(
        tmp_path,
        "bind",
        task_id=task_id,
        entrypoint="telegram",
        session_id="telegram:42:7",
    )
    assert bound["task"]["task_id"] == task_id
    telegram_status = _call(
        tmp_path,
        "status",
        entrypoint="telegram",
        session_id="telegram:42:7",
    )
    assert telegram_status["task"]["task_id"] == task_id


def test_full_tool_control_plane_flow(tmp_path):
    created = _call(
        tmp_path,
        "create",
        title="flow",
        original_sources=[{"source_id": "S1", "kind": "user_message", "content": "flow source"}],
    )
    task = created["task"]
    task_id = task["task_id"]
    specified = _call(
        tmp_path,
        "freeze_spec",
        task_id=task_id,
        expected_state_revision=task["state_revision"],
        spec_bundle={
            "required_clauses": [{"id": "R1", "text": "works"}],
            "negative_requirements": [],
            "closure_criteria": ["R1"],
        },
    )["task"]
    planned = _call(
        tmp_path,
        "set_plan",
        task_id=task_id,
        expected_state_revision=specified["state_revision"],
        slices=[{
            "slice_id": "s1",
            "role": "implementer",
            "objective": "implement",
            "dependencies": [],
            "required_clauses": ["R1"],
            "negative_requirements": [],
            "source_refs": ["S1"],
            "architecture_invariants": [],
            "owned_paths": ["src/a.py"],
            "forbidden_changes": [],
            "verification_commands": ["pytest -q"],
            "authority": {"side_effect_ceiling": "local_reversible"},
        }],
    )["task"]
    assert planned["plan_revision"] == 1

    packet = _call(tmp_path, "claim", task_id=task_id, worker_session_id="worker-fresh")["context_packet"]
    result = {
        "slice_id": "s1",
        "input_digest": packet["packet_digest"],
        "claims": ["R1"],
        "changed_artifacts": ["src/a.py"],
        "evidence_refs": [{"ref": "test"}],
        "verification_results": [{"command": "pytest -q", "exit_code": 0}],
        "risks": [],
        "unknowns": [],
        "state_delta_proposal": {"slice_status": "completed"},
        "candidate_digest": "sha256:final",
    }
    submitted = _call(
        tmp_path,
        "submit",
        task_id=task_id,
        worker_session_id="worker-fresh",
        packet_id=packet["packet_id"],
        worker_result=result,
    )["worker_result"]
    assert submitted["disposition"] == "accepted_for_integration"
    integrated = _call(
        tmp_path,
        "integrate",
        task_id=task_id,
        result_id=submitted["result_id"],
        integrator_session_id="integrator",
    )["integration"]
    assert integrated["slice_status"] == "completed"
    closed = _call(
        tmp_path,
        "close",
        task_id=task_id,
        candidate_digest="sha256:final",
        verifier_session_id="closure-fresh",
        claims={"R1": "pass"},
    )["task"]
    assert closed["status"] == "closed"


def test_tool_is_registered_and_in_default_core_toolset():
    assert registry.get_entry("task_runtime") is not None
    assert "task_runtime" in resolve_toolset("hermes-cli")
