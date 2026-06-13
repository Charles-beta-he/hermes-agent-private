# Modular Workflow Kernel Phase 11 ADR: Runtime Module Execution Boundary

## Status

Accepted — local deterministic module execution only.

## Context

Phase 10 proved that a workflow pack candidate can move through taskpool import, runtime integration, claim execution, and runtime worker start until the selected task is `running` with `workflow_runtime_execution_status=running_noop_started_not_reviewed`.

That still did not prove a real execution-plane module can run. The next safe boundary is not `done`, model calls, adapter write-through, or external notification. It is a narrow local deterministic module that produces reviewable evidence from the running task.

## Decision

Add a runtime module execution layer:

```text
Runtime Execution Worker
  → Runtime Module Executor
  → review
  → STOP before done / model call / adapter write-through / external side effect
```

The executor may run exactly one selected running task when all of these are true:

- runtime packet id matches the task;
- claim execution id matches the task;
- runtime execution id matches the task;
- selected group id is unique;
- task status is `running`;
- `workflow_runtime_execution_status=running_noop_started_not_reviewed`;
- `claimed_by=workflow_runtime_claim_executor`;
- `requires_human_gate=false`;
- model, adapter, and external side-effect flags are false.

The first real module is deterministic and local:

```text
contract_acceptance_governance.local_validator.v1
```

It runs the workflow contract validator and requires PASS before moving the task to `review`.

On write, the executor changes only the selected taskpool note:

```text
status: review
workflow_runtime_execution_status: module_execution_review_ready
workflow_module_execution_status: module_execution_review_ready
runtime_module_enabled: true
model_call_enabled: false
adapter_write_enabled: false
external_side_effects_enabled: false
```

It writes a local runtime module execution packet and refreshes derived taskpool views for readback.

## Explicit non-goals

This phase does not:

- mark the task `done`;
- call any model;
- execute arbitrary shell from the task body;
- write adapters;
- send notifications;
- mutate external systems;
- run human-gated groups such as `safety_flags`.

## Artifacts

- Schema: `schemas/runtime_module_execution_packet.schema.json`
- Script: `scripts/workflow_runtime_module_executor.py`
- Tests: `tests/scripts/test_workflow_runtime_module_executor.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-module-executions/*.runtime_module_execution_packet.json`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_module_executor.py --json
python3 scripts/workflow_runtime_module_executor.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_module_executor.py
```

Expected invariants after write:

```text
selected task status = review
workflow_module_execution_status = module_execution_review_ready
model_call_enabled = false
adapter_write_enabled = false
external_side_effects_enabled = false
no done transition
```

## Rollback

After explicit rollback authorization, transition the selected task from `review` back to `running` and remove:

```text
workflow_module_execution_id
workflow_module_execution_status
workflow_module_execution_record
```

No model, adapter, or external state needs rollback because this phase does not touch them.
