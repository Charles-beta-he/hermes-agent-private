# Modular Workflow Kernel Phase 48 ADR: External Dispatch Policy Gate Local Outbox

## Status

Accepted — after full authorization, Phase48 selects the default safe track `P47A_LOCAL_OUTBOX_ONLY` because no exact external adapter target and payload were provided.

## Decision

Add `scripts/workflow_external_dispatch_policy_gate.py` to consume the Phase47 candidate packet and write:

- one local outbox item;
- one local receipt;
- a Phase48 policy-gate report;
- no external side effects.

## Evidence

```text
status=EXTERNAL_DISPATCH_POLICY_GATE_LOCAL_OUTBOX_RECORDED
selected_track=P47A_LOCAL_OUTBOX_ONLY
selected_adapter=local_outbox_simulator
external_side_effects_executed=false
unattended_external_dispatch_enabled=false
```

## Rationale

Broad authorization allows progression to the next safe layer, but it does not create an exact Telegram/Lark/GitHub target or payload. The local outbox gate is the reversible, auditable step that preserves the full-landing path without fabricating external intent.

## Boundary

Guarded external send remains closed until exact adapter, target, payload, mode, idempotency key, and rollback/correction path are selected.
