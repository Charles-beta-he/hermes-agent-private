# Modular Workflow Kernel Phase 80 ADR: Publication Governance Archive Update

## Status

Accepted — Phase80 updates the read-only publication governance archive/index.

## Decision

Add:

```text
scripts/workflow_publication_governance_archive_update.py
tests/scripts/test_workflow_publication_governance_archive_update.py
```

The archive indexes publication governance artifacts from Phase70/72/74/76/78/79 and verifies no publication apply drift.

## Evidence

```text
status=PUBLICATION_GOVERNANCE_ARCHIVE_READY
archive_policy=read_only_publication_governance_index
item_count=6
read_only=true
publication_apply_enabled=false
```

## Boundary

This is an index update only. It does not export, publish, apply, send, mutate cron, or read credentials.
