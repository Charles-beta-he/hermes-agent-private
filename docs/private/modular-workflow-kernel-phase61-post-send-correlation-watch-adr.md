# Modular Workflow Kernel Phase 61 ADR: Post-send Correlation and Compensation Watch

## Status

Accepted — Phase61 correlates the Phase60 one-shot send receipt without sending any new message.

## Decision

Add:

```text
scripts/workflow_post_send_correlation_watch.py
tests/scripts/test_workflow_post_send_correlation_watch.py
```

The watch reads Phase59 preview, Phase60 receipt, and append-only receipt ledger, then verifies message id, chat id, idempotency, payload hash, ledger correlation, and compensation-path availability.

## Evidence

```text
status=POST_SEND_CORRELATION_COMPENSATION_WATCH_READY
message_id=4310
matching_ledger_rows=1
new_message_sent=false
unattended_dispatch_enabled=false
```

## Boundary

This phase is read-only. It does not send a correction, enable automatic compensation, mutate cron, or enable unattended dispatch.
