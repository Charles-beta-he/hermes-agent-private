# Modular Workflow Kernel Phase 59 ADR: Guarded Manual-send Runner

## Status

Accepted — Phase59 implements a guarded manual-send runner with preview-by-default behavior.

## Decision

Add:

```text
scripts/workflow_guarded_manual_send_runner.py
tests/scripts/test_workflow_guarded_manual_send_runner.py
```

Default invocation writes a Phase59 preview and performs no external side effect. The runner requires `--execute` plus valid Phase58 activation packet, exact target, exact payload, operator reason, policy digest, and idempotency key validation before any send.

## Evidence

```text
status=GUARDED_MANUAL_SEND_RUNNER_READY
external_side_effects_executed=false
target=telegram
```

## Boundary

The runner is not unattended. It does not enable repeated dispatch; every future send must pass exact manual gate validation.
