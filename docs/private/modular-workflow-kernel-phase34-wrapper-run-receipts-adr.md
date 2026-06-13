# Modular Workflow Kernel Phase 34 ADR: Wrapper Run Receipt Ledger

## Status

Accepted — the two opted-in kernel-shim cron wrappers now record append-only local run receipts containing kernel/gate metadata for future scheduled-run correlation.

## Context

Phase 33 correlated one real every-4h scheduled run after the Phase 32 projection, but existing wrapper JSONL logs did not include per-run `kernel_shim`, `gate_report_id`, or `gate_decision` metadata. That made attribution rely on separate projection evidence plus wrapper code inspection.

The next safe step is to enrich future wrapper runs with local receipt metadata, not to force cron execution or switch the scheduler.

## Decision

Add shared receipt helpers to:

```text
workflow_kernel/cron_wrappers.py
```

Default receipt ledger:

```text
~/.hermes/state/cron-provider-health-gate/wrapper_run_receipts.jsonl
```

Patch only the two opted-in wrappers:

```text
~/.hermes/scripts/multi-model-smoke-daily-full-wrapper.py
~/.hermes/scripts/multi-model-smoke-every4h-wrapper.py
```

Each future wrapper run records:

```text
job_id
job_name
runtime_mode
kernel_shim=true
gate_report
gate_report_id
gate_decision
payload_status
payload_ok
payload_returncode
model_generation_attempted
cron_state_mutated=false
external_side_effects=false
summary
```

## Non-goals

This phase does not:

- execute or trigger cron jobs;
- mutate cron definitions;
- enable global runtime switch;
- call model generation during verification;
- read/write credentials;
- send external messages;
- claim a real daily-full post-projection run has occurred.

## Evidence

Verification used an override ledger:

```text
HERMES_CRON_WRAPPER_RECEIPT_LEDGER=/tmp/phase34_receipts.jsonl
HERMES_MULTI_MODEL_SMOKE_EVERY4H_PLAN_ONLY=1 \
  python3 ~/.hermes/scripts/multi-model-smoke-every4h-wrapper.py
```

Readback:

```text
runtime_mode: plan_only
kernel_shim: true
job_id: 2c14ca2a9bd2
```

Focused tests:

```text
./scripts/run_tests.sh \
  tests/workflow_kernel/test_cron_wrapper_kernel_shim.py \
  tests/scripts/test_multi_model_smoke_every4h_wrapper_gate.py \
  tests/scripts/test_multi_model_smoke_daily_wrapper_gate.py

16 tests passed, 0 failed
```

## Consequences

Benefits:

- future correlation can rely on a direct run receipt rather than reconstructing from code/projection;
- preserves auditability without scheduler mutation;
- keeps the architecture switch bounded to already opted-in wrappers.

Tradeoffs:

- future scheduled runs will append a local receipt row;
- existing historical runs remain without receipt metadata.

## Next Phase

Phase 35 can update `workflow_cron_run_outcome_correlation.py` to consume `wrapper_run_receipts.jsonl`, or wait for the daily-full scheduled run and correlate both wrappers with direct receipt evidence.
