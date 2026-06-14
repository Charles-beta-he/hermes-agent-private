# Modular Workflow Kernel Phase 66 ADR: Future Unattended Reopen Packet

## Status

Accepted — Phase66 creates a packet-only reopen design for possible future unattended apply work.

## Decision

Add:

```text
scripts/workflow_future_unattended_reopen_packet.py
tests/scripts/test_workflow_future_unattended_reopen_packet.py
```

The packet defines future options and apply requirements but keeps `reopen_apply_enabled=false` and `unattended_dispatch_enabled=false`.

## Evidence

```text
status=FUTURE_UNATTENDED_REOPEN_PACKET_READY
recommended_default=A_NO_REOPEN_KEEP_MANUAL_ONLY
selected_option=null
packet_only=true
reopen_apply_enabled=false
unattended_dispatch_enabled=false
external_side_effects_executed=false
```

## Boundary

Future divergence should be handled only at a single implementation point with competing schemes. Sequential phases are not treated as divergence by themselves.
