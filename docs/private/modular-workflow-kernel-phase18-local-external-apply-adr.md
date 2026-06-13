# Modular Workflow Kernel Phase 18 ADR: Local External Apply Recorded

## Status

Accepted — the `taskpool_outbox_simulator` target is applied as a local real-apply simulator record. External dispatch remains blocked.

## Context

Phase 17 fully enumerated external apply requirements and target options. The user then authorized full advancement. Under the authority model, that does not authorize Lark/GitHub/Telegram external side effects without a concrete target and operation scope. The safe forward path is the local simulator target because it is local, reversible by evidence, and does not send or mutate external systems.

## Decision

Implement `scripts/workflow_runtime_external_apply_local.py` for exactly one supported target:

```text
taskpool_outbox_simulator
```

The executor writes:

- a local outbox simulator item;
- a runtime external apply receipt;
- rollback evidence with `not_needed_no_external_mutation`;
- taskpool readback markers.

It refuses:

- non-local targets;
- tasks with `external_side_effects_enabled` true-ish;
- missing/mismatched contract id;
- non-local adapter records.

## Invariants

```text
apply_status = local_outbox_simulator_apply_recorded
apply_mode = local_real_apply_no_external_dispatch
external_side_effects_enabled = false
readback_verified = true
```

## Artifacts

- Script: `scripts/workflow_runtime_external_apply_local.py`
- Tests: `tests/scripts/test_workflow_runtime_external_apply_local.py`
- Registry update: `config/target_adapter_capability_registry.json`
- Outbox evidence: `docs/private/workflow-kernel-runtime-external-apply-outbox/*`

## Verification

Run:

```bash
python3 scripts/workflow_runtime_external_apply_local.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_external_apply_local.py \
  tests/scripts/test_workflow_runtime_external_apply_contract.py \
  tests/scripts/test_workflow_runtime_external_apply_scaffold.py \
  tests/scripts/test_workflow_contract_validator.py
```

Expected:

```text
21 tests passed
receipt schema validation PASS
rollback schema validation PASS
brain_task_orchestrator.py selfcheck PASS
brain_task_orchestrator.py proof-check PASS
```

## Next boundary

The next layer is **external dispatch adapter implementation** for one concrete target such as Lark, GitHub issue, or Telegram notification. That remains a separate external-side-effect gate requiring exact target, operation, rollback, and post-apply readback.
