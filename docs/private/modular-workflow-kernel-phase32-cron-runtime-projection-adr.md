# Modular Workflow Kernel Phase 32 ADR: Scheduler-level Runtime Projection

## Status

Accepted — a read-only scheduler projection now shows whether opted-in cron wrappers are present, kernel-shim backed, and consuming fresh provider-health/probe artifacts.

## Context

Phase 31 moved the two opted-in smoke wrappers onto `workflow_kernel.cron_wrappers`, but scheduler-level visibility still lagged behind. Operators could see wrapper code and health-gate reports separately, but not a single projection answering:

- which cron job will run which wrapper;
- whether that wrapper imports the kernel shim;
- which gate/probe artifact is fresh enough to trust;
- whether missing gate decisions are expected or a degradation.

## Decision

Create:

```text
scripts/workflow_cron_runtime_projection.py
```

It compiles a read-only projection from:

```text
~/.hermes/cron/jobs.json
docs/private/workflow-kernel-cron-provider-health-gate/cron_provider_health_gate_*.json
docs/private/workflow-kernel-provider-network-probe/provider_network_probe_*.json
~/.hermes/scripts/multi-model-smoke-*-wrapper.py
workflow_kernel.cron_wrappers
```

The projection classifies each opted-in wrapper as:

```text
KERNEL_SHIM_SCHEDULER_READBACK_READY
KERNEL_SHIM_NO_GATE_DECISION_NEEDED
NO_GATE_DECISION_FOR_WRAPPER
WRAPPER_NOT_USING_KERNEL_SHIM
WRAPPER_SCRIPT_MISSING
CRON_JOB_MISSING
```

A missing gate decision is acceptable only when the cron job is not currently in error, because Phase 24 health gate emits decisions for failed jobs rather than all jobs.

## Non-goals

This phase does not:

- mutate `~/.hermes/cron/jobs.json`;
- execute cron jobs;
- invoke model generation;
- read/write credentials;
- send Telegram or other messages;
- enable the global runtime switch;
- migrate scheduler execution to kernel-native runtime.

## Evidence

Commands:

```text
python -m py_compile scripts/workflow_cron_runtime_projection.py
./scripts/run_tests.sh \
  tests/scripts/test_workflow_cron_runtime_projection.py \
  tests/workflow_kernel/test_cron_wrapper_kernel_shim.py \
  tests/scripts/test_cron_provider_health_gate.py \
  tests/scripts/test_provider_network_probe_ledger.py
```

Result:

```text
19 tests passed, 0 failed
```

Real projection readback:

```text
status: SCHEDULER_READBACK_PROJECTION_READY
projection_counts:
  KERNEL_SHIM_SCHEDULER_READBACK_READY: 1
  KERNEL_SHIM_NO_GATE_DECISION_NEEDED: 1
gate_stale: false
probe_stale: false
runtime_switch_enabled_by_default: false
```

## Consequences

Benefits:

- scheduler/operator layer can see kernel-shim adoption without reading wrapper code manually;
- stale gate/probe artifacts become explicit before trusting wrapper behavior;
- `ok` jobs no longer look degraded just because Phase 24 emits decisions only for failed jobs;
- prepares the next phase for real scheduled-run observation.

Tradeoffs:

- still projection-only; it does not enforce scheduler behavior;
- artifact freshness threshold is fixed by CLI option/default and may need future tuning.

## Next Phase

Phase 33 should observe a real scheduled wrapper run or add durable run-outcome correlation. Global runtime switch and scheduler-native execution remain closed.
