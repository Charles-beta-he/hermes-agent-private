from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import importlib.resources
import json
from pathlib import Path

import pytest

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
    package_schema = importlib.resources.files("workflow_kernel.schemas").joinpath(schema_name)

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
                "output_schema": {"type": "object", "required": ["source"], "additionalProperties": False, "properties": {"source": {"type": "string"}}},
                "reserve": _reservation(),
            },
            "implement": {
                "adapter": "local.echo",
                "isolation": "in_process_trusted_pure",
                "input": implement_value,
                "output_schema": {} if null_output else {"type": "object", "required": ["ok"], "additionalProperties": False, "properties": {"ok": {"const": True}}},
                "reserve": _reservation(),
            },
            "verify": {
                "adapter": "local.verify_dependency",
                "isolation": "in_process_trusted_pure",
                "input": {"dependency": "implement"},
                "output_schema": {"type": "object", "required": ["verified", "dependency"], "additionalProperties": False, "properties": {"verified": {"const": True}, "dependency": {"const": "implement"}}},
                "reserve": _reservation(),
            },
        },
    }


def _run(tmp_path: Path, *, definition: dict | None = None, manifest: dict | None = None, resume: bool = False, now: str = NOW) -> dict:
    return run_workflow(
        definition or _definition(),
        manifest or _manifest(),
        ledger_path=tmp_path / "run.jsonl",
        private_root=tmp_path,
        execute_grant=ExecuteGrant.local_only(),
        resume=resume,
        now=now,
    )


def test_preview_is_read_only_and_runtime_requires_fresh_execute_grant(tmp_path: Path) -> None:
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


def test_missing_status_is_strictly_read_only(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    before_mode = tmp_path.stat().st_mode & 0o777
    before_entries = sorted(path.name for path in tmp_path.iterdir())

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(tmp_path / "run.jsonl", private_root=tmp_path)

    assert excinfo.value.code == "run_missing"
    assert (tmp_path.stat().st_mode & 0o777) == before_mode
    assert sorted(path.name for path in tmp_path.iterdir()) == before_entries


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


def test_local_dag_executes_with_structured_packets_and_status_projects_ledger(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result["status"] == "run_completed"
    assert result["verification_state"] == "verified"
    assert result["convergence_state"] == "not_converged"
    assert [packet["leaf_id"] for packet in result["leaf_packets"]] == ["inspect", "implement", "verify"]
    assert all(packet["status"] == "completed" for packet in result["leaf_packets"])
    assert result["budget"]["settled"]["spawns"] == 3
    assert result["budget"]["reserved"]["spawns"] == 0

    status = read_runtime_status(tmp_path / "run.jsonl", private_root=tmp_path)
    assert status == result


def test_completed_null_is_distinct_from_missing_or_failed_result(tmp_path: Path) -> None:
    result = _run(tmp_path, definition=_definition(null_output=True), manifest=_manifest(null_output=True))
    packet = next(packet for packet in result["leaf_packets"] if packet["leaf_id"] == "implement")

    assert packet["status"] == "completed"
    assert packet["value_present"] is True
    assert packet["result"] is None


def test_output_schema_failure_is_unverified_not_completed(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["implement"]["output_schema"] = {"type": "string"}

    result = _run(tmp_path, manifest=manifest)
    packet = next(packet for packet in result["leaf_packets"] if packet["leaf_id"] == "implement")

    assert packet["status"] == "unverified"
    assert packet["value_present"] is True
    assert packet["uncertainty"][0]["code"] == "output_schema_invalid"
    assert result["status"] == "run_completed"
    assert result["verification_state"] == "unverified"
    verify = next(packet for packet in result["leaf_packets"] if packet["leaf_id"] == "verify")
    assert verify["status"] == "blocked"


def test_unsupported_isolation_fails_before_ledger_or_reservation(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["inspect"]["isolation"] = "container"

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=manifest)
    assert excinfo.value.code == "isolation_unsupported"
    assert not (tmp_path / "run.jsonl").exists()


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


def test_hard_budget_blocks_new_leaf_and_preserves_partial_receipts(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["hard_budget"]["spawns"] = 1

    result = _run(tmp_path, manifest=manifest)

    assert result["budget"]["settled"]["spawns"] == 1
    inspect = next(packet for packet in result["leaf_packets"] if packet["leaf_id"] == "inspect")
    implement = next(packet for packet in result["leaf_packets"] if packet["leaf_id"] == "implement")
    assert inspect["status"] == "completed"
    assert implement["status"] == "blocked"
    assert implement["uncertainty"][0]["code"] == "hard_budget_exhausted"


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
def test_resume_rejects_each_identity_dimension_drift(tmp_path: Path, path: tuple[str, ...], value: object) -> None:
    _run(tmp_path)
    changed = deepcopy(_manifest())
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, manifest=changed, resume=True, now=LATER)
    assert excinfo.value.code == "resume_identity_mismatch"


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


def test_torn_ledger_tail_fails_closed_without_rewriting_history(tmp_path: Path) -> None:
    _run(tmp_path)
    ledger = tmp_path / "run.jsonl"
    original = ledger.read_bytes()
    ledger.write_bytes(original + b'{"truncated":')

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(ledger, private_root=tmp_path)
    assert excinfo.value.code == "torn_tail"
    assert ledger.read_bytes() == original + b'{"truncated":'


def _seed_reserved_start(tmp_path: Path, *, expires_at: str, hard_spawns: int = 3) -> None:
    manifest = _manifest()
    manifest["hard_budget"]["spawns"] = hard_spawns
    preview = compile_runtime_preview(_definition(), manifest)
    ledger = tmp_path / "run.jsonl"
    common = {"ledger_path": ledger, "private_root": tmp_path, "run_id": manifest["run_id"], "occurred_at": NOW}
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
        },
        idempotency_key="seed:accepted",
    )
    append_execution_event(**common, event_type="run_started", payload={}, idempotency_key="seed:started")
    append_execution_event(
        **common,
        event_type="budget_reserved",
        payload={"reservation_id": "seed:reservation", "task_id": "inspect", "amounts": _reservation(), "expires_at": expires_at},
        idempotency_key="seed:reserve",
    )
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
        },
        idempotency_key="seed:accepted-only",
        occurred_at=NOW,
    )

    result = _run(tmp_path, resume=True, now=LATER)

    assert result["status"] == "run_completed"
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    assert [event["event_type"] for event in events[:2]] == ["run_accepted", "run_started"]


def test_resume_fails_closed_on_active_reservation_then_recovers_expired_start(tmp_path: Path) -> None:
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z")

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        _run(tmp_path, resume=True, now=NOW)
    assert excinfo.value.code == "reservation_active"

    result = _run(tmp_path, resume=True, now=LATER)
    assert result["status"] == "run_completed"
    assert all(packet["status"] == "completed" for packet in result["leaf_packets"])
    events = read_execution_events(tmp_path / "run.jsonl", private_root=tmp_path)
    assert any(
        event["event_type"] == "budget_released"
        and event["payload"]["reason"] == "expired_on_resume"
        for event in events
    )


def test_concurrent_budget_reservations_are_serialized_atomically(tmp_path: Path) -> None:
    _seed_reserved_start(tmp_path, expires_at="2026-07-23T05:01:00Z", hard_spawns=1)
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
                payload={"reservation_id": f"race:{task_id}", "task_id": task_id, "amounts": _reservation(), "expires_at": "2026-07-23T05:01:00Z"},
                idempotency_key=f"race:{task_id}",
                occurred_at=NOW,
            )
            return "admitted"
        except WorkflowExecutionLedgerError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(reserve, ["inspect", "implement"]))
    assert outcomes == ["admitted", "hard_budget_exhausted"]
    projection = project_execution_events(read_execution_events(ledger, private_root=tmp_path))
    assert len(projection["reservations"]) == 1


def test_cancellation_stops_admitting_new_leaves_and_preserves_receipts(tmp_path: Path) -> None:
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
    assert result["verification_state"] == "unverified"


def test_missing_manifest_schema_fails_closed_before_ledger(monkeypatch, tmp_path: Path) -> None:
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


def test_remote_output_schema_reference_is_rejected_before_execution(tmp_path: Path) -> None:
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
    raw[1] = json.dumps(second, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tampered = "\n".join(raw) + "\n"
    ledger.write_text(tampered, encoding="utf-8")

    with pytest.raises(WorkflowRuntimeError) as excinfo:
        read_runtime_status(ledger, private_root=tmp_path)
    assert excinfo.value.code == "broken_link"
    assert ledger.read_text(encoding="utf-8") == tampered


def test_adapter_failure_is_distinct_from_null_blocked_and_unverified(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["leaves"]["verify"]["input"] = {"dependency": "inspect"}

    result = _run(tmp_path, manifest=manifest)
    packet = next(packet for packet in result["leaf_packets"] if packet["leaf_id"] == "verify")

    assert packet["status"] == "failed"
    assert packet["value_present"] is False
    assert packet["result"] is None
    assert packet["uncertainty"][0]["code"] == "adapter_failed"
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
