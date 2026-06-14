# Modular Workflow Kernel Phase 77 ADR: Private Export Retention Watch

## Status

Accepted — Phase77 adds a read-only retention/drift watch over the private export artifact and no-apply choice.

## Decision

Add:

```text
scripts/workflow_private_export_retention_watch.py
tests/scripts/test_workflow_private_export_retention_watch.py
```

The watch verifies Phase75 private export and Phase76 keep-private choice invariants.

## Evidence

```text
status=PRIVATE_EXPORT_RETENTION_WATCH_CLEAR
drift=[]
read_only=true
publication_apply_enabled=false
external_publication_enabled=false
```

## Boundary

No publication, external send, cron mutation, unattended dispatch, or credential access is performed.
