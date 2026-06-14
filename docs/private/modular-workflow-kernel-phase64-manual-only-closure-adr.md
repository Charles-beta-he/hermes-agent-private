# Modular Workflow Kernel Phase 64 ADR: Safe-default Manual-only Closure

## Status

Accepted — Phase64 closes the Workflow Kernel external-dispatch ladder on the safe manual-only default.

## Decision

Add:

```text
scripts/workflow_manual_only_closure.py
tests/scripts/test_workflow_manual_only_closure.py
```

The closure keeps the Phase59 guarded manual-send runner preview path and the Phase57 no-send dry-run cron receipts, while closing repeated/unattended external dispatch unless a future explicit reopen/apply phase is authorized.

## Evidence

```text
status=MANUAL_ONLY_CLOSURE_READY
final_mode=manual_only_with_no_send_dry_run_cron
unattended_dispatch_enabled=false
external_side_effects_executed=false
```

## Closed paths without new authorization

- repeated external sends
- unattended external dispatch
- cron-triggered external dispatch
- automatic compensation message
- cross-adapter external dispatch
