# Modular Workflow Kernel Phase 58 ADR: Guarded Send Runner Activation Packet

## Status

Accepted — Phase58 creates the activation packet and contract for a future guarded manual-send runner.

## Decision

Add:

```text
scripts/workflow_guarded_send_runner_activation_packet.py
tests/scripts/test_workflow_guarded_send_runner_activation_packet.py
docs/private/workflow-kernel-guarded-send-runner-activation/
```

The contract requires exact target, exact payload, operator reason, policy digest match, idempotency key, and pre-send preview before any future send runner can execute.

## Safety

```text
manual_send_runner_is_active_now=false
unattended_dispatch_enabled=false
external_side_effects_executed=false
send_message_called=false
hermes_send_called=false
```

## Boundary

This packet prepares a future runner but does not implement or activate it. The next boundary is Phase59 guarded manual-send runner implementation versus Phase60 one-shot guarded send activation.
