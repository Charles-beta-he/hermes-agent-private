# Modular Workflow Kernel Phase 28 ADR: Single Cron Wrapper Rollout

## Status

Accepted — one opt-in cron wrapper now consumes the provider-health gate and emits a typed local skip when its target provider is degraded.

## Context

Phase 27 ended at:

```text
RUNTIME_SWITCH_READY_PENDING_EXPLICIT_ENABLE
next_closed_boundary=explicit runtime switch enablement and one-slice wrapper rollout
```

The current provider-health gate says:

```text
job_id: 42baeebb646c
name: multi-model-smoke-daily-full
decision: skip_provider_health_degraded
endpoint: xiaomi
reason: endpoint xiaomi classified tls_error
```

This is the safest first rollout because the daily full smoke explicitly targets Xiaomi/MiMo and would otherwise burn a known-degraded provider call.

## Decision

Patch exactly one user cron wrapper:

```text
/Users/charles/.hermes/scripts/multi-model-smoke-daily-full-wrapper.py
```

New behavior:

1. Load the latest provider health gate report.
2. Find decision for job `42baeebb646c`.
3. If decision is `skip_provider_health_degraded`, append a typed skip event to:

```text
~/.hermes/state/cron-provider-health-gate/provider_health_skips.jsonl
```

4. Print the skip payload and exit 0.
5. Do not call `multi-model-smoke.py` in the skip path.
6. If the gate is absent or allows retry, retain existing behavior and call the original safe smoke runner.

## Safety Boundaries

This phase does not:

- mutate `~/.hermes/cron/jobs.json`;
- pause/resume/remove cron jobs;
- enable global runtime switch;
- change every cron wrapper;
- call model generation in the verified skip path;
- read/write credentials;
- send external messages.

Only the daily full wrapper is opted in. The every-4h multi-provider wrapper remains unchanged.

## Evidence

Focused tests:

```text
./scripts/run_tests.sh \
  tests/scripts/test_multi_model_smoke_daily_wrapper_gate.py \
  tests/scripts/test_cron_provider_health_gate.py \
  tests/workflow_kernel/test_runtime_switch_readiness.py

13 tests passed, 0 failed
```

Actual wrapper execution:

```text
python3 /Users/charles/.hermes/scripts/multi-model-smoke-daily-full-wrapper.py
```

Readback:

```text
status: provider_health_degraded_skip
job_id: 42baeebb646c
model_generation_attempted: false
cron_state_mutated: false
external_side_effects: false
```

## Consequences

Benefits:

- The next scheduled daily Xiaomi/MiMo smoke can record a clean local skip instead of a generic provider RuntimeError.
- The wrapper remains idempotent by gate-report/job/decision key.
- The gate's result is exercised by the actual cron script path, not just a report.

Tradeoffs:

- This is not scheduler-native yet; it is one wrapper-level opt-in.
- Provider inference and health decisions still depend on the latest local report.

## Next Phase

Phase 29 should either:

1. Add an opt-in exclusion hint for the multi-provider every-4h wrapper, or
2. Create a scheduler-level projection that surfaces typed health skips without editing more wrappers.

Do not batch-mutate all cron jobs until both paths have readback evidence.
