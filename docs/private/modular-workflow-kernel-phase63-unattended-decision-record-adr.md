# Modular Workflow Kernel Phase 63 ADR: Explicit Unattended Decision Record

## Status

Accepted — Phase63 records the explicit `keep_manual_only` decision from the Phase62 decision schema.

## Decision

Add:

```text
scripts/workflow_unattended_decision_record.py
tests/scripts/test_workflow_unattended_decision_record.py
```

The decision record is append-only and record-only. It selects `A_KEEP_MANUAL_ONLY` and does not apply or enable any dispatch behavior.

## Evidence

```text
status=UNATTENDED_DECISION_RECORD_READY
decision=keep_manual_only
selected_option=A_KEEP_MANUAL_ONLY
unattended_dispatch_enabled=false
external_side_effects_executed=false
cron_state_mutated=false
```

## Boundary

No unattended send, repeated send, cron-triggered send, or policy apply is enabled by this record.
