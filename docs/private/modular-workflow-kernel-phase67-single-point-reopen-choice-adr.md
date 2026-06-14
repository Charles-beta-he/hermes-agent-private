# Modular Workflow Kernel Phase 67 ADR: Single-point Reopen Choice

## Status

Accepted — Phase67 records the concrete reopen implementation-point choice as `A_NO_REOPEN_KEEP_MANUAL_ONLY`.

## Decision

Add:

```text
scripts/workflow_single_point_reopen_choice.py
tests/scripts/test_workflow_single_point_reopen_choice.py
```

The record is append-only and record-only. It chooses the safe default from Phase66 and does not apply or enable any dispatch behavior.

## Evidence

```text
status=SINGLE_POINT_REOPEN_CHOICE_RECORDED
choice=A_NO_REOPEN_KEEP_MANUAL_ONLY
record_only=true
reopen_apply_enabled=false
unattended_dispatch_enabled=false
external_side_effects_executed=false
```

## Boundary

No unattended apply, cron mutation, repeated send, or external dispatch is enabled.
