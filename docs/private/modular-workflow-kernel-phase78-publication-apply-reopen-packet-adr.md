# Modular Workflow Kernel Phase 78 ADR: Publication Apply Reopen Packet

## Status

Accepted — Phase78 creates a packet-only future reopen design for publication apply.

## Decision

Add:

```text
scripts/workflow_publication_apply_reopen_packet.py
tests/scripts/test_workflow_publication_apply_reopen_packet.py
```

The packet recommends `A_NO_REOPEN_KEEP_PRIVATE` and keeps apply disabled.

## Evidence

```text
status=PUBLICATION_APPLY_REOPEN_PACKET_READY
recommended_default=A_NO_REOPEN_KEEP_PRIVATE
selected_option=null
packet_only=true
reopen_apply_enabled=false
publication_apply_enabled=false
```

## Boundary

This is not an apply phase. A real external publication requires exact target/payload/rollback and a future single implementation-point choice.
