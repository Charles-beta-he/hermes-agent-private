from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from workflow_kernel.workflow_compensation import (
    WorkflowCompensationError,
    prepare_workflow_compensation_v1,
)
from workflow_kernel.workflow_execution_event_ledger import (
    WorkflowExecutionLedgerSink,
    append_workflow_execution_event,
    create_workflow_execution_event_v1,
    derive_execution_ids_v1,
)
from workflow_kernel.workflow_graph_runtime import (
    LocalExecutionGrant,
    RuntimeExecutionSession,
    RuntimeTaskBinding,
    derive_runtime_apply_approval_digest_v1,
    derive_runtime_binding_digest_v1,
    execute_workflow_plan_v1,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _source_evidence(
    tmp_path: Path,
    *,
    effect_status: str = "observed",
    ledger_name: str = "source.jsonl",
) -> tuple[dict[str, Any], Path, str]:
    identity = {
        "session_owner": "source-worker",
        "lease_id": "source-lease",
        "lease_expires_at": "2026-07-29T00:00:00Z",
        "fencing_token": 3,
        "workflow_id": "source-workflow",
        "definition_digest": DIGEST_A,
        "plan_digest": DIGEST_B,
        "binding_digest": DIGEST_C,
        "execution_mode": "apply",
        "policy_digest": DIGEST_A,
        "approval_digest": derive_runtime_apply_approval_digest_v1(
            run_id="source-run",
            workflow_id="source-workflow",
            plan_digest=DIGEST_B,
            binding_digest=DIGEST_C,
            policy_digest=DIGEST_A,
        ),
    }
    ids = derive_execution_ids_v1(
        run_id="source-run",
        plan_digest=DIGEST_B,
        binding_digest=DIGEST_C,
        task_id="implement",
        attempt=1,
        side_effect="local_write",
    )
    bodies: list[dict[str, Any]] = []

    def append(kind: str, **values: Any) -> None:
        bodies.append(
            create_workflow_execution_event_v1(
                {
                    "schema_version": "workflow_execution_event_v1",
                    "run_id": "source-run",
                    "sequence": len(bodies) + 1,
                    "previous_event_id": bodies[-1]["event_id"] if bodies else None,
                    "idempotency_key": f"source:{len(bodies) + 1}:{kind}",
                    "occurred_at": f"2026-07-28T00:00:{len(bodies) + 1:02d}Z",
                    "identity": identity,
                    "kind": kind,
                    "task_id": values.get("task_id"),
                    "step_id": values.get("step_id"),
                    "attempt_id": values.get("attempt_id"),
                    "effect_id": values.get("effect_id"),
                    "side_effect": values.get("side_effect", "none"),
                    "evidence_digest": values.get("evidence_digest"),
                    "reason_code": values.get("reason_code"),
                }
            )
        )

    append("run_admitted")
    append("task_started", task_id="implement", side_effect="local_write", **ids)
    append("effect_intent", task_id="implement", side_effect="local_write", **ids)
    if effect_status == "observed":
        append(
            "effect_observed",
            task_id="implement",
            side_effect="local_write",
            evidence_digest=DIGEST_A,
            **ids,
        )
    append(
        "task_failed",
        task_id="implement",
        side_effect="local_write",
        reason_code="verification_failed",
        **ids,
    )
    core = {
        "schema_version": "workflow_graph_execution_receipt_v1",
        "status": "failed",
        "workflow_ref": {"workflow_id": "source-workflow", "version": 1},
        "definition_digest": DIGEST_A,
        "plan_digest": DIGEST_B,
        "binding_digest": DIGEST_C,
        "nodes": {"implement": {"status": "failed", "accepted": False, "role": "execute"}},
    }
    receipt = {"core": core, "core_digest": _digest(core)}
    append(
        "run_failed",
        reason_code="workflow_failed",
        evidence_digest=receipt["core_digest"],
    )
    ledger = tmp_path / ledger_name
    for event in bodies:
        append_workflow_execution_event(ledger, event, private_root=tmp_path)
    return receipt, ledger, ids["effect_id"]  # type: ignore[return-value]


def _package(tmp_path: Path, *, ledger_name: str = "source.jsonl") -> dict[str, Any]:
    receipt, ledger, effect_id = _source_evidence(tmp_path, ledger_name=ledger_name)
    return prepare_workflow_compensation_v1(
        receipt,
        ledger,
        private_root=tmp_path,
        effect_id=effect_id,
        compensation_kind="restore-local-state",
        capabilities=["read_file", "write_file"],
        writes=["artifact"],
    )


def _bindings() -> list[RuntimeTaskBinding]:
    return [
        RuntimeTaskBinding(task_id="compensate", runner_key="compensate"),
        RuntimeTaskBinding(task_id="verify-compensation", runner_key="verify-compensation"),
    ]


def _session(
    package: dict[str, Any],
    bindings: list[RuntimeTaskBinding],
    *,
    run_id: str,
    mode: str,
    approval_digest: str | None,
) -> RuntimeExecutionSession:
    plan = package["plan"]
    return RuntimeExecutionSession(
        run_id=run_id,
        session_owner="compensation-worker",
        lease_id=f"lease-{run_id}",
        lease_expires_at="2026-07-29T00:00:00Z",
        fencing_token=8,
        workflow_id=package["definition"]["workflow_id"],
        definition_digest=plan["definition_digest"],
        plan_digest=plan["plan_digest"],
        binding_digest=derive_runtime_binding_digest_v1(bindings),
        execution_mode=mode,
        policy_digest=package["policy_digest"],
        approval_digest=approval_digest,
    )


def _grant(package: dict[str, Any], grant_id: str) -> LocalExecutionGrant:
    plan = package["plan"]
    return LocalExecutionGrant(
        grant_id=grant_id,
        plan_digest=plan["plan_digest"],
        definition_digest=plan["definition_digest"],
        allowed_capabilities=frozenset({"read_file", "write_file"}),
        max_side_effect="local_write",
        max_workers=1,
    )


def test_preparation_is_deterministic_authority_neutral_and_source_bound(
    tmp_path: Path,
) -> None:
    first = _package(tmp_path, ledger_name="source-first.jsonl")
    second = _package(tmp_path, ledger_name="source-second.jsonl")

    assert first == second
    assert first["authority_granted"] is False
    assert first["executable"] is False
    assert first["source_run_id"] == "source-run"
    assert first["source_effect_id"].startswith("wef_")
    assert first["source_terminal_event_id"].startswith("wee_")
    assert first["policy_digest"].startswith("sha256:")
    assert first["plan"]["authority_granted"] is False
    assert first["plan"]["executable"] is False


def test_preparation_rejects_behavioral_mapping_subclasses(tmp_path: Path) -> None:
    receipt, ledger, effect_id = _source_evidence(tmp_path)

    class HostileCore(dict[str, Any]):
        def get(self, key: str, default: Any = None) -> Any:
            if key == "status":
                return "failed"
            if key == "nodes":
                return {"implement": {"status": "failed"}}
            return super().get(key, default)

    honest_core = copy.deepcopy(receipt["core"])
    honest_core["status"] = "success"
    honest_core["nodes"] = {}
    receipt["core"] = HostileCore(honest_core)
    receipt["core_digest"] = _digest(honest_core)

    with pytest.raises(WorkflowCompensationError) as exc:
        prepare_workflow_compensation_v1(
            receipt,
            ledger,
            private_root=tmp_path,
            effect_id=effect_id,
            compensation_kind="restore-local-state",
            capabilities=["read_file", "write_file"],
            writes=["artifact"],
        )

    assert exc.value.code == "compensation_input_invalid"


def test_preparation_rejects_effect_absent_from_durable_ledger(tmp_path: Path) -> None:
    receipt, ledger, _effect_id = _source_evidence(tmp_path)

    with pytest.raises(WorkflowCompensationError) as exc:
        prepare_workflow_compensation_v1(
            receipt,
            ledger,
            private_root=tmp_path,
            effect_id="wef_" + "0" * 64,
            compensation_kind="restore-local-state",
            capabilities=["read_file", "write_file"],
            writes=["artifact"],
        )

    assert exc.value.code == "compensation_effect_missing"


def test_preparation_rejects_receipt_not_bound_to_terminal_event(tmp_path: Path) -> None:
    receipt, ledger, effect_id = _source_evidence(tmp_path)
    receipt["core"]["nodes"]["implement"]["accepted"] = True
    receipt["core_digest"] = _digest(receipt["core"])

    with pytest.raises(WorkflowCompensationError) as exc:
        prepare_workflow_compensation_v1(
            receipt,
            ledger,
            private_root=tmp_path,
            effect_id=effect_id,
            compensation_kind="restore-local-state",
            capabilities=["read_file", "write_file"],
            writes=["artifact"],
        )

    assert exc.value.code == "compensation_source_terminal_mismatch"


def test_preparation_rejects_uncertain_effect(tmp_path: Path) -> None:
    receipt, ledger, effect_id = _source_evidence(tmp_path, effect_status="uncertain")

    with pytest.raises(WorkflowCompensationError) as exc:
        prepare_workflow_compensation_v1(
            receipt,
            ledger,
            private_root=tmp_path,
            effect_id=effect_id,
            compensation_kind="restore-local-state",
            capabilities=["read_file", "write_file"],
            writes=["artifact"],
        )

    assert exc.value.code == "compensation_effect_uncertain"


def test_compensation_dry_run_does_not_invoke_compensation_runner(tmp_path: Path) -> None:
    package = _package(tmp_path)
    bindings = _bindings()
    calls: list[str] = []
    receipt = execute_workflow_plan_v1(
        package["plan"],
        package["definition"],
        bindings,
        {
            "compensate": lambda _ctx: calls.append("compensate"),
            "verify-compensation": lambda _ctx: calls.append("verify") or True,
        },
        grant=_grant(package, "comp-dry-grant"),
        execution_session=_session(
            package,
            bindings,
            run_id="comp-dry-run",
            mode="dry_run",
            approval_digest=None,
        ),
        event_sink=WorkflowExecutionLedgerSink(
            tmp_path / "comp-dry.jsonl", private_root=tmp_path
        ),
        execution_clock=lambda: "2026-07-28T00:30:00Z",
    )

    assert calls == []
    assert receipt["core"]["status"] == "dry_run"


def test_compensation_apply_requires_bound_approval_and_fresh_grant(tmp_path: Path) -> None:
    package = _package(tmp_path)
    bindings = _bindings()
    run_id = "comp-apply-run"
    binding_digest = derive_runtime_binding_digest_v1(bindings)
    approval = derive_runtime_apply_approval_digest_v1(
        run_id=run_id,
        workflow_id=package["definition"]["workflow_id"],
        plan_digest=package["plan"]["plan_digest"],
        binding_digest=binding_digest,
        policy_digest=package["policy_digest"],
    )
    calls: list[str] = []
    receipt = execute_workflow_plan_v1(
        package["plan"],
        package["definition"],
        bindings,
        {
            "compensate": lambda ctx: calls.append(ctx.task_id) or {"restored": True},
            "verify-compensation": lambda ctx: calls.append(ctx.task_id) or True,
        },
        grant=_grant(package, "comp-apply-grant"),
        execution_session=_session(
            package,
            bindings,
            run_id=run_id,
            mode="apply",
            approval_digest=approval,
        ),
        event_sink=WorkflowExecutionLedgerSink(
            tmp_path / "comp-apply.jsonl", private_root=tmp_path
        ),
        execution_clock=lambda: "2026-07-28T00:30:00Z",
    )

    assert calls == ["compensate", "verify-compensation"]
    assert receipt["core"]["status"] == "success"
    assert receipt["core"]["policy_digest"] == package["policy_digest"]
    assert receipt["core"]["approval_digest"] == approval
