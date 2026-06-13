# Modular Workflow Kernel Phase 36 ADR: Runtime Readiness Summary

## Status

Accepted — a read-only operator-facing runtime readiness summary now aggregates the workflow kernel switch boundary, scheduler projection, run correlation, receipt readiness, provider health gate, and provider probe freshness.

## Context

Phases 31-35 incrementally moved two opt-in cron wrappers onto `workflow_kernel.cron_wrappers`, added scheduler projection, correlated real scheduled runs, added wrapper-run receipts, and made correlation receipt-aware.

Operators still needed a single summary artifact that answers:

- is the global runtime switch still closed?
- are projection/correlation/receipt layers ready?
- are provider gate/probe artifacts fresh?
- are there any blocking checks before an explicit runtime enable gate?

## Decision

Add:

```text
scripts/workflow_runtime_readiness_summary.py
```

The script reads the newest artifacts from:

```text
docs/private/workflow-kernel-cron-runtime-projection/
docs/private/workflow-kernel-cron-run-correlation/
docs/private/workflow-kernel-receipt-aware-correlation/
docs/private/workflow-kernel-wrapper-run-receipts/
docs/private/workflow-kernel-cron-provider-health-gate/
docs/private/workflow-kernel-provider-network-probe/
workflow_kernel.runtime.compile_runtime_readiness()
```

It emits:

```text
RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE
```

only when all readiness checks pass and the global runtime switch remains disabled.

## Non-goals

This phase does not:

- enable `workflow_kernel.RUNTIME_SWITCH_ENABLED`;
- mutate `~/.hermes/cron/jobs.json`;
- trigger cron jobs;
- call model generation;
- read/write credentials;
- send external messages;
- claim receipt-backed full runtime correlation before real receipts exist.

## Evidence

Verification:

```text
./venv/bin/python -m py_compile \
  scripts/workflow_runtime_readiness_summary.py \
  scripts/workflow_cron_run_outcome_correlation.py \
  scripts/workflow_cron_runtime_projection.py \
  workflow_kernel/cron_wrappers.py

./scripts/run_tests.sh \
  tests/scripts/test_workflow_runtime_readiness_summary.py \
  tests/scripts/test_workflow_cron_run_outcome_correlation.py \
  tests/scripts/test_workflow_cron_runtime_projection.py \
  tests/workflow_kernel/test_cron_wrapper_kernel_shim.py
```

Readback:

```text
18 tests passed, 0 failed
status=RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE
blocking_checks=[]
runtime_switch_enabled_by_default=false
```

## Consequences

This creates a single operator summary for future phases. It makes runtime enablement decisions more auditable, but it still does not grant authority to cross the explicit enable gate.

## Next Phase

Continue with read-only or reversible architecture work. A real runtime enable or scheduler-native takeover remains a separate explicit authorization boundary.
