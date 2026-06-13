# Modular Workflow Kernel Phase 29 ADR: Multi-provider Cron Wrapper Exclusion Hint

## Status

Accepted — the every-4h multi-model smoke wrapper now consumes the provider-health gate and converts `run_with_degraded_provider_exclusions` into concrete `--model` selection arguments.

## Context

Phase 28 proved one specific degraded-provider job can safely emit a typed local skip. The next lower-risk target is the multi-provider every-4h smoke job:

```text
job_id: 2c14ca2a9bd2
name: multi-model-smoke-every-4h
gate decision: run_with_degraded_provider_exclusions
degraded endpoint: xiaomi
```

Unlike the daily full Xiaomi/MiMo job, the every-4h job should not fully skip when one provider is degraded. It should continue with non-degraded providers.

## Decision

Patch exactly one wrapper:

```text
/Users/charles/.hermes/scripts/multi-model-smoke-every4h-wrapper.py
```

New behavior:

1. Read the latest provider health gate report.
2. Find decision for job `2c14ca2a9bd2`.
3. If decision is `run_with_degraded_provider_exclusions`, map degraded endpoints to model exclusions.
4. Pass explicit `--model` args for allowed models.
5. Keep base wrapper args:

```text
--skip-claude --timeout 45
```

Current planned args:

```text
--skip-claude --timeout 45 --model gpt-5.5 --model deepseek-v4-pro
```

This excludes:

```text
mimo-v2.5-pro
```

## Safety Boundaries

This phase does not:

- mutate `~/.hermes/cron/jobs.json`;
- enable global runtime switch;
- patch more than one additional wrapper;
- read/write credentials;
- send external messages;
- call model generation during verification.

Verification used plan-only mode:

```text
HERMES_MULTI_MODEL_SMOKE_EVERY4H_PLAN_ONLY=1
```

## Evidence

Focused tests:

```text
./scripts/run_tests.sh \
  tests/scripts/test_multi_model_smoke_every4h_wrapper_gate.py \
  tests/scripts/test_multi_model_smoke_daily_wrapper_gate.py \
  tests/scripts/test_cron_provider_health_gate.py

14 tests passed, 0 failed
```

Plan-only wrapper readback:

```text
status: provider_health_exclusion_plan
selected_models: [gpt-5.5, deepseek-v4-pro]
degraded_endpoints_excluded: [xiaomi]
model_generation_attempted: false
cron_state_mutated: false
```

## Consequences

Benefits:

- The every-4h multi-provider smoke no longer has to fail the whole run because Xiaomi/MiMo is degraded.
- Non-degraded providers remain eligible for scheduled smoke checks.
- The wrapper-level rollout remains reversible and scoped.

Tradeoffs:

- This is still wrapper-level logic, not scheduler-native.
- Real scheduled behavior should be observed at the next cadence before broader rollout.

## Next Phase

Phase 30 should collect post-schedule readback or add a scheduler-level typed projection. Do not batch-patch every cron wrapper until the two opt-in wrappers have stable readback evidence.
