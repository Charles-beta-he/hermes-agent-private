# Modular Workflow Kernel Phase 10 ADR: Runtime Execution Worker Boundary

## Status

Accepted — local no-op runtime worker boundary only.

## Context

Phase 9 consumed one prepared claim packet and moved the selected consolidated workflow-kernel task from `proposed` to `claimed`. The next safe boundary is not model execution, review, done, adapter write-through, or external notification. It is a narrow local worker marker that proves the claimed task can enter the runtime lifecycle without granting model/adapter/external authority.

## Decision

Add a runtime execution worker layer:

```text
Runtime Claim Executor
  → Runtime Execution Worker
  → STOP before review / done / model call / adapter write-through / external side effect
```

The worker may start exactly one selected claimed task when all of these are true:

- runtime packet id matches the task;
- claim execution id matches the task;
- selected group id is unique;
- task status is `claimed`;
- `workflow_claim_packet_status=consumed_claimed_not_running`;
- `claimed_by=workflow_runtime_claim_executor`;
- `requires_human_gate=false`;
- model, adapter, and external side-effect flags are false.

On write, the worker changes only the selected taskpool note:

```text
status: running
workflow_runtime_execution_status: running_noop_started_not_reviewed
runtime_worker_enabled: true
model_call_enabled: false
adapter_write_enabled: false
external_side_effects_enabled: false
```

It writes a local runtime execution worker packet and refreshes derived taskpool views for readback.

## Explicit non-goals

This phase does not:

- execute workflow modules with an LLM;
- call any model;
- transition to `review` or `done`;
- write adapters;
- send notifications;
- mutate external systems;
- run human-gated groups such as `safety_flags`.

## Artifacts

- Schema: `schemas/runtime_execution_worker_packet.schema.json`
- Script: `scripts/workflow_runtime_execution_worker.py`
- Tests: `tests/scripts/test_workflow_runtime_execution_worker.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-executions/*.runtime_execution_worker_packet.json`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_execution_worker.py --json
python3 scripts/workflow_runtime_execution_worker.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_execution_worker.py
```

Expected invariants after write:

```text
selected task status = running
workflow_runtime_execution_status = running_noop_started_not_reviewed
model_call_enabled = false
adapter_write_enabled = false
external_side_effects_enabled = false
no review/done transition
```

## Rollback

After explicit rollback authorization, transition the selected task from `running` back to `claimed` and remove:

```text
workflow_runtime_execution_id
workflow_runtime_execution_status
workflow_runtime_worker_record
run_id
```

No model, adapter, or external state needs rollback because this phase does not touch them.
