# Modular Workflow Kernel Phase 15 ADR: External Side-effect Human Gate

## Status

Accepted — human gate packet and fail-closed apply scaffold only.

## Context

Phase 14 proved adapter write-through as a digest-bound local taskpool outbox simulation. The next boundary is external side effects: applying an adapter write to a third-party system or sending a notification.

That boundary requires a concrete target adapter, blast radius, rollback, and post-apply verification. The user authorized continued progress, but no specific external target was named. Therefore the correct full-landing move is to create the governed human gate packet and task annotation, not to invent or mutate an external target.

## Decision

Add a runtime external side-effect gate:

```text
Adapter Write-through Local Outbox
  → External Side-effect Human Gate Packet
  → blocked_pending_external_target_authorization
  → STOP before external adapter apply / notification / third-party mutation
```

The packet includes:

- decision needed;
- confirmed facts;
- unconfirmed facts;
- options A/B/C;
- recommended default;
- blast radius;
- rollback plan;
- post-action verification.

The selected task is annotated with:

```text
workflow_external_side_effect_gate_status = blocked_pending_external_target_authorization
external_side_effects_enabled = false
```

## Explicit non-goals

This phase does not:

- apply any external adapter;
- send a notification;
- mutate Lark/Jira/GitHub/customer systems;
- infer an external target from local taskpool state;
- enable external_side_effects.

## Artifacts

- Schema: `schemas/runtime_external_side_effect_gate_packet.schema.json`
- Script: `scripts/workflow_runtime_external_side_effect_gate.py`
- Tests: `tests/scripts/test_workflow_runtime_external_side_effect_gate.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-external-side-effect-gates/*`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_external_side_effect_gate.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_external_side_effect_gate.py
```

Expected invariants:

```text
workflow_external_side_effect_gate_status = blocked_pending_external_target_authorization
external_side_effects_enabled = false
readback_verified = true
```

## Next authority boundary

External apply requires a later explicit authorization with:

```text
target adapter
operation
blast radius
rollback path
post-apply verification
```
