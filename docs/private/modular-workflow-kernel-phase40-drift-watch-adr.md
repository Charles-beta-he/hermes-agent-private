# Modular Workflow Kernel Phase 40 ADR: Receipt/Projection Drift Watch

## Status

Accepted — a read-only drift watcher now detects stale projection/correlation/summary artifacts and incomplete production wrapper-run receipts.

## Context

Phase 39 produced an enablement runbook and recommended a drift watcher before any runtime/scheduler boundary crossing. Production wrapper-run receipts are expected only after future scheduled wrapper runs, so missing receipts should be visible as a degraded-not-blocked state rather than silently ignored or forced by triggering cron.

## Decision

Add:

```text
scripts/workflow_drift_watch.py
```

It reads:

```text
docs/private/workflow-kernel-cron-runtime-projection/
docs/private/workflow-kernel-cron-run-correlation/
docs/private/workflow-kernel-runtime-readiness-summary/
~/.hermes/state/cron-provider-health-gate/wrapper_run_receipts.jsonl
```

It emits:

```text
DRIFT_WATCH_CLEAR_READ_ONLY
```

or:

```text
DRIFT_WATCH_DEGRADED_NOT_BLOCKED
```

Current real readback is degraded-not-blocked because production receipts are not complete yet.

## Evidence

Verification:

```text
./venv/bin/python -m py_compile \
  scripts/workflow_drift_watch.py \
  scripts/workflow_enablement_runbook.py

./scripts/run_tests.sh \
  tests/scripts/test_workflow_drift_watch.py \
  tests/scripts/test_workflow_enablement_runbook.py
```

Readback:

```text
8 tests passed, 0 failed
status=DRIFT_WATCH_DEGRADED_NOT_BLOCKED
drift_signals=[production_receipts_not_complete]
missing_receipt_jobs=[42baeebb646c, 2c14ca2a9bd2]
```

## Non-goals

This phase does not trigger cron to create receipts. It does not mutate cron, enable runtime switch, call models, read credentials, or send external messages.

## Consequence

The remaining safe path is waiting for natural scheduled runs or explicitly authorizing B/C from the Phase 37 packet. The watcher makes that state machine observable.
