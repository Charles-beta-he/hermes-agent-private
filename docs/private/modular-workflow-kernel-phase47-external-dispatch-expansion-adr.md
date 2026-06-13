# Modular Workflow Kernel Phase 47 ADR: External Dispatch Expansion Candidate Packet

## Status

Accepted — Phase47 enumerates external dispatch expansion paths after runtime auto-enable without executing external side effects.

## Decision

Add `scripts/workflow_external_dispatch_expansion_packet.py` to produce a candidate packet with:

- adapter options;
- required authority per adapter;
- required inputs and exact target/payload needs;
- blast radius;
- rollback/correction path;
- policy requirements;
- candidate tracks from local-outbox-only to unattended policy gate.

## Current Candidate Tracks

```text
P47A_LOCAL_OUTBOX_ONLY
P47B_SINGLE_TARGET_DRY_RUN_POLICY
P47C_GUARDED_SINGLE_SEND
P47D_UNATTENDED_POLICY_GATE
```

Default safe adapter:

```text
local_outbox_simulator
```

## Evidence

```text
status=EXTERNAL_DISPATCH_EXPANSION_CANDIDATE_READY
unattended_external_dispatch_enabled=false
external_side_effects_executed=false
```

## Boundary

Even under broad authorization, no Telegram/Lark/GitHub external mutation runs until an exact adapter, target, payload, mode, idempotency key, and rollback/readback path are selected.
