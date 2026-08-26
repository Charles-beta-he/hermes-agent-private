from __future__ import annotations

import json
import subprocess
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


def _make_linked_worktree(tmp_path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=main, check=True)
    subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=main, check=True)
    (main / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=main, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=main, check=True)
    subprocess.run(["git", "worktree", "add", "-q", str(linked), "-b", "worker-test"], cwd=main, check=True)
    return main, linked


def _git_head(path):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_workspace_validation_requires_real_linked_worktree_and_confines_owned_paths(tmp_path):
    with pytest.raises(ValueError, match="isolated_workspace_required"):
        worker_module._validated_workspace({"owned_paths": ["agent/runtime.py"]})

    main, linked = _make_linked_worktree(tmp_path)
    valid_packet = {
        "workspace_path": str(linked),
        "workspace_base_commit": _git_head(linked),
        "owned_paths": ["agent/runtime.py"],
    }

    assert worker_module._validated_workspace(valid_packet) == str(linked.resolve())
    missing_base = dict(valid_packet, workspace_base_commit="")
    with pytest.raises(ValueError, match="workspace_base_commit_required"):
        worker_module._validated_workspace(missing_base)
    wrong_base = dict(valid_packet, workspace_base_commit="0" * 40)
    with pytest.raises(ValueError, match="workspace_base_commit_mismatch"):
        worker_module._validated_workspace(wrong_base)
    dirty = linked / "dirty.txt"
    dirty.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="isolated_workspace_not_clean"):
        worker_module._validated_workspace(valid_packet)
    dirty.unlink()
    with pytest.raises(ValueError, match="owned_path_outside_workspace"):
        worker_module._validated_workspace(dict(valid_packet, owned_paths=["../escape.txt"]))
    with pytest.raises(ValueError, match="linked_git_worktree_required"):
        worker_module._validated_workspace(
            {
                "workspace_path": str(main),
                "workspace_base_commit": _git_head(main),
                "owned_paths": ["seed.txt"],
            }
        )


def _worker_result(packet, *, changed_artifacts):
    return {
        "slice_id": packet["slice_id"],
        "input_digest": packet["packet_digest"],
        "claims": ["R1", "N1"],
        "changed_artifacts": changed_artifacts,
        "evidence_refs": [{"kind": "test", "ref": "pytest:passed"}],
        "verification_results": [{"command": "pytest -q", "exit_code": 0}],
        "risks": [],
        "unknowns": [],
        "state_delta_proposal": {"slice_status": "completed"},
        "candidate_digest": "sha256:candidate",
    }


def _ready(tmp_path, *, workspace_path=""):
    workspace_base_commit = _git_head(workspace_path) if workspace_path else ""
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
            "workspace_path": str(workspace_path),
            "workspace_base_commit": workspace_base_commit,
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
    assert store.get_task(task_id)["slices"][0]["status"] == "quarantined"


def test_worker_runner_exception_quarantines_claim_without_termination_proof(tmp_path):
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
    assert receipt["claim_receipt"]["status"] == "quarantined"
    assert store.get_task(task_id)["slices"][0]["status"] == "quarantined"


@pytest.mark.parametrize(
    ("execution_status", "expected_error", "worker_terminated", "expected_slice_status"),
    [
        ("timeout", "worker_timeout", False, "quarantined"),
        ("timeout", "worker_timeout", True, "pending"),
        ("cancelled", "worker_cancelled", None, "quarantined"),
        ("cancelled", "worker_cancelled", True, "pending"),
        ("error", "worker_execution_failed", None, "quarantined"),
    ],
)
def test_worker_terminal_execution_status_fences_claim_safely(
    tmp_path,
    execution_status,
    expected_error,
    worker_terminated,
    expected_slice_status,
):
    store, task_id = _ready(tmp_path)

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(*, child, goal, parent_agent):
        execution = {
            "status": execution_status,
            "summary": None,
            "error": f"simulated {execution_status}",
            "exit_reason": execution_status,
        }
        if worker_terminated is not None:
            execution["worker_terminated"] = worker_terminated
        return execution

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
    assert receipt["claim_receipt"]["status"] == (
        "quarantined" if expected_slice_status == "quarantined" else "abandoned"
    )
    task = store.get_task(task_id)
    assert task["slices"][0]["status"] == expected_slice_status
    if expected_slice_status == "quarantined":
        assert task["state_events"][-1]["event_type"] == "slice_claim_quarantined"
        assert store.claim_next_slice(
            task_id, worker_session_id="worker-retry", lease_seconds=60
        ) is None


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


def test_default_executor_fails_closed_without_isolated_workspace(tmp_path):
    store, task_id = _ready(tmp_path)

    receipt = dispatch_next_slice(SimpleNamespace(enabled_toolsets=None), task_id=task_id, store=store)

    assert receipt["success"] is False
    assert receipt["error"] == "isolated_workspace_required"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"


def test_default_executor_forces_child_session_to_lease_owner(tmp_path, monkeypatch):
    _, linked = _make_linked_worktree(tmp_path)
    store, task_id = _ready(tmp_path, workspace_path=linked)
    captured = {}

    def fake_build_child_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(session_id=kwargs["forced_session_id"])

    def fake_run_single_child(index, goal, child, parent):
        target = linked / "src" / "x.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed = True\n", encoding="utf-8")
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
    assert captured["forced_workspace_path"] == str(linked.resolve())
    assert receipt["worker_execution"]["workspace_readback"] == {
        "workspace_path": str(linked),
        "expected_base_commit": _git_head(linked),
        "actual_base_commit": _git_head(linked),
        "actual_changed_paths": ["src/x.py"],
        "declared_changed_paths": ["src/x.py"],
    }


def test_default_executor_rejects_worker_commit_that_moves_fenced_base(tmp_path, monkeypatch):
    _, linked = _make_linked_worktree(tmp_path)
    store, task_id = _ready(tmp_path, workspace_path=linked)
    captured = {}

    def fake_build_child_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(session_id=kwargs["forced_session_id"])

    def fake_run_single_child(index, goal, child, parent):
        target = linked / "src" / "x.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/x.py"], cwd=linked, check=True)
        subprocess.run(["git", "commit", "-qm", "worker commit"], cwd=linked, check=True)
        packet = json.loads(captured["context"].split("CONTEXT_PACKET_JSON=", 1)[1])
        return {
            "status": "completed",
            "summary": json.dumps(_worker_result(packet, changed_artifacts=[])),
        }

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", fake_build_child_agent)
    monkeypatch.setattr("tools.delegate_tool._run_single_child", fake_run_single_child)
    receipt = dispatch_next_slice(SimpleNamespace(enabled_toolsets=None), task_id=task_id, store=store)

    assert receipt["success"] is False
    assert receipt["error"] == "worker_workspace_base_changed"
    readback = receipt["worker_execution"]["workspace_readback"]
    assert readback["actual_base_commit"] != readback["expected_base_commit"]
    assert store.get_task(task_id)["slices"][0]["status"] == "quarantined"


def test_default_executor_rejects_declared_write_set_mismatch(tmp_path, monkeypatch):
    _, linked = _make_linked_worktree(tmp_path)
    store, task_id = _ready(tmp_path, workspace_path=linked)
    captured = {}

    def fake_build_child_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(session_id=kwargs["forced_session_id"])

    def fake_run_single_child(index, goal, child, parent):
        target = linked / "src" / "x.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed = True\n", encoding="utf-8")
        packet = json.loads(captured["context"].split("CONTEXT_PACKET_JSON=", 1)[1])
        result = {
            "slice_id": packet["slice_id"],
            "input_digest": packet["packet_digest"],
            "claims": ["R1", "N1"],
            "changed_artifacts": [],
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

    assert receipt["success"] is False
    assert receipt["error"] == "worker_write_set_mismatch"
    assert receipt["worker_execution"]["workspace_readback"]["actual_changed_paths"] == ["src/x.py"]
    assert store.get_task(task_id)["slices"][0]["status"] == "quarantined"
