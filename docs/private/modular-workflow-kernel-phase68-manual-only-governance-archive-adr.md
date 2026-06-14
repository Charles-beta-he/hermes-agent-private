# Modular Workflow Kernel Phase 68 ADR: Manual-only Governance Archive

## Status

Accepted — Phase68 builds a read-only governance archive/index for the closed manual-only ladder.

## Decision

Add:

```text
scripts/workflow_manual_only_governance_archive.py
tests/scripts/test_workflow_manual_only_governance_archive.py
```

The archive indexes key evidence from the no-send dry-run cron, one-shot send history, closure, monitoring, reopen packet, and keep-closed choice.

## Evidence

```text
status=MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY
archive_policy=read_only_index_closed_ladder
read_only=true
reopen_apply_enabled=false
unattended_dispatch_enabled=false
external_side_effects_executed=false
```

## Boundary

This phase is archive/index only. Publication or external sharing remains a separate side-effect packet.
