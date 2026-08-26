from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

import agent.task_runtime_worker as worker_module
from agent.task_runtime import TaskRuntimeStore
from agent.task_runtime_worker import dispatch_next_slice
from tools.registry import registry
import tools.task_runtime_worker_tool  # noqa: F401 -- register test tool
from toolsets import resolve_toolset


SPEC = {
    "required_clauses": [{"id": "R1", "text": "implement"}],
    "negative_requirements": [{"id": "N1", "text": "no forbidden writes"}],
    "closure_criteria": ["R1", "N1"],
}


def _ready(tmp_path):
    store = TaskRuntimeStore(tmp_path / "runtime.db")
    task = store.create_task(
        title="worker dispatch",
        original_sources=[{"source_id": "s1", "kind": "user_message", "content": "do work"}],
    )
    task = store.freeze_spec(task["task_id"], SPEC, expected_state_revision=task["state_revision"])
    store.set_plan(
        task["task_id"],
        [{
            "slice_id": "slice-1",
            "role": "implementer",
            "objective": "implement slice",
            "dependencies": [],
            "required_clauses": ["R1"],
            "negative_requirements": ["N1"],
            "source_refs": ["s1"],
            "architecture_invariants": ["fresh session"],
            "owned_paths": ["src/x.py"],
            "forbidden_changes": ["config.yaml"],
            "verification_commands": ["pytest -q"],
            "authority": {"side_effect_ceiling": "local_reversible"},
        }],
        expected_state_revision=task["state_revision"],
    )
    return store, task["task_id"]


def test_dispatch_uses_fresh_claim_owner_and_submits_structured_result(tmp_path):
    store, task_id = _ready(tmp_path)
    built = {}

    def builder(*, worker_session_id, packet, parent_agent):
        built.update(worker_session_id=worker_session_id, packet=packet, parent_agent=parent_agent)
        return SimpleNamespace(session_id=worker_session_id)

    def runner(*, child, goal, parent_agent):
        packet = built["packet"]
        result = {
            "slice_id": packet["slice_id"],
            "input_digest": packet["packet_digest"],
            "claims": ["R1", "N1"],
            "changed_artifacts": ["src/x.py"],
            "evidence_refs": [{"kind": "test", "ref": "pytest:passed"}],
            "verification_results": [{"command": "pytest -q", "exit_code": 0}],
            "risks": [],
            "unknowns": [],
            "state_delta_proposal": {"slice_status": "completed"},
            "candidate_digest": "sha256:candidate",
        }
        return {"status": "completed", "summary": json.dumps(result), "child_session_id": child.session_id}

    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent-session"),
        task_id=task_id,
        store=store,
        child_builder=builder,
        child_runner=runner,
        lease_seconds=60,
    )

    assert receipt["success"] is True
    assert receipt["worker_session_id"].startswith("task-worker-")
    assert built["worker_session_id"] == receipt["worker_session_id"]
    assert receipt["worker_result"]["disposition"] == "accepted_for_integration"
    task = store.get_task(task_id)
    assert task["slices"][0]["status"] == "submitted"


def test_dispatch_rejects_non_json_worker_output_without_integrating(tmp_path):
    store, task_id = _ready(tmp_path)

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(**kwargs):
        return {"status": "completed", "summary": "I think it is done"}

    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent-session"),
        task_id=task_id,
        store=store,
        child_builder=builder,
        child_runner=runner,
    )
    assert receipt["success"] is False
    assert receipt["error"] == "worker_result_not_json"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"


def test_worker_exception_releases_claim_for_retry(tmp_path):
    store, task_id = _ready(tmp_path)

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(**kwargs):
        raise RuntimeError("worker crashed")

    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent-session"),
        task_id=task_id,
        store=store,
        child_builder=builder,
        child_runner=runner,
    )
    assert receipt["success"] is False
    assert receipt["error"] == "worker_execution_failed"
    assert receipt["error_type"] == "RuntimeError"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"


@pytest.mark.parametrize(
    ("execution_status", "expected_error"),
    [("timeout", "worker_timeout"), ("cancelled", "worker_cancelled"), ("error", "worker_execution_failed")],
)
def test_worker_terminal_execution_status_releases_claim_for_retry(
    tmp_path, execution_status, expected_error
):
    store, task_id = _ready(tmp_path)

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(*, child, goal, parent_agent):
        return {
            "status": execution_status,
            "summary": None,
            "error": f"simulated {execution_status}",
            "exit_reason": execution_status,
        }

    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent-session"),
        task_id=task_id,
        store=store,
        child_builder=builder,
        child_runner=runner,
    )

    assert receipt["success"] is False
    assert receipt["error"] == expected_error
    assert receipt["worker_execution"]["status"] == execution_status
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"


def test_worker_pool_never_exceeds_configured_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "_WORKER_SLOTS", threading.BoundedSemaphore(2))
    lock = threading.Lock()
    two_active = threading.Event()
    active = 0
    maximum = 0
    receipts = []
    errors = []
    jobs = [_ready(tmp_path / f"job-{index}") for index in range(4)]

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(*, child, goal, parent_agent):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_active.set()
        assert two_active.wait(timeout=5)
        time.sleep(0.05)
        packet_json = goal.split("CONTEXT_PACKET_JSON=", 1)[1]
        packet = json.loads(packet_json)
        result = {
            "slice_id": packet["slice_id"],
            "input_digest": packet["packet_digest"],
            "claims": ["R1", "N1"],
            "changed_artifacts": ["src/x.py"],
            "evidence_refs": [{"kind": "test", "ref": "pool-cap"}],
            "verification_results": [{"command": "pytest -q", "exit_code": 0}],
            "risks": [],
            "unknowns": [],
            "state_delta_proposal": {"slice_status": "completed"},
            "candidate_digest": "sha256:pool-cap",
        }
        with lock:
            active -= 1
        return {"status": "completed", "summary": json.dumps(result)}

    def dispatch(job):
        store, task_id = job
        try:
            receipts.append(dispatch_next_slice(
                SimpleNamespace(session_id="parent-session"),
                task_id=task_id,
                store=store,
                child_builder=builder,
                child_runner=runner,
            ))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=dispatch, args=(job,)) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert errors == []
    assert maximum == 2
    assert len(receipts) == 4
    assert all(receipt["success"] for receipt in receipts)


def test_worker_tool_is_exposed_but_registry_cannot_bypass_live_agent():
    assert "task_runtime_worker" in resolve_toolset("hermes-cli")
    payload = registry.dispatch("task_runtime_worker", {"task_id": "t1"})
    assert payload == {"success": False, "error": "task_runtime_worker_requires_live_agent"}


def test_default_executor_forces_child_session_to_lease_owner(tmp_path, monkeypatch):
    store, task_id = _ready(tmp_path)
    captured = {}

    def fake_build_child_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(session_id=kwargs["forced_session_id"])

    def fake_run_single_child(index, goal, child, parent):
        packet = captured["context"].split("CONTEXT_PACKET_JSON=", 1)[1]
        packet = json.loads(packet)
        result = {
            "slice_id": packet["slice_id"],
            "input_digest": packet["packet_digest"],
            "claims": ["R1", "N1"],
            "changed_artifacts": ["src/x.py"],
            "evidence_refs": [{"kind": "test", "ref": "pytest:passed"}],
            "verification_results": [{"command": "pytest -q", "exit_code": 0}],
            "risks": [], "unknowns": [],
            "state_delta_proposal": {"slice_status": "completed"},
            "candidate_digest": "sha256:candidate",
        }
        return {"status": "completed", "summary": json.dumps(result)}

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", fake_build_child_agent)
    monkeypatch.setattr("tools.delegate_tool._run_single_child", fake_run_single_child)
    receipt = dispatch_next_slice(SimpleNamespace(enabled_toolsets=None), task_id=task_id, store=store)
    assert receipt["success"] is True
    assert captured["forced_session_id"] == receipt["worker_session_id"]
