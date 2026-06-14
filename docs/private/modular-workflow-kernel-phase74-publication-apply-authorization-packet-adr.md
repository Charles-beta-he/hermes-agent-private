# Modular Workflow Kernel Phase 74 ADR: Publication Apply Authorization Packet

## Status

Accepted — Phase74 creates a fail-closed authorization packet for any future publication apply.

## Decision

Add:

```text
scripts/workflow_publication_apply_authorization_packet.py
tests/scripts/test_workflow_publication_apply_authorization_packet.py
```

The packet offers safe default `A_KEEP_PRIVATE_NO_PUBLICATION` and keeps `selected_option=null`.

## Evidence

```text
status=PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY
recommended_default=A_KEEP_PRIVATE_NO_PUBLICATION
packet_only=true
selected_option=null
publication_apply_enabled=false
external_publication_enabled=false
```

## Boundary

This is not an apply phase. It does not publish, send, mutate cron, or access credentials.
