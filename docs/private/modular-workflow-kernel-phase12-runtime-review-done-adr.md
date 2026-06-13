# Modular Workflow Kernel Phase 12 ADR: Runtime Review Done Gate

## Status

Accepted — local review/done closeout only.

## Context

Phase 11 proved that a running workflow-kernel task can execute a bounded local deterministic module and advance to `review` with `workflow_module_execution_status=module_execution_review_ready`.

The next safe boundary is to close the local lifecycle loop for exactly that task. This proves the control plane can finish a locally verified runtime task without granting model-call, adapter-write, notification, or external-system authority.

## Decision

Add a runtime review/done gate:

```text
Runtime Module Executor
  → Review / Done Gate
  → done
  → STOP before model call / adapter write-through / external side effect
```

The gate may close exactly one selected task when all of these are true:

- runtime packet id matches the task;
- claim execution id matches the task;
- runtime execution id matches the task;
- module execution id matches the task;
- selected group id is unique;
- task status is `review`;
- `workflow_module_execution_status=module_execution_review_ready`;
- `requires_human_gate=false`;
- model, adapter, and external side-effect flags are false;
- closeout verification passes.

The closeout verification is deterministic and local:

```text
workflow_contract_validator
```

On write, the gate changes only the selected taskpool note:

```text
status: done
workflow_review_done_status: done_local_review_closed
review_done_enabled: true
model_call_enabled: false
adapter_write_enabled: false
external_side_effects_enabled: false
```

It writes a local runtime review done packet and refreshes derived taskpool views for readback.

## Explicit non-goals

This phase does not:

- call any model;
- write adapters;
- send notifications;
- mutate external systems;
- run human-gated groups such as `safety_flags`;
- grant broad lifecycle mutation authority.

## Artifacts

- Schema: `schemas/runtime_review_done_packet.schema.json`
- Script: `scripts/workflow_runtime_review_done_gate.py`
- Tests: `tests/scripts/test_workflow_runtime_review_done_gate.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-review-done/*.runtime_review_done_packet.json`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_review_done_gate.py --json
python3 scripts/workflow_runtime_review_done_gate.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_review_done_gate.py
```

Expected invariants after write:

```text
selected task status = done
workflow_review_done_status = done_local_review_closed
model_call_enabled = false
adapter_write_enabled = false
external_side_effects_enabled = false
```

## Rollback

After explicit rollback authorization, transition the selected task from `done` back to `review` and remove:

```text
workflow_review_done_execution_id
workflow_review_done_status
workflow_review_done_record
```

No model, adapter, or external state needs rollback because this phase does not touch them.
