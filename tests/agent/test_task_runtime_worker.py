from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import agent.task_runtime_worker as worker_module
from agent.task_runtime import TaskRuntimeStore, _integrate_workspace_candidate_ref
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


def _ready(
    tmp_path,
    *,
    workspace_path="",
    workspace_source_repo="",
    worker_command=None,
    worker_timeout_seconds=900,
):
    workspace_base_commit = (
        _git_head(workspace_path or workspace_source_repo)
        if (workspace_path or workspace_source_repo)
        else ""
    )
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
            "workspace_source_repo": str(workspace_source_repo),
            "workspace_base_commit": workspace_base_commit,
            "worker_command": list(worker_command or []),
            "worker_timeout_seconds": worker_timeout_seconds,
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
        ("timeout", "worker_timeout", True, "quarantined"),
        ("cancelled", "worker_cancelled", None, "quarantined"),
        ("cancelled", "worker_cancelled", True, "quarantined"),
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


def test_worker_heartbeat_extends_claim_during_long_runner(tmp_path, monkeypatch):
    store, task_id = _ready(tmp_path)
    heartbeats = []
    original = store.heartbeat_claim

    def observed_heartbeat(*args, **kwargs):
        receipt = original(*args, **kwargs)
        heartbeats.append(receipt)
        return receipt

    monkeypatch.setattr(store, "heartbeat_claim", observed_heartbeat)

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(*, child, goal, parent_agent):
        time.sleep(0.45)
        packet = json.loads(goal.split("CONTEXT_PACKET_JSON=", 1)[1])
        return {
            "status": "completed",
            "summary": json.dumps(_worker_result(packet, changed_artifacts=[])),
        }

    receipt = dispatch_next_slice(
        SimpleNamespace(enabled_toolsets=None),
        task_id=task_id,
        lease_seconds=1,
        store=store,
        child_builder=builder,
        child_runner=runner,
    )
    assert receipt["success"] is True
    assert len(heartbeats) >= 1
    assert all(item["packet_id"] == receipt["context_packet"]["packet_id"] for item in heartbeats)


def test_pool_slot_is_released_after_prestart_builder_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "_WORKER_SLOTS", threading.BoundedSemaphore(1))
    failed_store, failed_task = _ready(tmp_path / "failed")

    def broken_builder(**kwargs):
        raise RuntimeError("build failed")

    failed = dispatch_next_slice(
        SimpleNamespace(enabled_toolsets=None),
        task_id=failed_task,
        store=failed_store,
        child_builder=broken_builder,
        child_runner=lambda **kwargs: {},
    )
    assert failed["success"] is False
    assert failed_store.get_task(failed_task)["slices"][0]["status"] == "pending"

    good_store, good_task = _ready(tmp_path / "good")

    def builder(*, worker_session_id, packet, parent_agent):
        return SimpleNamespace(session_id=worker_session_id)

    def runner(*, child, goal, parent_agent):
        packet = json.loads(goal.split("CONTEXT_PACKET_JSON=", 1)[1])
        return {
            "status": "completed",
            "summary": json.dumps(_worker_result(packet, changed_artifacts=[])),
        }

    receipts = []
    thread = threading.Thread(
        target=lambda: receipts.append(
            dispatch_next_slice(
                SimpleNamespace(enabled_toolsets=None),
                task_id=good_task,
                store=good_store,
                child_builder=builder,
                child_runner=runner,
            )
        )
    )
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert receipts[0]["success"] is True


def _write_process_worker(tmp_path, body: str) -> Path:
    script = tmp_path / f"worker-{len(list(tmp_path.glob('worker-*.py')))}.py"
    script.write_text(body, encoding="utf-8")
    return script


def test_exact_subprocess_worker_executes_without_callable_child(tmp_path):
    _main, linked = _make_linked_worktree(tmp_path)
    script = _write_process_worker(
        tmp_path,
        """import json, sys
prompt = sys.stdin.read()
packet = json.loads(prompt.split('CONTEXT_PACKET_JSON=', 1)[1])
print(json.dumps({
  'slice_id': packet['slice_id'],
  'input_digest': packet['packet_digest'],
  'claims': ['R1', 'N1'],
  'changed_artifacts': [],
  'evidence_refs': [{'kind': 'test', 'ref': 'subprocess:passed'}],
  'verification_results': [{'command': 'pytest -q', 'exit_code': 0}],
  'risks': [], 'unknowns': [],
  'state_delta_proposal': {'slice_status': 'completed'},
  'candidate_digest': 'sha256:subprocess'
}))
""",
    )
    store, task_id = _ready(
        tmp_path / "state",
        workspace_path=linked,
        worker_command=[sys.executable, str(script)],
    )

    def forbidden_builder(**_kwargs):
        raise AssertionError("callable child builder must not run")

    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent"),
        task_id=task_id,
        store=store,
        child_builder=None,
        child_runner=forbidden_builder,
        lease_seconds=2,
    )
    assert receipt["success"] is True
    process_receipt = receipt["worker_execution"]["process_receipt"]
    assert process_receipt["leader_reaped"] is True
    assert process_receipt["residual_probe_succeeded"] is True
    assert process_receipt["residual_pids"] == []
    assert store.get_task(task_id)["slices"][0]["status"] == "submitted"


def test_subprocess_boundary_freezes_managed_workspace_candidate(tmp_path, monkeypatch):
    source, _linked = _make_linked_worktree(tmp_path)
    script = _write_process_worker(
        tmp_path,
        """import json, pathlib, sys
payload = sys.stdin.read()
packet = json.loads(payload.split('CONTEXT_PACKET_JSON=', 1)[1])
path = pathlib.Path('src/x.py')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text('value = 1\\n')
print(json.dumps({
    'slice_id': packet['slice_id'],
    'input_digest': packet['packet_digest'],
    'claims': ['R1', 'N1'],
    'changed_artifacts': ['src/x.py'],
    'evidence_refs': [{'kind': 'test', 'ref': 'local'}],
    'verification_results': [{'command': 'pytest -q', 'exit_code': 0}],
    'risks': [], 'unknowns': [],
    'state_delta_proposal': {'slice_status': 'completed'},
    'candidate_digest': 'worker-supplied-untrusted',
}))
""",
    )
    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    store, task_id = _ready(
        tmp_path / "state",
        workspace_source_repo=source,
        worker_command=[sys.executable, str(script)],
    )
    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent"), task_id=task_id, store=store, lease_seconds=2
    )
    assert receipt["success"] is True, receipt.get("worker_result", receipt)
    readback = receipt["worker_execution"]["workspace_readback"]
    assert readback["actual_changed_paths"] == ["src/x.py"]
    assert readback["candidate_digest"].startswith("sha256:")
    assert receipt["accepted_result"]["candidate_digest"] == readback["candidate_digest"]
    candidate_receipt = receipt["workspace_candidate_receipt"]
    assert candidate_receipt["candidate_digest"] == readback["candidate_digest"]
    assert candidate_receipt["workspace_reap_receipt"]["removed"] is True
    assert Path(candidate_receipt["archive_path"]).is_file()
    assert not Path(receipt["workspace_lease"]["workspace_path"]).exists()

    result_id = receipt["worker_result"]["result_id"]
    with pytest.raises(ValueError, match="workspace_candidate_receipt_required"):
        store.integrate_result(
            task_id, result_id, integrator_session_id="independent-integrator"
        )
    wrong_receipt = dict(candidate_receipt)
    wrong_receipt["packet_id"] = "wrong-packet"
    with pytest.raises(ValueError, match="workspace_candidate_receipt_binding_mismatch"):
        store.integrate_result(
            task_id,
            result_id,
            integrator_session_id="independent-integrator",
            workspace_candidate_receipt=wrong_receipt,
        )
    archive = Path(candidate_receipt["archive_path"])
    archive_bytes = archive.read_bytes()
    archive.write_bytes(archive_bytes + b"tampered")
    with pytest.raises(ValueError, match="workspace_candidate_archive_digest_mismatch"):
        store.integrate_result(
            task_id,
            result_id,
            integrator_session_id="independent-integrator",
            workspace_candidate_receipt=candidate_receipt,
        )
    archive.write_bytes(archive_bytes)
    symlink_receipt = dict(candidate_receipt)
    symlink_archive = Path(candidate_receipt["archive_path"]).with_name("candidate-link.tar")
    symlink_archive.symlink_to(Path(candidate_receipt["archive_path"]))
    symlink_receipt["archive_path"] = str(symlink_archive)
    with pytest.raises(ValueError, match="workspace_candidate_archive_invalid"):
        _integrate_workspace_candidate_ref(
            symlink_receipt,
            integrator_session_id="integrator-symlink",
        )

    integrated = store.integrate_result(
        task_id,
        result_id,
        integrator_session_id="independent-integrator",
        workspace_candidate_receipt=candidate_receipt,
    )
    assert integrated["slice_status"] == "completed"
    integration_receipt = integrated["workspace_integration_receipt"]
    assert integration_receipt["schema"] == "hermes_workspace_candidate_integration_v1"
    assert integration_receipt["candidate_digest"] == readback["candidate_digest"]
    assert subprocess.run(
        ["git", "-C", str(source), "rev-parse", integration_receipt["candidate_ref"]],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == integration_receipt["candidate_commit"]
    assert subprocess.run(
        [
            "git", "-C", str(source), "show",
            f"{integration_receipt['candidate_commit']}:src/x.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout == "value = 1\n"
    assert subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == candidate_receipt["base_commit"]
    replay_integration = _integrate_workspace_candidate_ref(
        candidate_receipt,
        integrator_session_id="integrator-2",
    )
    assert replay_integration["candidate_commit"] == integration_receipt["candidate_commit"]
    assert replay_integration["idempotent_replay"] is True
    subprocess.run(
        [
            "git", "-C", str(source), "update-ref",
            integration_receipt["candidate_ref"], candidate_receipt["base_commit"],
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="workspace_candidate_ref_conflict"):
        _integrate_workspace_candidate_ref(
            candidate_receipt,
            integrator_session_id="integrator-conflict",
        )
    wrong_base = dict(candidate_receipt)
    wrong_base["base_commit"] = "0" * 40
    with pytest.raises(ValueError, match="workspace_candidate_git_integration_failed"):
        _integrate_workspace_candidate_ref(
            wrong_base,
            integrator_session_id="integrator-wrong-base",
        )
    assert subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == candidate_receipt["base_commit"]
    subprocess.run(
        [
            "git", "-C", str(source),
            "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "--allow-empty", "-m", "advance source head",
        ],
        check=True,
        capture_output=True,
    )
    advanced_head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(ValueError, match="workspace_candidate_source_head_mismatch"):
        _integrate_workspace_candidate_ref(
            candidate_receipt,
            integrator_session_id="integrator-stale-head",
        )
    assert subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == advanced_head


def test_candidate_finalize_failure_quarantines_accepted_result_and_preserves_workspace(
    tmp_path, monkeypatch
):
    source, _linked = _make_linked_worktree(tmp_path)
    script = _write_process_worker(
        tmp_path,
        """import json, pathlib, sys
packet = json.loads(sys.stdin.read().split('CONTEXT_PACKET_JSON=', 1)[1])
path = pathlib.Path('src/x.py')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text('value = 1\\n')
print(json.dumps({
    'slice_id': packet['slice_id'],
    'input_digest': packet['packet_digest'],
    'claims': ['R1', 'N1'],
    'changed_artifacts': ['src/x.py'],
    'evidence_refs': [{'kind': 'test', 'ref': 'local'}],
    'verification_results': [{'command': 'pytest -q', 'exit_code': 0}],
    'risks': [], 'unknowns': [],
    'state_delta_proposal': {'slice_status': 'completed'},
    'candidate_digest': 'untrusted',
}))
""",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    store, task_id = _ready(
        tmp_path / "state",
        workspace_source_repo=source,
        worker_command=[sys.executable, str(script)],
    )

    def fail_finalize(*_args, **_kwargs):
        raise OSError("simulated candidate storage failure")

    monkeypatch.setattr(worker_module, "freeze_workspace_candidate", fail_finalize)
    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent"), task_id=task_id, store=store, lease_seconds=2
    )

    assert receipt["success"] is False
    assert receipt["error"] == "workspace_candidate_finalize_failed"
    assert receipt["candidate_finalize_receipt"]["status"] == "quarantined"
    assert receipt["process_receipt"]["residual_probe_succeeded"] is True
    workspace = Path(receipt["workspace_lease"]["workspace_path"])
    assert workspace.is_dir()
    task = store.get_task(task_id)
    assert task["slices"][0]["status"] == "quarantined"
    assert task["state_events"][-1]["event_type"] == "workspace_candidate_finalize_failed"
    result_id = receipt["worker_result"]["result_id"]
    with pytest.raises(ValueError, match="result_not_accepted_for_integration"):
        store.integrate_result(
            task_id,
            result_id,
            integrator_session_id="must-not-integrate",
            workspace_candidate_receipt={},
        )
    replay = store.quarantine_candidate_finalize_failure(
        task_id,
        result_id,
        worker_session_id=receipt["worker_session_id"],
        packet_id=receipt["context_packet"]["packet_id"],
        error="replay",
    )
    assert replay["idempotent_replay"] is True


@pytest.mark.parametrize(
    ("body", "timeout", "expected_error"),
    [
        ("print('not-json')\n", 2, "worker_result_not_json"),
        ("import sys; sys.exit(7)\n", 2, "worker_execution_failed"),
        ("import time; time.sleep(5)\n", 0.1, "worker_timeout"),
    ],
)
def test_controlled_subprocess_failure_reaps_workspace_then_recovers_claim(
    tmp_path, monkeypatch, body, timeout, expected_error
):
    source, _linked = _make_linked_worktree(tmp_path)
    script = _write_process_worker(tmp_path, body)
    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    store, task_id = _ready(
        tmp_path / "state",
        workspace_source_repo=source,
        worker_command=[sys.executable, str(script)],
        worker_timeout_seconds=timeout,
    )
    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent"),
        task_id=task_id,
        store=store,
        lease_seconds=2,
    )
    assert receipt["success"] is False
    assert receipt["error"] == expected_error
    assert receipt["claim_receipt"]["status"] == "recovered"
    cleanup = receipt["workspace_cleanup_receipt"]
    assert cleanup["removed"] is True
    assert cleanup["residual_probe_succeeded"] is True
    assert not Path(cleanup["workspace_path"]).exists()
    task = store.get_task(task_id)
    assert task["slices"][0]["status"] == "pending"
    assert task["state_events"][-1]["event_type"] == "slice_claim_recovered"
    assert (
        task["state_events"][-1]["payload"]["packet_id"]
        == receipt["context_packet"]["packet_id"]
    )


@pytest.mark.live_system_guard_bypass
def test_subprocess_boundary_reaps_observed_setsid_descendant(tmp_path, monkeypatch):
    source, _linked = _make_linked_worktree(tmp_path)
    child_pid_file = tmp_path / "child.pid"
    script = _write_process_worker(
        tmp_path,
        """import os, subprocess, sys, time
child = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(30)'],
    start_new_session=True,
)
open(sys.argv[1], 'w').write(str(child.pid))
time.sleep(0.2)
print('not-json')
""",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    store, task_id = _ready(
        tmp_path / "state",
        workspace_source_repo=source,
        worker_command=[sys.executable, str(script), str(child_pid_file)],
        worker_timeout_seconds=3,
    )
    receipt = dispatch_next_slice(
        SimpleNamespace(session_id="parent"),
        task_id=task_id,
        store=store,
        lease_seconds=2,
    )
    assert receipt["error"] == "worker_result_not_json", receipt
    process_receipt = receipt["worker_execution"]["process_receipt"]
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert child_pid in {
        item["pid"] for item in process_receipt["observed_descendants"]
    }
    assert process_receipt["residual_probe_succeeded"] is True
    assert process_receipt["residual_pids"] == []
    assert receipt["claim_receipt"]["status"] == "recovered"


def test_worker_tool_is_exposed_but_registry_cannot_bypass_live_agent():
    assert "task_runtime_worker" in resolve_toolset("hermes-cli")
    payload = registry.dispatch("task_runtime_worker", {"task_id": "t1"})
    assert payload == {"success": False, "error": "task_runtime_worker_requires_live_agent"}


def test_default_executor_fails_closed_without_isolated_workspace(tmp_path):
    store, task_id = _ready(tmp_path)

    receipt = dispatch_next_slice(SimpleNamespace(enabled_toolsets=None), task_id=task_id, store=store)

    assert receipt["success"] is False
    assert receipt["error"] == "workspace_source_repo_required"
    assert store.get_task(task_id)["slices"][0]["status"] == "pending"


def test_default_executor_allocates_owned_workspace_from_source_repo(tmp_path, monkeypatch):
    main, _ = _make_linked_worktree(tmp_path)
    store, task_id = _ready(tmp_path, workspace_source_repo=main)
    captured = {}
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))

    def fake_build_child_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(session_id=kwargs["forced_session_id"])

    def fake_run_single_child(index, goal, child, parent):
        workspace = Path(captured["forced_workspace_path"])
        target = workspace / "src" / "x.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed = True\n", encoding="utf-8")
        packet = json.loads(captured["context"].split("CONTEXT_PACKET_JSON=", 1)[1])
        assert "_execution_workspace_path" not in packet
        return {
            "status": "completed",
            "summary": json.dumps(_worker_result(packet, changed_artifacts=["src/x.py"])),
        }

    monkeypatch.setattr("tools.delegate_tool._build_child_agent", fake_build_child_agent)
    monkeypatch.setattr("tools.delegate_tool._run_single_child", fake_run_single_child)
    receipt = dispatch_next_slice(
        SimpleNamespace(enabled_toolsets=None), task_id=task_id, store=store
    )

    assert receipt["success"] is True
    assert receipt["workspace_lease"]["packet_id"] == receipt["context_packet"]["packet_id"]
    assert Path(receipt["workspace_lease"]["workspace_path"]).is_dir()
    assert captured["forced_workspace_path"] == receipt["workspace_lease"]["workspace_path"]


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
