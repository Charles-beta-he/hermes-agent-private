# Modular Workflow Kernel Phase 14 ADR: Runtime Adapter Write-through Local Outbox

## Status

Accepted — digest-bound local adapter write-through simulation only.

## Context

Phase 13 proved that a completed workflow runtime task can invoke a pinned/read-only model route and persist prompt/response/model-call evidence while keeping external side effects closed.

The next boundary is adapter write-through. Direct mutation of external adapters remains a high-authority boundary, so Phase 14 opens the smallest safe slice: a local taskpool adapter intent and outbox artifact with digest verification. This proves model output can become a governed write intent without mutating third-party systems.

## Decision

Add a runtime adapter write gate:

```text
Model Call Gate
  → Adapter Write-through Local Outbox
  → digest-bound write intent + local outbox artifact + task annotation
  → STOP before external adapter apply / notification / third-party mutation
```

The gate may run only when all of these are true:

- runtime packet id matches the selected task;
- claim execution id matches the selected task;
- runtime execution id matches the selected task;
- module execution id matches the selected task;
- review done execution id matches the selected task;
- model call id matches the selected task;
- task status is `done`;
- `workflow_model_call_status=model_call_completed_read_only`;
- `requires_human_gate=false`;
- `external_side_effects_enabled=false`.

The gate writes a local outbox artifact:

```text
adapter_target = taskpool_outbox
adapter_mode = local_outbox_simulation
operation = annotate_model_call_ready_for_adapter_review
```

It also annotates the selected task with:

```text
workflow_adapter_write_status = adapter_write_local_outbox_simulated
adapter_write_enabled = true
external_side_effects_enabled = false
```

## Explicit non-goals

This phase does not:

- call third-party APIs;
- send notifications;
- mutate Lark/Jira/GitHub/customer systems;
- apply external adapter writes;
- broaden adapter authority beyond the one selected task;
- run human-gated groups.

## Artifacts

- Schema: `schemas/runtime_adapter_write_packet.schema.json`
- Script: `scripts/workflow_runtime_adapter_write_gate.py`
- Tests: `tests/scripts/test_workflow_runtime_adapter_write_gate.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-adapter-writes/*`

Each adapter packet contains:

- write intent;
- intent digest;
- local outbox artifact digest;
- readback digest verification;
- explicit `external_side_effects_enabled=false`.

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_adapter_write_gate.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_adapter_write_gate.py
```

Expected invariants after write:

```text
workflow_adapter_write_status = adapter_write_local_outbox_simulated
adapter_write_enabled = true
external_side_effects_enabled = false
outbox_artifact.readback_digest_match = true
```

## Rollback

After explicit rollback authorization, remove:

```text
workflow_adapter_write_id
workflow_adapter_write_status
workflow_adapter_write_record
```

and remove matching evidence markers. Optionally delete the local outbox artifact. No external state needs rollback because this phase does not touch external systems.
