# Modular Workflow Kernel Phase 71 ADR: Private Archive Retention Watch

## Status

Accepted — Phase71 adds a read-only retention/drift watch over the private governance archive.

## Decision

Add:

```text
scripts/workflow_private_archive_retention_watch.py
tests/scripts/test_workflow_private_archive_retention_watch.py
```

The watch verifies Phase68 archive readiness, Phase69 health, Phase70 private publication default, and disabled publication apply.

## Evidence

```text
status=PRIVATE_ARCHIVE_RETENTION_WATCH_CLEAR
retention_policy=private_archive_retained_no_publication
read_only=true
publication_apply_enabled=false
external_side_effects_executed=false
```

## Boundary

No publication, external send, cron mutation, or credential access is performed.
