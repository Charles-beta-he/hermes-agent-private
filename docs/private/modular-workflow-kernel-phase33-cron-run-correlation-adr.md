# Modular Workflow Kernel Phase 33 ADR: Real Scheduled-run Outcome Correlation

## Status

Accepted — a read-only run-outcome correlation now links scheduler state, Phase 32 projection, and wrapper JSONL outcomes.

## Context

Phase 32 proved the scheduler-level projection for two opted-in kernel-shim cron wrappers was ready. The next step was not scheduler-native enforcement; it was to observe whether a real scheduled wrapper run occurred after that projection and whether the observed outcome matched the projected wrapper path.

## Decision

Create:

```text
scripts/workflow_cron_run_outcome_correlation.py
```

It reads only:

```text
~/.hermes/cron/jobs.json
docs/private/workflow-kernel-cron-runtime-projection/workflow_kernel_cron_runtime_projection_*.json
~/.hermes/logs/multi-model-smoke-every4h-wrapper.jsonl
~/.hermes/logs/multi-model-smoke-daily-full-wrapper.jsonl
```

It classifies each wrapper as:

```text
REAL_RUN_AFTER_PROJECTION_CORRELATED
ONLY_PRE_PROJECTION_RUN_OBSERVED
NO_WRAPPER_LOG_FOUND
NO_PROJECTION_FOR_JOB
CRON_JOB_MISSING
WRAPPER_LOG_TIMESTAMP_UNPARSEABLE
```

## Current Evidence

Current real readback:

```text
status: REAL_RUN_CORRELATION_PARTIAL_READY
REAL_RUN_AFTER_PROJECTION_CORRELATED: 1
ONLY_PRE_PROJECTION_RUN_OBSERVED: 1
```

Interpretation:

- `multi-model-smoke-every-4h` ran after the Phase 32 projection and returned `ok` in its wrapper JSONL log.
- `multi-model-smoke-daily-full` has not yet had its next scheduled run after the Phase 32 projection; the latest log is still pre-projection.

## Non-goals

This phase does not:

- execute or trigger cron jobs;
- mutate `~/.hermes/cron/jobs.json`;
- call model generation;
- read/write credentials;
- send messages;
- enable global runtime switch;
- claim full two-wrapper runtime correlation before the daily job runs.

## Evidence

Commands:

```text
python -m py_compile scripts/workflow_cron_run_outcome_correlation.py scripts/workflow_cron_runtime_projection.py
./scripts/run_tests.sh \
  tests/scripts/test_workflow_cron_run_outcome_correlation.py \
  tests/scripts/test_workflow_cron_runtime_projection.py \
  tests/workflow_kernel/test_cron_wrapper_kernel_shim.py
python scripts/workflow_cron_run_outcome_correlation.py --write-report --json
```

Result:

```text
12 tests passed, 0 failed
status: REAL_RUN_CORRELATION_PARTIAL_READY
```

## Consequences

Benefits:

- real scheduled run evidence is now separated from projection-only evidence;
- avoids falsely claiming daily wrapper runtime behavior before its next scheduled run;
- creates a clean next boundary for full runtime correlation or wrapper-run receipt enrichment.

Tradeoffs:

- existing wrapper JSONL logs do not include gate_report_id/kernel_shim metadata inside every run receipt; the correlation relies on Phase 32 projection plus wrapper code detection for shim attribution.

## Next Phase

Phase 34 should either wait for the daily-full scheduled run, or add a durable wrapper-run ledger/receipt field so future correlations include `kernel_shim`, `gate_report_id`, and `projection_report_id` directly in each run outcome.
