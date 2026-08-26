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
        session_id="integrator",
    )["integration"]
    assert integrated["slice_status"] == "completed"
    closure_packet = _call(
        tmp_path,
        "closure_context",
        task_id=task_id,
        session_id="closure-fresh",
    )["closure_packet"]
    closed = _call(
        tmp_path,
        "close",
        task_id=task_id,
        candidate_digest="sha256:final",
        session_id="closure-fresh",
        claims={"R1": "pass"},
        closure_packet=closure_packet,
        closure_evidence_refs=[
            {"ref": "test", "candidate_digest": "sha256:final"}
        ],
        closure_verification_results=[
            {
                "command": "pytest -q",
                "exit_code": 0,
                "candidate_digest": "sha256:final",
            }
        ],
    )["task"]
    assert closed["status"] == "closed"


def test_tool_actor_identity_is_runtime_bound_and_not_model_overridable(tmp_path):
    created = _call(
        tmp_path,
        "create",
        title="identity bound",
        original_sources=[{"source_id": "S1", "content": "identity"}],
        session_id="actual-session",
    )
    task_id = created["task"]["task_id"]

    closure = _call(
        tmp_path,
        "closure_context",
        task_id=task_id,
        session_id="actual-session",
        verifier_session_id="forged-session",
    )
    assert closure["error"] == "verifier_session_override_forbidden"

    integration = _call(
        tmp_path,
        "integrate",
        task_id=task_id,
        result_id="result-does-not-matter",
        session_id="actual-session",
        integrator_session_id="forged-session",
    )
    assert integration["error"] == "integrator_session_override_forbidden"

    entry = registry.get_entry("task_runtime")
    assert entry is not None
    properties = entry.schema["parameters"]["properties"]
    assert "verifier_session_id" not in properties
    assert "integrator_session_id" not in properties


def test_registry_dispatch_uses_runtime_platform_when_entrypoint_is_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_TASK_RUNTIME_DB", str(tmp_path / "runtime.db"))
    created = json.loads(
        registry.dispatch(
            "task_runtime",
            {
                "action": "create",
                "title": "telegram task",
                "original_sources": [{"source_id": "S1", "kind": "user_message", "content": "from telegram"}],
            },
            session_id="gateway-session",
            platform="telegram",
        )
    )
    assert created["task"]["bindings"][0]["entrypoint"] == "telegram"


def test_tool_is_registered_and_in_default_core_toolset():
    assert registry.get_entry("task_runtime") is not None
    assert "task_runtime" in resolve_toolset("hermes-cli")
