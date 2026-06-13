# Modular Workflow Kernel Phase 31 ADR: Cron Wrapper Kernel Shim

## Status

Accepted — provider-health gate consumption for the two opt-in smoke wrappers has moved from duplicated wrapper-local logic into `workflow_kernel.cron_wrappers`.

## Context

Phase 27 made the workflow kernel switchable in compatibility-shim mode while keeping the global runtime switch disabled. Phases 28–30 then wired two concrete wrappers and calibrated Xiaomi/MiMo provider-health evidence.

The next safe architecture switch is not a scheduler rewrite or global switch enablement. It is a strangler step: centralize the already-proven wrapper gate logic in the kernel package while preserving wrapper entrypoints and cron definitions.

## Decision

Create:

```text
workflow_kernel/cron_wrappers.py
```

Move shared behavior into it:

- latest provider-health gate artifact lookup;
- gate decision readback by job id;
- daily full typed local skip payload and idempotent skip ledger;
- every-4h degraded-provider exclusion argument compiler.

Patch only the two already opted-in wrappers:

```text
~/.hermes/scripts/multi-model-smoke-daily-full-wrapper.py
~/.hermes/scripts/multi-model-smoke-every4h-wrapper.py
```

They now import the kernel shim from:

```text
~/.hermes/hermes-agent/workflow_kernel/cron_wrappers.py
```

The wrappers remain the cron entrypoints. `~/.hermes/cron/jobs.json` is unchanged.

## Non-goals

This phase does not:

- enable `workflow_kernel.RUNTIME_SWITCH_ENABLED` globally;
- mutate cron definitions;
- rewrite scheduler execution;
- execute model generation during verification;
- broaden to all cron wrappers;
- read/write credentials;
- send external messages.

## Evidence

Focused tests:

```text
./scripts/run_tests.sh \
  tests/workflow_kernel/test_cron_wrapper_kernel_shim.py \
  tests/scripts/test_multi_model_smoke_every4h_wrapper_gate.py \
  tests/scripts/test_multi_model_smoke_daily_wrapper_gate.py \
  tests/workflow_kernel/test_runtime_switch_readiness.py

19 tests passed, 0 failed
```

Plan-only wrapper readback:

```text
status: provider_health_exclusion_plan
script_args: [--skip-claude, --timeout, 45]
kernel_shim: true
model_generation_attempted: false
cron_state_mutated: false
```

## Consequences

Benefits:

- removes duplicated gate lookup/decision logic from wrappers;
- makes wrapper rollout part of the kernel strangler path;
- keeps current cron jobs stable and reversible;
- creates a clearer boundary for future scheduler-level projections.

Tradeoffs:

- wrappers now depend on the local Hermes Agent checkout path being present;
- this is still wrapper-level architecture, not scheduler-native integration.

## Next Phase

Phase 32 should add a scheduler-level stale-gate/readback projection or observe the next real scheduled run. Global runtime switch and batch wrapper rollout remain closed.
