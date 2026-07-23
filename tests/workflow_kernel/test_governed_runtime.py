from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import importlib.resources
import json
import multiprocessing
import os
from pathlib import Path
import select
from threading import Barrier, Event, Lock
from typing import Any, Mapping, NoReturn

import pytest

from workflow_kernel import governed_runtime
from workflow_kernel import workflow_execution_ledger as execution_ledger
from workflow_kernel.governed_runtime import (
    ExecuteGrant,
    WorkflowRuntimeError,
    compile_runtime_preview,
    read_runtime_status,
    run_workflow,
)
from workflow_kernel.workflow_execution_ledger import (
    WorkflowExecutionLedgerError,
    append_execution_event,
    derive_workflow_execution_event_id_v1,
    project_execution_events,
    read_execution_events,
)


NOW = "2026-07-23T05:00:00Z"
LATER = "2026-07-23T05:10:00Z"


def _multiprocess_run_worker(
    ledger_path: str,
    private_root: str,
    start: Any,
    results: Any,
) -> None:
    start.wait()
    try:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=Path(ledger_path),
            private_root=Path(private_root),
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
        )
    except WorkflowRuntimeError as exc:
        results.put(exc.code)
    else:
        results.put("completed")


def _fifo_ledger_public_api_worker(
    operation: str,
    ledger_path: str,
    private_root: str,
    result_pipe: Any,
) -> None:
    try:
        if operation == "status":
            read_runtime_status(Path(ledger_path), private_root=Path(private_root))
        else:
            run_workflow(
                _definition(),
                _manifest(),
                ledger_path=Path(ledger_path),
                private_root=Path(private_root),
                execute_grant=ExecuteGrant.local_only(),
                now=NOW,
            )
    except BaseException as exc:
        result_pipe.send((type(exc).__name__, getattr(exc, "code", None)))
    else:
        result_pipe.send(("returned", None))
    finally:
        result_pipe.close()


@pytest.mark.parametrize(
    "schema_name",
    [
        "workflow-runtime-manifest-v1.schema.json",
        "workflow-leaf-packet-v1.schema.json",
        "workflow-execution-event-v1.schema.json",
    ],
)
def test_release_and_package_schema_copies_are_identical(schema_name: str) -> None:
    release_schema = Path(__file__).parents[2] / "schemas" / schema_name
    package_schema = importlib.resources.files("workflow_kernel.schemas").joinpath(
        schema_name
    )

    assert release_schema.read_bytes() == package_schema.read_bytes()


def _definition(*, null_output: bool = False) -> dict:
    tasks = [
        {
            "id": "inspect",
            "title": "Inspect input",
            "role": "explore",
            "owner": "explorer",
            "depends_on": [],
            "inputs": ["runtime input"],
            "outputs": ["inventory"],
            "writes": [],
            "authority": {"side_effect": "none", "capabilities": []},
            "acceptance": [
                {
                    "id": "inspect-grounded",
                    "claim": "The input is represented structurally.",
                    "evidence_required": ["readback"],
                }
            ],
            "verifies": [],
        },
        {
            "id": "implement",
            "title": "Produce local result",
            "role": "execute",
            "owner": "executor",
            "depends_on": ["inspect"],
            "inputs": ["inventory"],
            "outputs": ["result"],
            "writes": [],
            "authority": {"side_effect": "none", "capabilities": []},
            "acceptance": [
                {
                    "id": "result-structured",
                    "claim": "The result matches its schema.",
                    "evidence_required": ["readback"],
                }
            ],
            "verifies": [],
        },
        {
            "id": "verify",
            "title": "Verify local result",
            "role": "verify",
            "owner": "verifier",
            "depends_on": ["implement"],
            "inputs": ["result"],
            "outputs": ["verdict"],
            "writes": [],
            "authority": {"side_effect": "none", "capabilities": []},
            "acceptance": [
                {
                    "id": "verdict-structured",
                    "claim": "The result has an independent verifier receipt.",
                    "evidence_required": ["readback"],
                }
            ],
            "verifies": ["implement"],
        },
    ]
    return {
        "schema_version": "workflow_definition_v1",
        "workflow_id": "governed-runtime-test",
        "version": 1,
        "title": "Governed runtime test",
        "goal": "Execute a bounded local-only workflow.",
        "non_goals": ["No external side effects."],
        "constraints": ["Fresh execute grant required."],
        "authority": {"max_side_effect": "none", "allowed_capabilities": []},
        "tasks": tasks,
        "loop_policy": {
            "max_iterations": 2,
            "repeated_failure_limit": 1,
            "on_repeated_failure": "terminal_failure",
        },
        "stop_conditions": ["success", "blocked", "terminal_failure", "cancelled"],
        "handoff": {
            "required_fields": [
                "goal",
                "task_states",
                "evidence",
                "artifacts",
                "authority_state",
                "blockers",
                "next_step",
            ]
        },
    }


def _reservation(*, wall_ms: int = 10_000) -> dict[str, int]:
    return {
        "spawns": 1,
        "wall_ms": wall_ms,
        "tool_calls": 0,
        "tokens": 0,
        "side_effect_ops": 0,
    }


def _manifest(*, null_output: bool = False) -> dict:
    implement_value = None if null_output else {"ok": True}
    return {
        "schema_version": "workflow_runtime_manifest_v1",
        "run_id": "run-governed-runtime-test",
        "identity_schema_version": 1,
        "baseline_ref": "git:57cb785a9",
        "project_scope": "/tmp/project",
        "profile_scope": "default",
        "tool_policy_ref": "policy:local-pure-v1",
        "model_contract_ref": "model:none",
        "authority_ref": "auth:user-local-only-20260723",
        "max_concurrency": 2,
        "reservation_ttl_ms": 60_000,
        "hard_budget": {
            "spawns": 3,
            "wall_ms": 30_000,
            "tool_calls": 0,
            "tokens": 0,
            "side_effect_ops": 0,
        },
        "leaves": {
            "inspect": {
                "adapter": "local.echo",
                "isolation": "in_process_trusted_pure",
                "input": {"source": "fixture"},
                "output_schema": {
                    "type": "object",
                    "required": ["source"],
                    "additionalProperties": False,
                    "properties": {"source": {"type": "string"}},
                },
                "reserve": _reservation(),
            },
            "implement": {
                "adapter": "local.echo",
                "isolation": "in_process_trusted_pure",
                "input": implement_value,
                "output_schema": {}
                if null_output
                else {
                    "type": "object",
                    "required": ["ok"],
                    "additionalProperties": False,
                    "properties": {"ok": {"const": True}},
                },
                "reserve": _reservation(),
            },
            "verify": {
                "adapter": "local.verify_dependency",
                "isolation": "in_process_trusted_pure",
                "input": {"dependency": "implement"},
                "output_schema": {
                    "type": "object",
                    "required": ["verified", "dependency"],
                    "additionalProperties": False,
                    "properties": {
                        "verified": {"const": True},
                        "dependency": {"const": "implement"},
                    },
                },
                "reserve": _reservation(),
            },
        },
    }


def _run(
    tmp_path: Path,
    *,
    definition: dict | None = None,
    manifest: dict | None = None,
    resume: bool = False,
    now: str = NOW,
) -> dict:
    return run_workflow(
        definition or _definition(),
        manifest or _manifest(),
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        resume=resume,
        now=now,
    )


def test_relative_runtime_paths_are_pinned_before_cwd_can_change(
    monkeypatch, tmp_path: Path
) -> None:
    original_directory = tmp_path / "original"
    drift_directory = tmp_path / "drift"
    original_directory.mkdir()
    drift_directory.mkdir()
    monkeypatch.chdir(original_directory)
    real_exists = governed_runtime.private_file_exists_read_only
    cwd_changed = False

    def change_cwd_after_first_read(path: Path, *, private_root: Path) -> bool:
        nonlocal cwd_changed
        result = real_exists(path, private_root=private_root)
        if not cwd_changed:
            cwd_changed = True
            os.chdir(drift_directory)
        return result

    monkeypatch.setattr(
        governed_runtime,
        "private_file_exists_read_only",
        change_cwd_after_first_read,
    )

    result = run_workflow(
        _definition(),
        _manifest(),
        ledger_path=Path("state/run.jsonl"),
        private_root=Path("state"),
        execute_grant=ExecuteGrant.local_only(),
        now=NOW,
    )

    assert result["status"] == "run_completed"
    assert (original_directory / "state/run.jsonl").is_file()
    assert not (drift_directory / "state/run.jsonl").exists()


def test_output_schema_format_is_enforced(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["input"] = "definitely-not-rfc3339"
    manifest["leaves"]["inspect"]["output_schema"] = {
        "type": "string",
        "format": "date-time",
    }

    result = _run(tmp_path, manifest=manifest)

    inspect_packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect"
    )
    assert inspect_packet["status"] == "unverified"
    assert inspect_packet["uncertainty"][0]["code"] == "output_schema_invalid"
    assert result["verification_state"] == "unverified"


def test_valid_local_output_schema_ref_and_datetime_format_execute(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["input"] = "2026-07-23T05:00:00Z"
    manifest["leaves"]["inspect"]["output_schema"] = {
        "$defs": {"timestamp": {"type": "string", "format": "date-time"}},
        "$ref": "#/$defs/timestamp",
    }

    compile_runtime_preview(_definition(), manifest)
    result = _run(tmp_path, manifest=manifest)

    inspect_packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect"
    )
    assert inspect_packet["status"] == "completed"


def test_unsupported_output_schema_format_fails_before_runtime_state(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = {
        "type": "string",
        "format": "custom-unknown",
    }

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)

    assert excinfo.value.code == "output_schema_invalid"
    assert not (tmp_path / "run.jsonl").exists()


@pytest.mark.parametrize(
    "schema",
    [
        {"$defs": {"a/b": {"type": "string"}}, "$ref": "#/$defs/a~1b"},
        {"$defs": {"value": {"type": "string"}}, "$ref": "#/%24defs/value"},
        {"allOf": [{"type": "string"}], "$ref": "#/allOf/0"},
    ],
)
def test_valid_local_output_schema_pointer_forms_execute(
    tmp_path: Path, schema: dict[str, object]
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["input"] = "value"
    manifest["leaves"]["inspect"]["output_schema"] = schema

    result = _run(tmp_path, manifest=manifest)

    inspect_packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect"
    )
    assert inspect_packet["status"] == "completed"


@pytest.mark.parametrize(
    "schema",
    [
        {"$defs": {"a~2b": {"type": "string"}}, "$ref": "#/$defs/a~2b"},
        {"allOf": [{"type": "string"}, {"type": "string"}], "$ref": "#/allOf/01"},
    ],
)
def test_malformed_local_output_schema_pointer_fails_closed(
    tmp_path: Path, schema: dict[str, object]
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = schema

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)

    assert excinfo.value.code == "output_schema_invalid"
    assert not (tmp_path / "run.jsonl").exists()


def test_schema_annotations_and_instance_values_are_not_treated_as_schema_keywords(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = {
        "const": {
            "format": "opaque-application-data",
            "$ref": "https://example.invalid/not-a-schema-reference",
        }
    }

    preview = compile_runtime_preview(_definition(), manifest)

    assert preview["run_id"] == manifest["run_id"]
    assert not (tmp_path / "run.jsonl").exists()


def test_nested_schema_resource_resolves_its_own_local_reference(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = {
        "$defs": {
            "nested": {
                "$id": "nested-resource",
                "$defs": {"payload": {"type": "object"}},
                "$ref": "#/$defs/payload",
            }
        },
        "$ref": "#/$defs/nested",
    }

    preview = compile_runtime_preview(_definition(), manifest)

    assert preview["run_id"] == manifest["run_id"]
    assert not (tmp_path / "run.jsonl").exists()


def test_unresolved_nested_schema_resource_ref_fails_before_runtime_state(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = {
        "$defs": {
            "payload": {"type": "object"},
            "nested": {
                "$id": "nested-resource",
                "$ref": "#/$defs/payload",
            },
        },
        "$ref": "#/$defs/nested",
    }

    with pytest.raises(WorkflowRuntimeError) as exc:
        _run(tmp_path, manifest=manifest)

    assert exc.value.code == "output_schema_invalid"
    assert not (tmp_path / "run.jsonl").exists()
    assert not (tmp_path / ".run.jsonl.execution.lock").exists()


def test_dangling_local_output_schema_ref_fails_before_runtime_state(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = {"$ref": "#/$defs/missing"}

    with pytest.raises(WorkflowRuntimeError) as preview_exc:
        compile_runtime_preview(_definition(), manifest)
    assert preview_exc.value.code == "output_schema_invalid"

    private_root = tmp_path / "private"
    with pytest.raises(WorkflowRuntimeError) as run_exc:
        run_workflow(
            _definition(),
            manifest,
            ledger_path=private_root / "run.jsonl",
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
        )
    assert run_exc.value.code == "output_schema_invalid"
    assert not private_root.exists()


def test_verifier_input_is_constructed_from_declared_target_and_stable_on_resume(
    tmp_path: Path,
) -> None:
    canonical_manifest = _manifest()
    spoofed_manifest = _manifest()
    spoofed_manifest["leaves"]["verify"]["input"] = {
        "dependency": "inspect",
        "forged": True,
    }

    canonical_preview = compile_runtime_preview(_definition(), canonical_manifest)
    spoofed_preview = compile_runtime_preview(_definition(), spoofed_manifest)
    assert spoofed_preview["identity_hash"] == canonical_preview["identity_hash"]
    assert spoofed_preview["manifest_digest"] == canonical_preview["manifest_digest"]

    result = _run(tmp_path, manifest=spoofed_manifest)
    verify_packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "verify"
    )
    assert result["verification_state"] == "verified"
    assert verify_packet["result"] == {"verified": True, "dependency": "implement"}

    resumed = run_workflow(
        _definition(),
        spoofed_manifest,
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        resume=True,
        now=LATER,
    )
    assert resumed["verification_state"] == "verified"


def test_generic_adapter_cannot_satisfy_verifier_role(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["verify"]["adapter"] = "local.echo"
    manifest["leaves"]["verify"]["input"] = {"not_a_verdict": True}
    manifest["leaves"]["verify"]["output_schema"] = {}

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)

    assert excinfo.value.code == "verifier_contract_invalid"
    assert not (tmp_path / "run.jsonl").exists()


def test_preview_is_read_only_and_runtime_requires_fresh_execute_grant(
    tmp_path: Path,
) -> None:
    preview = compile_runtime_preview(_definition(), _manifest())
    assert preview["mode"] == "compile_preview_only"
    assert preview["execution_enabled"] is False
    assert preview["task_count"] == 3

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=tmp_path / "run.jsonl",
            private_root=tmp_path,
            execute_grant=None,
            now=NOW,
        )
    assert excinfo.value.code == "execute_grant_required"
    assert not (tmp_path / "run.jsonl").exists()


def test_rejected_invocation_still_consumes_one_shot_grant(tmp_path: Path) -> None:
    grant = ExecuteGrant.local_only()
    invalid_manifest = _manifest()
    invalid_manifest["leaves"]["inspect"]["isolation"] = "container"

    with pytest.raises(WorkflowRuntimeError) as invalid_exc:
        run_workflow(
            _definition(),
            invalid_manifest,
            ledger_path=tmp_path / "invalid/run.jsonl",
            private_root=tmp_path / "invalid",
            execute_grant=grant,
            now=NOW,
        )
    assert invalid_exc.value.code == "isolation_unsupported"
    assert not (tmp_path / "invalid").exists()

    with pytest.raises(WorkflowRuntimeError) as reuse_exc:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=tmp_path / "second/run.jsonl",
            private_root=tmp_path / "second",
            execute_grant=grant,
            now=NOW,
        )
    assert reuse_exc.value.code == "execute_grant_reused"
    assert not (tmp_path / "second").exists()


def test_execute_grant_is_one_shot_across_distinct_runs(tmp_path: Path) -> None:
    grant = ExecuteGrant.local_only()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir(mode=0o700)
    second_root.mkdir(mode=0o700)

    run_workflow(
        _definition(),
        _manifest(),
        ledger_path=first_root / "run.jsonl",
        private_root=first_root,
        execute_grant=grant,
        now=NOW,
    )

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            {**_manifest(), "run_id": "run-governed-runtime-second"},
            ledger_path=second_root / "run.jsonl",
            private_root=second_root,
            execute_grant=grant,
            now=NOW,
        )

    assert excinfo.value.code == "execute_grant_reused"
    assert not (second_root / "run.jsonl").exists()


def test_execute_grant_cannot_be_reconstructed_from_public_fields() -> None:
    with pytest.raises(TypeError):
        ExecuteGrant(mode="local_only", nonce="copied-nonce")  # type: ignore[call-arg]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork semantics")
def test_execute_grant_inherited_across_fork_is_rejected_in_child() -> None:
    grant = ExecuteGrant.local_only()
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        try:
            grant.consume()
        except WorkflowRuntimeError as exc:
            outcome = exc.code
        else:
            outcome = "consumed"
        os.write(writer, outcome.encode("utf-8"))
        os.close(writer)
        os._exit(0)

    os.close(writer)
    child_outcome = os.read(reader, 128).decode("utf-8")
    os.close(reader)
    _, status = os.waitpid(pid, 0)

    assert os.waitstatus_to_exitcode(status) == 0
    assert child_outcome == "execute_grant_process_mismatch"
    grant.consume()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork semantics")
def test_forked_grant_rejects_before_touching_inherited_locked_mutex() -> None:
    grant = ExecuteGrant.local_only()
    reader, writer = os.pipe()
    grant._ticket.lock.acquire()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - assertions run in parent
        os.close(reader)
        try:
            grant.consume()
        except WorkflowRuntimeError as exc:
            os.write(writer, exc.code.encode("utf-8"))
        finally:
            os._exit(0)

    os.close(writer)
    ready, _, _ = select.select([reader], [], [], 0.75)
    try:
        if ready:
            child_outcome = os.read(reader, 128).decode("utf-8")
        else:
            os.kill(pid, 9)
            child_outcome = "hung"
    finally:
        grant._ticket.lock.release()
        os.close(reader)
    _, status = os.waitpid(pid, 0)

    assert ready, "forked grant consume hung on inherited mutex"
    assert os.waitstatus_to_exitcode(status) == 0
    assert child_outcome == "execute_grant_process_mismatch"
    grant.consume()


def test_missing_status_is_strictly_read_only(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    before_mode = tmp_path.stat().st_mode & 0o777
    before_entries = sorted(path.name for path in tmp_path.iterdir())

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(tmp_path / "run.jsonl", private_root=tmp_path)

    assert excinfo.value.code == "run_missing"
    assert (tmp_path.stat().st_mode & 0o777) == before_mode
    assert sorted(path.name for path in tmp_path.iterdir()) == before_entries


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
@pytest.mark.parametrize("operation", ["status", "run"])
def test_fifo_ledger_is_rejected_without_blocking_public_api(
    tmp_path: Path, operation: str
) -> None:
    ledger = tmp_path / "run.jsonl"
    os.mkfifo(ledger, mode=0o600)
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_fifo_ledger_public_api_worker,
        args=(operation, str(ledger), str(tmp_path), sender),
    )
    process.start()
    sender.close()
    returned_within_bound = receiver.poll(0.75)
    result = receiver.recv() if returned_within_bound else None
    process.join(timeout=0.25)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    receiver.close()

    assert returned_within_bound, f"{operation} blocked while opening a FIFO ledger"
    assert result is not None and result[0] in {"OSError", "WorkflowRuntimeError"}
    assert ledger.is_fifo()


def test_existing_status_does_not_create_lock_or_chmod_paths(tmp_path: Path) -> None:
    expected = _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    lock = tmp_path / ".run.jsonl.lock"
    lock.unlink()
    tmp_path.chmod(0o755)
    ledger.chmod(0o644)

    actual = read_runtime_status(ledger, private_root=tmp_path)

    assert actual == expected
    assert not lock.exists()
    assert (tmp_path.stat().st_mode & 0o777) == 0o755
    assert (ledger.stat().st_mode & 0o777) == 0o644


def test_execute_grant_consumption_is_atomic_under_concurrency(tmp_path: Path) -> None:
    grant = ExecuteGrant.local_only()

    def invoke(index: int) -> str:
        root = tmp_path / str(index)
        root.mkdir(mode=0o700)
        try:
            run_workflow(
                _definition(),
                {**_manifest(), "run_id": f"run-governed-runtime-{index}"},
                ledger_path=root / "run.jsonl",
                private_root=root,
                execute_grant=grant,
                now=NOW,
            )
        except WorkflowRuntimeError as exc:
            return exc.code
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(invoke, range(2)))

    assert outcomes == ["completed", "execute_grant_reused"]


def test_local_dag_executes_with_structured_packets_and_status_projects_ledger(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path)

    assert result["status"] == "run_completed"
    assert result["outcome"] == "completed"
    assert result["verification_state"] == "verified"
    assert result["convergence_state"] == "not_converged"
    assert [packet["leaf_id"] for packet in result["leaf_packets"]] == [
        "inspect",
        "implement",
        "verify",
    ]
    assert all(packet["status"] == "completed" for packet in result["leaf_packets"])
    assert result["budget"]["settled"]["spawns"] == 3
    assert result["budget"]["reserved"]["spawns"] == 0

    status = read_runtime_status(tmp_path / "run.jsonl", private_root=tmp_path)
    assert status == result
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    assert events[-2]["event_type"] == "run_verified"
    assert events[-1]["event_type"] == "run_completed"


def test_run_completion_cannot_fabricate_verification_without_explicit_event(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    verified_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "run_verified"
    )
    without_verification = events[:verified_index] + [deepcopy(events[-1])]
    completion = without_verification[-1]
    completion["sequence"] = without_verification[-2]["sequence"] + 1
    completion["previous_event_id"] = without_verification[-2]["event_id"]
    completion["event_id"] = derive_workflow_execution_event_id_v1(completion)

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(without_verification)
    assert excinfo.value.code == "completion_inconsistent"


def test_ledger_replay_rejects_forged_verifier_target_receipt(tmp_path: Path) -> None:
    _run(tmp_path)
    events = deepcopy(
        read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    )
    packet_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "leaf_packet"
        and event["payload"]["packet"]["leaf_id"] == "verify"
    )
    packet = events[packet_index]["payload"]["packet"]
    packet["result"] = {"verified": True, "dependency": "inspect"}
    packet["evidence"] = [{"type": "runtime_receipt", "ref": "leaf:inspect"}]
    for index in range(packet_index, len(events)):
        events[index]["previous_event_id"] = events[index - 1]["event_id"]
        events[index]["event_id"] = derive_workflow_execution_event_id_v1(events[index])

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(events)

    assert excinfo.value.code == "verification_inconsistent"


def test_ledger_replay_rejects_stale_verifier_receipt_after_target_result_change(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    events = deepcopy(
        read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    )
    packet_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "leaf_packet"
        and event["payload"]["packet"]["leaf_id"] == "implement"
    )
    events[packet_index]["payload"]["packet"]["result"] = {"ok": False}
    for index in range(packet_index, len(events)):
        events[index]["previous_event_id"] = events[index - 1]["event_id"]
        events[index]["event_id"] = derive_workflow_execution_event_id_v1(events[index])

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(events)

    assert excinfo.value.code == "verification_inconsistent"


def test_ledger_replay_rejects_completed_packet_without_value(tmp_path: Path) -> None:
    _run(tmp_path)
    events = deepcopy(
        read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    )
    packet_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "leaf_packet"
        and event["payload"]["packet"]["leaf_id"] == "verify"
    )
    packet = events[packet_index]["payload"]["packet"]
    packet["value_present"] = False
    packet["result"] = None
    for index in range(packet_index, len(events)):
        events[index]["previous_event_id"] = events[index - 1]["event_id"]
        events[index]["event_id"] = derive_workflow_execution_event_id_v1(events[index])

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(events)

    assert excinfo.value.code == "packet_semantics_invalid"


def test_completed_null_is_distinct_from_missing_or_failed_result(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        definition=_definition(null_output=True),
        manifest=_manifest(null_output=True),
    )
    packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "implement"
    )

    assert packet["status"] == "completed"
    assert packet["value_present"] is True
    assert packet["result"] is None


def test_output_schema_failure_is_unverified_not_completed(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["implement"]["output_schema"] = {"type": "string"}

    result = _run(tmp_path, manifest=manifest)
    packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "implement"
    )

    assert packet["status"] == "unverified"
    assert packet["value_present"] is True
    assert packet["uncertainty"][0]["code"] == "output_schema_invalid"
    assert result["status"] == "run_completed"
    assert result["verification_state"] == "unverified"
    verify = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "verify"
    )
    assert verify["status"] == "blocked"


def test_unsupported_isolation_fails_before_ledger_or_reservation(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["isolation"] = "container"

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)
    assert excinfo.value.code == "isolation_unsupported"
    assert not (tmp_path / "run.jsonl").exists()


def test_unsupported_isolation_fails_before_any_runtime_state(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["isolation"] = "container"
    private_root = tmp_path / "runtime-state"

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            manifest,
            ledger_path=private_root / "run.jsonl",
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
        )
    assert excinfo.value.code == "isolation_unsupported"
    assert not private_root.exists()


def test_external_read_authority_projects_without_unhandled_key_error() -> None:
    definition = _definition()
    definition["authority"]["max_side_effect"] = "external_read"
    definition["tasks"][0]["authority"]["side_effect"] = "external_read"

    preview = compile_runtime_preview(definition, _manifest())

    assert preview["execution_enabled"] is False
    assert preview["task_count"] == 3


def test_non_unit_spawn_reservation_fails_before_ledger(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["reserve"]["spawns"] = 2

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)

    assert excinfo.value.code == "reservation_invalid"
    assert not (tmp_path / "run.jsonl").exists()


def test_hard_budget_blocks_new_leaf_and_preserves_partial_receipts(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["hard_budget"]["spawns"] = 1

    result = _run(tmp_path, manifest=manifest)

    assert result["budget"]["settled"]["spawns"] == 1
    inspect = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect"
    )
    implement = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "implement"
    )
    assert inspect["status"] == "completed"
    assert implement["status"] == "blocked"
    assert implement["uncertainty"][0]["code"] == "hard_budget_exhausted"
    assert result["outcome"] == "blocked"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("baseline_ref",), "git:other"),
        (("project_scope",), "/tmp/other"),
        (("profile_scope",), "other"),
        (("tool_policy_ref",), "policy:other"),
        (("model_contract_ref",), "model:other"),
        (("authority_ref",), "auth:other"),
        (("leaves", "inspect", "isolation"), "container"),
        (("leaves", "inspect", "output_schema"), {"type": "string"}),
        (("leaves", "inspect", "input"), {"source": "changed"}),
    ],
)
def test_resume_rejects_each_identity_dimension_drift(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    _run(tmp_path)
    changed = deepcopy(_manifest())
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=changed, resume=True, now=LATER)
    assert excinfo.value.code == "resume_identity_mismatch"


def test_status_uses_existing_ledger_lock_without_chmodding_state(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    transaction_lock = ledger.with_name(f".{ledger.name}.lock")
    transaction_lock.chmod(0o400)
    tmp_path.chmod(0o755)

    result = read_runtime_status(ledger, private_root=tmp_path)

    assert result["status"] == "run_completed"
    assert (transaction_lock.stat().st_mode & 0o777) == 0o400
    assert (tmp_path.stat().st_mode & 0o777) == 0o755


def test_status_reads_legacy_ledger_without_creating_lock_or_chmodding_root(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    transaction_lock = ledger.with_name(f".{ledger.name}.lock")
    transaction_lock.unlink()
    tmp_path.chmod(0o755)
    before_entries = sorted(path.name for path in tmp_path.iterdir())

    result = read_runtime_status(ledger, private_root=tmp_path)

    assert result["status"] == "run_completed"
    assert (tmp_path.stat().st_mode & 0o777) == 0o755
    assert sorted(path.name for path in tmp_path.iterdir()) == before_entries
    assert not transaction_lock.exists()


def test_resume_identity_rejection_does_not_create_execution_lock_or_chmod_root(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    execution_lock = ledger.with_name(f".{ledger.name}.execution.lock")
    execution_lock.unlink()
    tmp_path.chmod(0o755)
    before_entries = sorted(path.name for path in tmp_path.iterdir())

    changed = _manifest()
    changed["authority_ref"] = "auth:other"
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=changed, resume=True, now=LATER)

    assert excinfo.value.code == "resume_identity_mismatch"
    assert (tmp_path.stat().st_mode & 0o777) == 0o755
    assert sorted(path.name for path in tmp_path.iterdir()) == before_entries
    assert not execution_lock.exists()


def test_resume_rejects_unknown_identity_schema_version(tmp_path: Path) -> None:
    _run(tmp_path)
    changed = _manifest()
    changed["identity_schema_version"] = 2

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=changed, resume=True, now=LATER)
    assert excinfo.value.code == "identity_schema_unsupported"


def test_resume_requires_new_nonpersisted_execute_grant(tmp_path: Path) -> None:
    _run(tmp_path)

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=tmp_path / "run.jsonl",
            private_root=tmp_path,
            execute_grant=None,
            resume=True,
            now=LATER,
        )
    assert excinfo.value.code == "execute_grant_required"


def test_torn_ledger_tail_fails_closed_without_rewriting_history(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    original = ledger.read_bytes()
    ledger.write_bytes(original + b'{"truncated":')

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(ledger, private_root=tmp_path)
    assert excinfo.value.code == "torn_tail"
    assert ledger.read_bytes() == original + b'{"truncated":'


def test_fresh_run_recovers_empty_first_create_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "run.jsonl"
    ledger.write_bytes(b"")
    ledger.chmod(0o600)

    result = _run(tmp_path)

    assert result["status"] == "run_completed"
    assert read_execution_events(ledger, private_root=tmp_path)


def _seed_reserved_start(
    tmp_path: Path,
    *,
    expires_at: str,
    hard_spawns: int = 3,
    mark_started: bool = True,
) -> None:
    manifest = _manifest()
    manifest["hard_budget"]["spawns"] = hard_spawns
    preview = compile_runtime_preview(_definition(), manifest)
    ledger = tmp_path / "run.jsonl"
    common = {
        "ledger_path": ledger,
        "private_root": tmp_path,
        "run_id": manifest["run_id"],
        "occurred_at": NOW,
    }
    append_execution_event(
        **common,
        event_type="run_accepted",
        payload={
            "identity_schema_version": 1,
            "identity": preview["identity"],
            "identity_hash": preview["identity_hash"],
            "manifest_digest": preview["manifest_digest"],
            "definition_digest": preview["definition_digest"],
            "plan_digest": preview["plan_digest"],
            "hard_budget": manifest["hard_budget"],
            "task_order": ["inspect", "implement", "verify"],
            "verification_task_ids": ["verify"],
        },
        idempotency_key="seed:accepted",
    )
    append_execution_event(
        **common, event_type="run_started", payload={}, idempotency_key="seed:started"
    )
    append_execution_event(
        **common,
        event_type="budget_reserved",
        payload={
            "reservation_id": "seed:reservation",
            "task_id": "inspect",
            "amounts": _reservation(),
            "expires_at": expires_at,
        },
        idempotency_key="seed:reserve",
    )
    if mark_started:
        append_execution_event(
            **common,
            event_type="leaf_started",
            payload={"leaf_id": "inspect", "reservation_id": "seed:reservation"},
            idempotency_key="seed:leaf-started",
        )


def test_resume_recovers_ledger_interrupted_after_acceptance(tmp_path: Path) -> None:
    manifest = _manifest()
    preview = compile_runtime_preview(_definition(), manifest)
    append_execution_event(
        tmp_path / "run.jsonl",
        private_root=tmp_path,
        run_id=manifest["run_id"],
        event_type="run_accepted",
        payload={
            "identity_schema_version": 1,
            "identity": preview["identity"],
            "identity_hash": preview["identity_hash"],
            "manifest_digest": preview["manifest_digest"],
            "definition_digest": preview["definition_digest"],
            "plan_digest": preview["plan_digest"],
            "hard_budget": manifest["hard_budget"],
            "task_order": ["inspect", "implement", "verify"],
            "verification_task_ids": ["verify"],
        },
        idempotency_key="seed:accepted-only",
        occurred_at=NOW,
    )

    result = _run(tmp_path, resume=True, now=LATER)

    assert result["status"] == "run_completed"
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    assert [event["event_type"] for event in events[:2]] == [
        "run_accepted",
        "run_started",
    ]


def test_resume_fails_closed_on_active_and_expired_started_reservations(
    tmp_path: Path,
) -> None:
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z")

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, resume=True, now=NOW)
    assert excinfo.value.code == "reservation_active"

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, resume=True, now=LATER)
    assert excinfo.value.code == "reservation_orphaned"


def test_resume_fails_closed_after_settlement_without_terminal_packet(
    tmp_path: Path,
) -> None:
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z")
    append_execution_event(
        tmp_path / "run.jsonl",
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="budget_settled",
        payload={"reservation_id": "seed:reservation", "actual": _reservation()},
        idempotency_key="seed:settled",
        occurred_at=NOW,
    )

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, resume=True, now=LATER)
    assert excinfo.value.code == "leaf_recovery_unsafe"


def test_failed_started_leaf_consumes_reserved_budget(
    monkeypatch, tmp_path: Path
) -> None:
    definition = _definition()
    next(task for task in definition["tasks"] if task["id"] == "implement")[
        "depends_on"
    ] = []
    manifest = _manifest()
    manifest["max_concurrency"] = 1
    manifest["hard_budget"] = _reservation()
    calls = 0
    original = governed_runtime._builtin_adapters()

    def fail_adapter(value, dependencies):
        nonlocal calls
        del value, dependencies
        calls += 1
        raise RuntimeError("adapter failed after dispatch")

    monkeypatch.setattr(
        governed_runtime,
        "_builtin_adapters",
        lambda: {
            **original,
            "local.echo": governed_runtime._Adapter(
                adapter_id="local.echo",
                capabilities=frozenset(),
                side_effect="none",
                supported_isolations=frozenset({"in_process_trusted_pure"}),
                execute=fail_adapter,
            ),
        },
    )

    result = _run(tmp_path, definition=definition, manifest=manifest)

    assert calls == 1
    assert result["budget"]["settled"] == _reservation()


def test_projection_rejects_verified_or_converged_completion_over_cancelled_packets(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    run_workflow(
        _definition(),
        manifest,
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=NOW,
        cancel_requested=lambda: True,
    )
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    events[-1]["payload"]["verification_state"] = "verified"
    events[-1]["payload"]["convergence_state"] = "converged"
    events[-1]["event_id"] = derive_workflow_execution_event_id_v1(events[-1])

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(events)
    assert excinfo.value.code == "completion_inconsistent"


def test_runtime_identity_normalizes_integral_json_numbers() -> None:
    integer_manifest = _manifest()
    float_manifest = deepcopy(integer_manifest)
    float_manifest["hard_budget"]["wall_ms"] = 30_000.0
    float_manifest["leaves"]["inspect"]["reserve"]["wall_ms"] = 10_000.0

    integer_preview = compile_runtime_preview(_definition(), integer_manifest)
    float_preview = compile_runtime_preview(_definition(), float_manifest)

    assert float_preview["manifest_digest"] == integer_preview["manifest_digest"]
    assert float_preview["identity_hash"] == integer_preview["identity_hash"]


def test_ledger_rejects_release_after_leaf_dispatch(tmp_path: Path) -> None:
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z")

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            tmp_path / "run.jsonl",
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_released",
            payload={"reservation_id": "seed:reservation", "reason": "must_not_refund"},
            idempotency_key="seed:invalid-release",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "started_reservation_release"


def test_ledger_rejects_settlement_before_leaf_dispatch(tmp_path: Path) -> None:
    _seed_reserved_start(
        tmp_path,
        expires_at="2026-07-23T05:01:00Z",
        mark_started=False,
    )

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            tmp_path / "run.jsonl",
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_settled",
            payload={"reservation_id": "seed:reservation", "actual": _reservation()},
            idempotency_key="seed:settle-before-dispatch",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "reservation_not_started"


def test_ledger_rejects_spawn_refund_after_leaf_dispatch(tmp_path: Path) -> None:
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z")
    refunded = {**_reservation(), "spawns": 0}

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            tmp_path / "run.jsonl",
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_settled",
            payload={"reservation_id": "seed:reservation", "actual": refunded},
            idempotency_key="seed:refund-dispatched-spawn",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "started_reservation_refund"


def test_ledger_rejects_new_reservation_after_cancellation_intent(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "run.jsonl"
    _seed_reserved_start(
        tmp_path,
        expires_at="2026-07-23T05:01:00Z",
        mark_started=False,
    )
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="budget_released",
        payload={"reservation_id": "seed:reservation", "reason": "test_reset"},
        idempotency_key="seed:release-before-cancel",
        occurred_at=NOW,
    )
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="run_cancel_requested",
        payload={"reason": "cancellation_requested", "message": None},
        idempotency_key="seed:cancel",
        occurred_at=NOW,
    )

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_reserved",
            payload={
                "reservation_id": "seed:after-cancel",
                "task_id": "implement",
                "amounts": _reservation(),
                "expires_at": "2026-07-23T05:02:00Z",
            },
            idempotency_key="seed:reserve-after-cancel",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "invalid_transition"


def test_ledger_rejects_leaf_start_after_cancellation_intent(tmp_path: Path) -> None:
    ledger = tmp_path / "run.jsonl"
    _seed_reserved_start(
        tmp_path,
        expires_at="2026-07-23T05:01:00Z",
        mark_started=False,
    )
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="run_cancel_requested",
        payload={"reason": "cancellation_requested", "message": None},
        idempotency_key="seed:cancel-before-start",
        occurred_at=NOW,
    )

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="leaf_started",
            payload={"leaf_id": "inspect", "reservation_id": "seed:reservation"},
            idempotency_key="seed:start-after-cancel",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "invalid_transition"


def test_reservation_and_task_ids_cannot_be_reused_after_dispatch(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "run.jsonl"
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z")
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="budget_settled",
        payload={"reservation_id": "seed:reservation", "actual": _reservation()},
        idempotency_key="seed:settle-before-reuse",
        occurred_at=NOW,
    )

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_reserved",
            payload={
                "reservation_id": "seed:reservation",
                "task_id": "implement",
                "amounts": _reservation(),
                "expires_at": "2026-07-23T05:02:00Z",
            },
            idempotency_key="seed:reuse-reservation",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "reservation_conflict"

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_reserved",
            payload={
                "reservation_id": "seed:second-reservation",
                "task_id": "inspect",
                "amounts": _reservation(),
                "expires_at": "2026-07-23T05:02:00Z",
            },
            idempotency_key="seed:second-reservation",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "reservation_conflict"


def test_budget_reservation_rejects_unknown_task(tmp_path: Path) -> None:
    _seed_reserved_start(
        tmp_path,
        expires_at="2026-07-23T05:01:00Z",
        mark_started=False,
    )
    append_execution_event(
        tmp_path / "run.jsonl",
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="budget_released",
        payload={"reservation_id": "seed:reservation", "reason": "test_reset"},
        idempotency_key="seed:test-reset",
        occurred_at=NOW,
    )
    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            tmp_path / "run.jsonl",
            private_root=tmp_path,
            run_id=_manifest()["run_id"],
            event_type="budget_reserved",
            payload={
                "reservation_id": "seed:unknown-task",
                "task_id": "not-in-plan",
                "amounts": _reservation(),
                "expires_at": "2026-07-23T05:02:00Z",
            },
            idempotency_key="seed:unknown-task",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "leaf_unknown"


def test_leaf_packet_usage_must_match_durable_settlement(tmp_path: Path) -> None:
    _run(tmp_path)
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    packet_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "leaf_packet"
    )
    prefix = events[: packet_index + 1]
    prefix[-1]["payload"]["packet"]["usage"]["wall_ms"] += 1
    prefix[-1]["event_id"] = derive_workflow_execution_event_id_v1(prefix[-1])

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(prefix)
    assert excinfo.value.code == "packet_usage_mismatch"


def test_non_completed_leaf_packet_requires_uncertainty_evidence(
    tmp_path: Path,
) -> None:
    run_workflow(
        _definition(),
        _manifest(),
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=NOW,
        cancel_requested=lambda: True,
    )
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    packet_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "leaf_packet"
    )
    prefix = events[: packet_index + 1]
    prefix[-1]["payload"]["packet"]["uncertainty"] = []
    prefix[-1]["event_id"] = derive_workflow_execution_event_id_v1(prefix[-1])

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        project_execution_events(prefix)
    assert excinfo.value.code == "packet_semantics_invalid"


def test_concurrent_budget_reservations_are_serialized_atomically(
    tmp_path: Path,
) -> None:
    _seed_reserved_start(
        tmp_path,
        expires_at="2026-07-23T05:01:00Z",
        hard_spawns=1,
        mark_started=False,
    )
    ledger = tmp_path / "run.jsonl"
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=_manifest()["run_id"],
        event_type="budget_released",
        payload={"reservation_id": "seed:reservation", "reason": "concurrency_test"},
        idempotency_key="seed:release",
        occurred_at=NOW,
    )

    def reserve(task_id: str) -> str:
        try:
            append_execution_event(
                ledger,
                private_root=tmp_path,
                run_id=_manifest()["run_id"],
                event_type="budget_reserved",
                payload={
                    "reservation_id": f"race:{task_id}",
                    "task_id": task_id,
                    "amounts": _reservation(),
                    "expires_at": "2026-07-23T05:01:00Z",
                },
                idempotency_key=f"race:{task_id}",
                occurred_at=NOW,
            )
            return "admitted"
        except WorkflowExecutionLedgerError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(reserve, ["inspect", "implement"]))
    assert outcomes == ["admitted", "hard_budget_exhausted"]
    projection = project_execution_events(
        read_execution_events(ledger, private_root=tmp_path)
    )
    assert len(projection["reservations"]) == 1


def test_concurrent_run_invocations_have_single_execution_owner(
    monkeypatch, tmp_path: Path
) -> None:
    original_exists = governed_runtime.private_file_exists_read_only
    original_echo = governed_runtime._echo_adapter
    initial_reads = Barrier(2)
    counter_lock = Lock()
    calls = 0
    read_calls = 0

    def synchronized_initial_read(ledger_path, *, private_root):
        nonlocal read_calls
        exists = original_exists(ledger_path, private_root=private_root)
        with counter_lock:
            read_calls += 1
            current_read = read_calls
        if current_read <= 2:
            initial_reads.wait(timeout=5)
        return exists

    def counting_echo(value, dependencies):
        nonlocal calls
        with counter_lock:
            calls += 1
        return original_echo(value, dependencies)

    monkeypatch.setattr(
        governed_runtime, "private_file_exists_read_only", synchronized_initial_read
    )
    monkeypatch.setattr(governed_runtime, "_echo_adapter", counting_echo)
    manifest = _manifest()
    manifest["max_concurrency"] = 1

    def invoke() -> str:
        try:
            result = _run(tmp_path, manifest=manifest)
            return result["status"]
        except WorkflowRuntimeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: invoke(), range(2)))

    assert outcomes == ["run_completed", "run_exists"]
    assert calls == 2


def test_multiprocess_run_invocations_have_single_execution_owner(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    ledger = tmp_path / "run.jsonl"
    processes = [
        context.Process(
            target=_multiprocess_run_worker,
            args=(str(ledger), str(tmp_path), start, results),
        )
        for _index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = sorted(results.get(timeout=15) for _process in processes)
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert outcomes == ["completed", "run_exists"]
    events = read_execution_events(ledger, private_root=tmp_path)
    projection = project_execution_events(events)
    assert projection["status"] == "run_completed"
    assert [event["event_type"] for event in events].count("leaf_started") == 3
    assert [event["event_type"] for event in events].count("leaf_packet") == 3


def test_terminal_append_failure_resumes_without_redispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_append = governed_runtime._append
    failed_once = False

    def fail_first_completion(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal failed_once
        event_type = args[3]
        if event_type == "run_completed" and not failed_once:
            failed_once = True
            raise OSError("terminal append unavailable")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(governed_runtime, "_append", fail_first_completion)
    with pytest.raises(OSError, match="terminal append unavailable"):
        _run(tmp_path)

    events_before = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    assert [event["event_type"] for event in events_before].count("leaf_started") == 3
    assert [event["event_type"] for event in events_before].count("leaf_packet") == 3
    assert [event["event_type"] for event in events_before].count("run_completed") == 0

    monkeypatch.setattr(governed_runtime, "_append", original_append)
    resumed = _run(tmp_path, resume=True, now=LATER)
    events_after = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)

    assert resumed["status"] == "run_completed"
    assert [event["event_type"] for event in events_after].count("leaf_started") == 3
    assert [event["event_type"] for event in events_after].count("leaf_packet") == 3
    assert [event["event_type"] for event in events_after].count("run_completed") == 1


def test_resume_waits_for_in_flight_initial_append(monkeypatch, tmp_path: Path) -> None:
    original_write_all = execution_ledger._write_all
    original_exists = governed_runtime.private_file_exists_read_only
    partial_written = Event()
    release_write = Event()
    contender_preflight = Event()
    first_write = True
    exists_calls = 0
    write_lock = Lock()

    def tracked_exists(ledger_path, *, private_root):
        nonlocal exists_calls
        result = original_exists(ledger_path, private_root=private_root)
        with write_lock:
            exists_calls += 1
            if exists_calls == 2:
                contender_preflight.set()
        return result

    def pausing_write_all(descriptor: int, record: bytes) -> None:
        nonlocal first_write
        with write_lock:
            pause_this_write = first_write
            first_write = False
        if not pause_this_write:
            original_write_all(descriptor, record)
            return
        midpoint = max(1, len(record) // 2)
        execution_ledger.os.write(descriptor, record[:midpoint])
        partial_written.set()
        assert release_write.wait(5)
        original_write_all(descriptor, record[midpoint:])

    monkeypatch.setattr(execution_ledger, "_write_all", pausing_write_all)
    monkeypatch.setattr(
        governed_runtime, "private_file_exists_read_only", tracked_exists
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(_run, tmp_path)
        assert partial_written.wait(2)
        contender = executor.submit(_run, tmp_path, resume=True, now=LATER)
        try:
            assert contender_preflight.wait(2)
            try:
                contender.result(timeout=0.2)
                contender_was_blocked = False
            except FutureTimeoutError:
                contender_was_blocked = True
        finally:
            release_write.set()
        owner_result = owner.result(timeout=5)
        contender_result = contender.result(timeout=5)

    assert contender_was_blocked
    assert owner_result["status"] == "run_completed"
    assert contender_result["status"] == "run_completed"


def test_resume_waits_for_cooperative_direct_ledger_append(
    monkeypatch, tmp_path: Path
) -> None:
    manifest = _manifest()
    preview = compile_runtime_preview(_definition(), manifest)
    ledger_path = tmp_path / "run.jsonl"
    append_execution_event(
        ledger_path,
        private_root=tmp_path,
        run_id=manifest["run_id"],
        event_type="run_accepted",
        payload={
            "identity_schema_version": 1,
            "identity": preview["identity"],
            "identity_hash": preview["identity_hash"],
            "manifest_digest": preview["manifest_digest"],
            "definition_digest": preview["definition_digest"],
            "plan_digest": preview["plan_digest"],
            "hard_budget": manifest["hard_budget"],
            "task_order": ["inspect", "implement", "verify"],
            "verification_task_ids": ["verify"],
        },
        idempotency_key="seed:accepted-direct-writer",
        occurred_at=NOW,
    )
    original_write_all = execution_ledger._write_all
    partial_written = Event()
    release_write = Event()
    first_write = True
    guard = Lock()

    def pausing_write_all(descriptor: int, record: bytes) -> None:
        nonlocal first_write
        with guard:
            pause_this_write = first_write
            first_write = False
        if not pause_this_write:
            original_write_all(descriptor, record)
            return
        midpoint = max(1, len(record) // 2)
        execution_ledger.os.write(descriptor, record[:midpoint])
        partial_written.set()
        assert release_write.wait(5)
        original_write_all(descriptor, record[midpoint:])

    monkeypatch.setattr(execution_ledger, "_write_all", pausing_write_all)

    def direct_writer() -> None:
        append_execution_event(
            ledger_path,
            private_root=tmp_path,
            run_id=manifest["run_id"],
            event_type="run_started",
            payload={},
            idempotency_key="seed:started-direct-writer",
            occurred_at=LATER,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(direct_writer)
        assert partial_written.wait(2)
        resume = executor.submit(_run, tmp_path, resume=True, now=LATER)
        try:
            with pytest.raises(FutureTimeoutError):
                resume.result(timeout=0.2)
        finally:
            release_write.set()
        writer.result(timeout=5)
        result = resume.result(timeout=5)

    assert result["status"] == "run_completed"


def test_run_exists_fails_without_creating_execution_lock(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    ledger = private_root / "run.jsonl"
    _run(private_root)
    execution_lock = ledger.with_name(f".{ledger.name}.execution.lock")
    execution_lock.unlink()

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=ledger,
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            now=LATER,
        )

    assert excinfo.value.code == "run_exists"
    assert not execution_lock.exists()


@pytest.mark.parametrize(
    "invalid_time",
    [
        "2026-07-23 05:00:00+00:00",
        "2026-02-30T05:00:00Z",
        "2026-07-23t05:00:00z",
        "2016-12-31T23:59:60Z",
    ],
)
def test_non_rfc3339_invocation_time_fails_without_creating_runtime_state(
    tmp_path: Path,
    invalid_time: str,
) -> None:
    private_root = tmp_path / "private"

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=private_root / "run.jsonl",
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            now=invalid_time,
        )

    assert excinfo.value.code == "time_invalid"
    assert not private_root.exists()


def test_windows_execution_fails_closed_before_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root = tmp_path / "private"
    monkeypatch.setattr(governed_runtime, "_runtime_platform", lambda: "nt")

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=private_root / "run.jsonl",
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
        )

    assert excinfo.value.code == "runtime_platform_unsupported"
    assert not private_root.exists()


def test_invalid_invocation_time_fails_without_creating_runtime_state(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=private_root / "run.jsonl",
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            now="not-an-instant",
        )

    assert excinfo.value.code == "time_invalid"
    assert not private_root.exists()


def test_resume_missing_fails_without_creating_runtime_state(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=private_root / "run.jsonl",
            private_root=private_root,
            execute_grant=ExecuteGrant.local_only(),
            resume=True,
            now=NOW,
        )

    assert excinfo.value.code == "resume_missing"
    assert not private_root.exists()


def test_cancellation_callback_failure_is_durably_terminal(tmp_path: Path) -> None:
    def broken_cancellation_probe() -> bool:
        raise RuntimeError("cancellation backend unavailable")

    result = run_workflow(
        _definition(),
        _manifest(),
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=NOW,
        cancel_requested=broken_cancellation_probe,
    )

    assert result["status"] == "run_completed"
    assert result["outcome"] == "blocked"
    assert result["verification_state"] == "unverified"
    assert all(packet["status"] == "blocked" for packet in result["leaf_packets"])
    assert all(
        packet["uncertainty"][0]["code"] == "cancellation_probe_failed"
        for packet in result["leaf_packets"]
    )
    assert read_runtime_status(tmp_path / "run.jsonl", private_root=tmp_path) == result


def test_cancellation_stops_admitting_new_leaves_and_preserves_receipts(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["max_concurrency"] = 1
    checks = iter([False, True, True])

    result = run_workflow(
        _definition(),
        manifest,
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=NOW,
        cancel_requested=lambda: next(checks, True),
    )

    packets = {packet["leaf_id"]: packet for packet in result["leaf_packets"]}
    assert packets["inspect"]["status"] == "completed"
    assert packets["implement"]["status"] == "cancelled"
    assert packets["verify"]["status"] == "cancelled"
    assert result["budget"]["settled"]["spawns"] == 1
    assert result["outcome"] == "cancelled"
    assert result["verification_state"] == "unverified"


def test_missing_manifest_schema_fails_closed_before_ledger(
    monkeypatch, tmp_path: Path
) -> None:
    import workflow_kernel.governed_runtime as runtime

    runtime._manifest_validator.cache_clear()
    monkeypatch.setattr(
        runtime.importlib.resources,
        "files",
        lambda _package: (_ for _ in ()).throw(FileNotFoundError("schema missing")),
    )
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path)
    assert excinfo.value.code == "schema_unavailable"
    assert not (tmp_path / "run.jsonl").exists()
    runtime._manifest_validator.cache_clear()


def test_remote_output_schema_reference_is_rejected_before_execution(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["output_schema"] = {
        "$ref": "https://attacker.invalid/schema.json"
    }

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)

    assert excinfo.value.code == "output_schema_remote_ref"
    assert not (tmp_path / "run.jsonl").exists()


def test_hash_chain_tamper_fails_closed_without_repair(tmp_path: Path) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    raw = ledger.read_text(encoding="utf-8").splitlines()
    second = json.loads(raw[1])
    second["previous_event_id"] = None
    second["event_id"] = derive_workflow_execution_event_id_v1(second)
    raw[1] = json.dumps(
        second, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    tampered = "\n".join(raw) + "\n"
    ledger.write_text(tampered, encoding="utf-8")

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(ledger, private_root=tmp_path)
    assert excinfo.value.code == "broken_link"
    assert ledger.read_text(encoding="utf-8") == tampered


def test_executor_submit_failure_is_durably_terminalized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class SubmitFailingExecutor:
        def __init__(self, *, max_workers: int) -> None:
            assert max_workers > 0

        def __enter__(self) -> SubmitFailingExecutor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def submit(self, *_args: object, **_kwargs: object) -> NoReturn:
            raise RuntimeError("submit unavailable")

    monkeypatch.setattr(governed_runtime, "ThreadPoolExecutor", SubmitFailingExecutor)

    result = _run(tmp_path)

    packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect"
    )
    assert packet["status"] == "failed"
    assert packet["uncertainty"] == [
        {
            "code": "dispatch_submit_failed",
            "message": "RuntimeError: submit unavailable",
        }
    ]
    assert packet["usage"] == _manifest()["leaves"]["inspect"]["reserve"]
    assert result["outcome"] == "failed"
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    projection = project_execution_events(events)
    assert projection["status"] == "run_completed"
    assert projection["reservations"] == {}


def test_adapter_failure_is_distinct_from_null_blocked_and_unverified(
    monkeypatch, tmp_path: Path
) -> None:
    adapters = governed_runtime._builtin_adapters()
    original = adapters["local.echo"]

    def fail_adapter(
        value: object, dependencies: Mapping[str, dict[str, object]]
    ) -> NoReturn:
        del value, dependencies
        raise RuntimeError("deterministic adapter failure")

    adapters["local.echo"] = governed_runtime._Adapter(
        adapter_id=original.adapter_id,
        capabilities=original.capabilities,
        side_effect=original.side_effect,
        supported_isolations=original.supported_isolations,
        execute=fail_adapter,
    )
    monkeypatch.setattr(governed_runtime, "_builtin_adapters", lambda: adapters)

    result = _run(tmp_path)
    packet = next(
        packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect"
    )

    assert packet["status"] == "failed"
    assert packet["value_present"] is False
    assert packet["result"] is None
    assert packet["uncertainty"][0]["code"] == "adapter_failed"
    assert result["outcome"] == "failed"
    assert result["verification_state"] == "unverified"


def test_scheduler_never_exceeds_declared_concurrency_bound(tmp_path: Path) -> None:
    definition = _definition()
    definition["tasks"][1]["depends_on"] = []
    manifest = _manifest()
    manifest["max_concurrency"] = 2

    _run(tmp_path, definition=definition, manifest=manifest)
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    outstanding: set[str] = set()
    peak = 0
    for event in events:
        if event["event_type"] == "budget_reserved":
            outstanding.add(event["payload"]["reservation_id"])
            peak = max(peak, len(outstanding))
        elif event["event_type"] in {"budget_settled", "budget_released"}:
            outstanding.discard(event["payload"]["reservation_id"])
    assert peak == 2
    assert not outstanding


def test_duplicate_physical_event_record_fails_closed(tmp_path: Path) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    records = ledger.read_text(encoding="utf-8").splitlines()
    ledger.write_text("\n".join([*records, records[-1]]) + "\n", encoding="utf-8")

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(ledger, private_root=tmp_path)
    assert excinfo.value.code == "duplicate_event"


def test_event_time_order_uses_instants_not_lexical_offsets(tmp_path: Path) -> None:
    manifest = _manifest()
    preview = compile_runtime_preview(_definition(), manifest)
    ledger = tmp_path / "run.jsonl"
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=manifest["run_id"],
        event_type="run_accepted",
        payload={
            "identity_schema_version": 1,
            "identity": preview["identity"],
            "identity_hash": preview["identity_hash"],
            "manifest_digest": preview["manifest_digest"],
            "definition_digest": preview["definition_digest"],
            "plan_digest": preview["plan_digest"],
            "hard_budget": manifest["hard_budget"],
            "task_order": ["inspect", "implement", "verify"],
            "verification_task_ids": ["verify"],
        },
        idempotency_key="time:accepted",
        occurred_at="2026-07-23T05:00:00Z",
    )
    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=manifest["run_id"],
            event_type="run_started",
            payload={},
            idempotency_key="time:started",
            occurred_at="2026-07-23T06:00:00+02:00",
        )
    assert excinfo.value.code == "time_reversal"


def test_event_time_order_rejects_submicrosecond_reversal(tmp_path: Path) -> None:
    manifest = _manifest()
    preview = compile_runtime_preview(_definition(), manifest)
    ledger = tmp_path / "run.jsonl"
    accepted_payload = {
        "identity_schema_version": 1,
        "identity": preview["identity"],
        "identity_hash": preview["identity_hash"],
        "manifest_digest": preview["manifest_digest"],
        "definition_digest": preview["definition_digest"],
        "plan_digest": preview["plan_digest"],
        "hard_budget": manifest["hard_budget"],
        "task_order": ["inspect", "implement", "verify"],
        "verification_task_ids": ["verify"],
    }
    append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=manifest["run_id"],
        event_type="run_accepted",
        payload=accepted_payload,
        idempotency_key="submicro:accepted",
        occurred_at="2026-07-23T05:00:00.0000009Z",
    )

    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=manifest["run_id"],
            event_type="run_started",
            payload={},
            idempotency_key="submicro:started",
            occurred_at="2026-07-23T05:00:00.0000001Z",
        )

    assert excinfo.value.code == "time_reversal"


def test_durability_unknown_retry_reestablishes_fsync_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest()
    preview = compile_runtime_preview(_definition(), manifest)
    ledger = tmp_path / "run.jsonl"
    accepted_payload = {
        "identity_schema_version": 1,
        "identity": preview["identity"],
        "identity_hash": preview["identity_hash"],
        "manifest_digest": preview["manifest_digest"],
        "definition_digest": preview["definition_digest"],
        "plan_digest": preview["plan_digest"],
        "hard_budget": manifest["hard_budget"],
        "task_order": ["inspect", "implement", "verify"],
        "verification_task_ids": ["verify"],
    }

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(execution_ledger.os, "fsync", fail_fsync)
    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=manifest["run_id"],
            event_type="run_accepted",
            payload=accepted_payload,
            idempotency_key="durability:accepted",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "durability_unknown"

    retry_fsync_calls = 0

    def record_retry_fsync(_descriptor: int) -> None:
        nonlocal retry_fsync_calls
        retry_fsync_calls += 1

    monkeypatch.setattr(execution_ledger.os, "fsync", record_retry_fsync)
    projection = append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=manifest["run_id"],
        event_type="run_accepted",
        payload=accepted_payload,
        idempotency_key="durability:accepted",
        occurred_at=NOW,
    )

    assert projection["status"] == "accepted"
    assert retry_fsync_calls == 2


def test_ledger_write_all_retries_interrupts_and_partial_writes(monkeypatch) -> None:
    read_descriptor, write_descriptor = execution_ledger.os.pipe()
    original_write = execution_ledger.os.write
    calls = 0

    def flaky_write(descriptor: int, value: memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError
        return original_write(descriptor, value[:3])

    monkeypatch.setattr(execution_ledger.os, "write", flaky_write)
    record = b"canonical-ledger-record\n"
    try:
        execution_ledger._write_all(write_descriptor, record)
        execution_ledger.os.close(write_descriptor)
        write_descriptor = -1
        assert execution_ledger.os.read(read_descriptor, len(record)) == record
        assert calls > 2
    finally:
        execution_ledger.os.close(read_descriptor)
        if write_descriptor >= 0:
            execution_ledger.os.close(write_descriptor)


def test_completed_resume_reestablishes_terminal_event_durability(
    tmp_path: Path, monkeypatch
) -> None:
    ledger = tmp_path / "run.jsonl"
    original_append = governed_runtime.append_execution_event
    injected = False

    def append_then_report_uncertain(*args, **kwargs):
        nonlocal injected
        projection = original_append(*args, **kwargs)
        if kwargs["event_type"] == "run_completed" and not injected:
            injected = True
            raise WorkflowExecutionLedgerError(
                "durability_unknown", "injected terminal durability uncertainty"
            )
        return projection

    monkeypatch.setattr(
        governed_runtime, "append_execution_event", append_then_report_uncertain
    )
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=ledger,
            private_root=tmp_path,
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
        )
    assert excinfo.value.code == "durability_unknown"
    assert (
        read_runtime_status(ledger, private_root=tmp_path)["status"] == "run_completed"
    )

    terminal_retries = 0

    def record_terminal_retry(*args, **kwargs):
        nonlocal terminal_retries
        if kwargs["event_type"] == "run_completed":
            terminal_retries += 1
        return original_append(*args, **kwargs)

    monkeypatch.setattr(
        governed_runtime, "append_execution_event", record_terminal_retry
    )
    resumed = run_workflow(
        _definition(),
        _manifest(),
        ledger_path=ledger,
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=LATER,
        resume=True,
    )

    assert resumed["status"] == "run_completed"
    assert terminal_retries == 1


def test_durable_cancellation_intent_prevents_admission_after_resume(
    tmp_path: Path, monkeypatch
) -> None:
    ledger = tmp_path / "run.jsonl"
    original_append = governed_runtime.append_execution_event
    cancelled_packets = 0

    def fail_before_second_cancelled_packet(*args, **kwargs):
        nonlocal cancelled_packets
        if (
            kwargs["event_type"] == "leaf_packet"
            and kwargs["payload"]["packet"]["status"] == "cancelled"
        ):
            cancelled_packets += 1
            if cancelled_packets == 2:
                raise WorkflowExecutionLedgerError(
                    "write_failed", "injected cancelled packet append failure"
                )
        return original_append(*args, **kwargs)

    monkeypatch.setattr(
        governed_runtime,
        "append_execution_event",
        fail_before_second_cancelled_packet,
    )
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=ledger,
            private_root=tmp_path,
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
            cancel_requested=lambda: True,
        )
    assert excinfo.value.code == "write_failed"

    events = read_execution_events(ledger, private_root=tmp_path)
    assert any(event["event_type"] == "run_cancel_requested" for event in events)
    dispatched = 0
    original_execute_leaf = governed_runtime._execute_leaf

    def record_dispatch(*args, **kwargs):
        nonlocal dispatched
        dispatched += 1
        return original_execute_leaf(*args, **kwargs)

    monkeypatch.setattr(governed_runtime, "append_execution_event", original_append)
    monkeypatch.setattr(governed_runtime, "_execute_leaf", record_dispatch)
    resumed = run_workflow(
        _definition(),
        _manifest(),
        ledger_path=ledger,
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=LATER,
        resume=True,
    )

    assert dispatched == 0
    assert resumed["outcome"] == "cancelled"
    assert {packet["status"] for packet in resumed["leaf_packets"]} == {"cancelled"}


@pytest.mark.parametrize(
    "payload",
    [
        {"reason": "unknown", "message": None},
        {"reason": "cancellation_requested", "message": "unexpected"},
        {"reason": "cancellation_probe_failed", "message": None},
    ],
)
def test_cancellation_intent_payload_rejects_ambiguous_semantics(payload) -> None:
    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        execution_ledger._validate_payload("run_cancel_requested", payload)
    assert excinfo.value.code == "payload_invalid"


def test_durable_cancellation_probe_failure_remains_blocked_after_resume(
    tmp_path: Path, monkeypatch
) -> None:
    ledger = tmp_path / "run.jsonl"
    original_append = governed_runtime.append_execution_event
    blocked_packets = 0

    def fail_before_second_blocked_packet(*args, **kwargs):
        nonlocal blocked_packets
        if (
            kwargs["event_type"] == "leaf_packet"
            and kwargs["payload"]["packet"]["status"] == "blocked"
            and kwargs["payload"]["packet"]["uncertainty"][0]["code"]
            == "cancellation_probe_failed"
        ):
            blocked_packets += 1
            if blocked_packets == 2:
                raise WorkflowExecutionLedgerError(
                    "write_failed", "injected blocked packet append failure"
                )
        return original_append(*args, **kwargs)

    def broken_probe() -> bool:
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(
        governed_runtime, "append_execution_event", fail_before_second_blocked_packet
    )
    with pytest.raises(WorkflowRuntimeError) as excinfo:
        run_workflow(
            _definition(),
            _manifest(),
            ledger_path=ledger,
            private_root=tmp_path,
            execute_grant=ExecuteGrant.local_only(),
            now=NOW,
            cancel_requested=broken_probe,
        )
    assert excinfo.value.code == "write_failed"

    monkeypatch.setattr(governed_runtime, "append_execution_event", original_append)
    resumed = run_workflow(
        _definition(),
        _manifest(),
        ledger_path=ledger,
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        now=LATER,
        resume=True,
    )

    assert resumed["outcome"] == "blocked"
    assert {packet["status"] for packet in resumed["leaf_packets"]} == {"blocked"}
    assert {packet["uncertainty"][0]["code"] for packet in resumed["leaf_packets"]} == {
        "cancellation_probe_failed"
    }


def test_partial_append_error_rolls_back_record_for_idempotent_retry(
    tmp_path: Path, monkeypatch
) -> None:
    ledger = tmp_path / "run.jsonl"
    manifest = _manifest()
    preview = compile_runtime_preview(_definition(), manifest)
    accepted_payload = {
        "identity_schema_version": 1,
        "identity": preview["identity"],
        "identity_hash": preview["identity_hash"],
        "manifest_digest": preview["manifest_digest"],
        "definition_digest": preview["definition_digest"],
        "plan_digest": preview["plan_digest"],
        "hard_budget": manifest["hard_budget"],
        "task_order": ["inspect", "implement", "verify"],
        "verification_task_ids": ["verify"],
    }
    original_write = execution_ledger.os.write
    calls = 0

    def partial_then_fail(descriptor: int, value: memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, value[:7])
        raise OSError("injected append failure")

    monkeypatch.setattr(execution_ledger.os, "write", partial_then_fail)
    with pytest.raises(WorkflowExecutionLedgerError) as excinfo:
        append_execution_event(
            ledger,
            private_root=tmp_path,
            run_id=manifest["run_id"],
            event_type="run_accepted",
            payload=accepted_payload,
            idempotency_key="partial:accepted",
            occurred_at=NOW,
        )
    assert excinfo.value.code == "write_failed"
    assert not ledger.exists()

    monkeypatch.setattr(execution_ledger.os, "write", original_write)
    projection = append_execution_event(
        ledger,
        private_root=tmp_path,
        run_id=manifest["run_id"],
        event_type="run_accepted",
        payload=accepted_payload,
        idempotency_key="partial:accepted",
        occurred_at=NOW,
    )
    assert projection["status"] == "accepted"
