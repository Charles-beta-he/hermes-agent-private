# Modular Workflow Kernel Phase 35 ADR: Receipt-aware Run Correlation

## Status

Accepted — `workflow_cron_run_outcome_correlation.py` now consumes `wrapper_run_receipts.jsonl` when present, falling back to wrapper JSONL logs otherwise.

## Context

Phase 34 added append-only wrapper-run receipts for future scheduled runs. Phase 33 correlation still used wrapper JSONL logs only. The next safe step is to make correlation receipt-aware without triggering cron or requiring a scheduled run to happen during verification.

## Decision

Update:

```text
scripts/workflow_cron_run_outcome_correlation.py
```

Add:

```text
--receipt-ledger ~/.hermes/state/cron-provider-health-gate/wrapper_run_receipts.jsonl
```

When a receipt exists after the projection timestamp, classify:

```text
RECEIPT_AFTER_PROJECTION_CORRELATED
```

If no receipt exists yet, keep the existing log-based statuses such as:

```text
REAL_RUN_AFTER_PROJECTION_CORRELATED
ONLY_PRE_PROJECTION_RUN_OBSERVED
```

## Evidence

Focused test:

```text
./scripts/run_tests.sh tests/scripts/test_workflow_cron_run_outcome_correlation.py
```

Result:

```text
5 tests passed, 0 failed
```

Real readback currently remains:

```text
REAL_RUN_CORRELATION_PARTIAL_READY
ONLY_PRE_PROJECTION_RUN_OBSERVED: 1
REAL_RUN_AFTER_PROJECTION_CORRELATED: 1
```

That is expected because production `wrapper_run_receipts.jsonl` will populate only on future scheduled wrapper runs after Phase 34.

## Non-goals

No cron execution, no scheduler mutation, no model generation, no external delivery, no credential read/write.

## Next Phase

Continue with the next safe architecture layer, or wait for future scheduled runs to populate production receipt rows and then claim receipt-backed correlation.
