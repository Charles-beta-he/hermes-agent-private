# Modular Workflow Kernel Phase 62 ADR: Unattended Dispatch Enablement Packet

## Status

Accepted — Phase62 creates a fail-closed enablement packet for possible future repeated/unattended dispatch.

## Decision

Add:

```text
scripts/workflow_unattended_dispatch_enablement_packet.py
tests/scripts/test_workflow_unattended_dispatch_enablement_packet.py
```

The packet enumerates options:

```text
A_KEEP_MANUAL_ONLY
B_APPROVE_DRY_RUN_ONLY_AUTOMATION
C_APPROVE_ONE_TARGET_LIMITED_UNATTENDED_SENDS
```

Default is `A_KEEP_MANUAL_ONLY`. The decision schema is record-only and must not enable unattended dispatch by itself.

## Evidence

```text
status=UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY
recommended_default=A_KEEP_MANUAL_ONLY
unattended_dispatch_enabled=false
external_side_effects_executed=false
cron_state_mutated=false
```

## Boundary

This phase does not enable repeated sends, does not mutate scheduler/apply state, and does not send any message. Future enablement requires a concrete option decision and a separate apply phase.
