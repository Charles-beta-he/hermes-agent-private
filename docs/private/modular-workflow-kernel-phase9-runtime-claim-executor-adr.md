# Modular Workflow Kernel Phase 9 ADR: Runtime Claim Executor Boundary

## Status

Accepted — local taskpool claim boundary only.

## Context

Phase 8 recorded runtime integration packets for the consolidated workflow-kernel tasks and prepared claim packets while keeping every task in `status: proposed`. The next stable boundary is not worker execution or adapter write-through; it is the narrow point where one prepared claim packet can be consumed by a governed local executor.

## Decision

Add a runtime claim executor layer:

```text
Runtime Integration Record-only
  → Runtime Claim Executor
  → STOP before running / review / done / model call / adapter write-through / external side effect
```

The executor may consume exactly one selected prepared claim packet when all of these are true:

- runtime packet id matches the target task;
- selected group id is unique;
- task status is `proposed`;
- `workflow_claim_packet_status=prepared_not_claimed`;
- `requires_human_gate=false`;
- model, adapter, and external side-effect flags are false.

On write, the executor changes only the selected taskpool note:

```text
status: claimed
workflow_claim_packet_status: consumed_claimed_not_running
claimed_by: workflow_runtime_claim_executor
taskpool_claim_enabled: true
runtime_enabled: true
runtime_record_only: false
model_call_enabled: false
adapter_write_enabled: false
external_side_effects_enabled: false
```

It writes a local packet artifact and refreshes derived taskpool views for readback.

## Explicit non-goals

This phase does not:

- start a worker;
- transition the task to `running`, `review`, or `done`;
- call any model;
- write an adapter;
- send notifications;
- mutate external systems;
- claim human-gated groups such as `safety_flags`.

## Artifacts

- Schema: `schemas/runtime_claim_execution_packet.schema.json`
- Script: `scripts/workflow_runtime_claim_executor.py`
- Tests: `tests/scripts/test_workflow_runtime_claim_executor.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-claims/*.runtime_claim_execution_packet.json`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_claim_executor.py --json
python3 scripts/workflow_runtime_claim_executor.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_claim_executor.py
```

Expected invariants after write:

```text
selected task status = claimed
claim packet status = consumed_claimed_not_running
claimed_by = workflow_runtime_claim_executor
model_call_enabled = false
adapter_write_enabled = false
external_side_effects_enabled = false
no running/review/done transition
```

## Rollback

After explicit rollback authorization, transition the selected task from `claimed` back to `proposed` and remove:

```text
workflow_claim_execution_id
workflow_claim_state
workflow_claim_record
claimed_by
claim_expires_at
```

No model, adapter, or external state needs rollback because this phase does not touch them.
