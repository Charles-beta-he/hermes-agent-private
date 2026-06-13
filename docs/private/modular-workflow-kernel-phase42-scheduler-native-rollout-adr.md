# Modular Workflow Kernel Phase 42 ADR: Single-wrapper Scheduler-native Rollout

## Status

Accepted — option C was explicitly authorized by the user (`BC授权`) and applied to exactly one opted-in cron job: `multi-model-smoke-every-4h` (`2c14ca2a9bd2`).

## Decision

Create scheduler-native runner:

```text
~/.hermes/scripts/workflow-kernel-multi-model-smoke-every4h-runner.py
```

Apply exactly one scheduler mutation:

```text
~/.hermes/cron/jobs.json
job_id=2c14ca2a9bd2
script: multi-model-smoke-every4h-wrapper.py
    -> workflow-kernel-multi-model-smoke-every4h-runner.py
```

Backup:

```text
/Users/charles/.hermes/cron/jobs.json.bak-phase42-scheduler-native-20260614014254
```

Rollback:

```bash
cp /Users/charles/.hermes/cron/jobs.json.bak-phase42-scheduler-native-20260614014254 /Users/charles/.hermes/cron/jobs.json
```

## Evidence

Dry-run before apply:

```text
SCHEDULER_NATIVE_ROLLOUT_DRY_RUN_READY
```

Apply readback:

```text
SCHEDULER_NATIVE_ROLLOUT_APPLIED
job_script=workflow-kernel-multi-model-smoke-every4h-runner.py
```

Native plan-only verification:

```text
payload_status=scheduler_native_provider_health_exclusion_plan
receipt_runtime_mode=scheduler_native_plan_only
cron_state_mutated=false
model_generation_attempted=false
```

Post-rollout projection/readiness:

```text
SCHEDULER_READBACK_PROJECTION_READY
RECEIPT_CORRELATION_PARTIAL_READY
RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE
DRIFT_WATCH_DEGRADED_NOT_BLOCKED signal=production_receipts_not_complete only for daily-full
```

## Boundary

This rollout did not enable global `workflow_kernel.RUNTIME_SWITCH_ENABLED`, did not broaden to daily-full scheduler-native takeover, did not trigger cron, did not call credentialed model generation, and did not send external messages.
