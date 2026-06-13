# Modular Workflow Kernel Phase 13 ADR: Runtime Model Call Gate

## Status

Accepted — first pinned read-only model call boundary.

## Context

Phase 12 closed one local deterministic runtime task from `review` to `done` while keeping model, adapter, and external side-effect authority disabled.

The next architectural boundary is to let a workflow module invoke a model route without granting write-through authority. This proves the execution plane can cross from deterministic local modules into model-assisted modules while keeping Control Plane evidence, route trace, prompt digest, response artifact, and rollback semantics intact.

## Decision

Add a runtime model call gate:

```text
Review / Done Gate
  → Model Call Gate
  → route trace + prompt artifact + response artifact + verification
  → STOP before adapter write-through or external side effect
```

The gate may run exactly one pinned/read-only model call when all of these are true:

- runtime packet id matches the selected task;
- claim execution id matches the selected task;
- runtime execution id matches the selected task;
- module execution id matches the selected task;
- review done execution id matches the selected task;
- selected group id is unique;
- task status is `done`;
- `workflow_review_done_status=done_local_review_closed`;
- `requires_human_gate=false`;
- adapter and external side-effect flags are false.

The first call is deliberately a smoke module for `goal_calibrator.v1` using route alias `best.goal_calibration.structured`. It asks the model for a fixed marker and verifies:

```text
MODEL_CALL_GATE_OK
```

This is intentionally minimal: it proves route invocation and artifact governance before allowing richer module prompts.

## Explicit non-goals

This phase does not:

- write adapters;
- send notifications;
- mutate external systems;
- perform broad dynamic routing;
- run human-gated groups;
- transform the model output into lifecycle closure or external action.

## Artifacts

- Schema: `schemas/runtime_model_call_packet.schema.json`
- Script: `scripts/workflow_runtime_model_call_gate.py`
- Tests: `tests/scripts/test_workflow_runtime_model_call_gate.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-model-calls/*`

Each packet contains:

- `route_trace`;
- `prompt_artifact` with SHA-256 digest;
- `response_artifact` with SHA-256 digest;
- verification checks;
- explicit false `adapter_write_enabled` and `external_side_effects_enabled`.

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_model_call_gate.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_model_call_gate.py
```

Expected invariants after write:

```text
workflow_model_call_status = model_call_completed_read_only
model_call_enabled = true
adapter_write_enabled = false
external_side_effects_enabled = false
response_artifact.expected_marker_present = true
```

## Rollback

After explicit rollback authorization, remove:

```text
workflow_model_call_id
workflow_model_call_status
workflow_model_call_record
```

and remove matching evidence markers. No adapter or external state needs rollback because this phase does not touch them.
