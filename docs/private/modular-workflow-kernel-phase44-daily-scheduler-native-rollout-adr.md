# Modular Workflow Kernel Phase 44 ADR: Daily Scheduler-native Rollout

## Status

Accepted — under the user's full authorization, the second opted-in cron wrapper (`multi-model-smoke-daily-full`, job `42baeebb646c`) was moved to a scheduler-native workflow-kernel runner.

## Decision

Mutate exactly one remaining opted-in cron job:

```text
job_id=42baeebb646c
script: multi-model-smoke-daily-full-wrapper.py
    -> workflow-kernel-multi-model-smoke-daily-full-runner.py
```

The every-4h job remains on its already-applied scheduler-native runner.

## Evidence

```text
phase44=SCHEDULER_NATIVE_ROLLOUT_APPLIED
daily_script=workflow-kernel-multi-model-smoke-daily-full-runner.py
every4h_script=workflow-kernel-multi-model-smoke-every4h-runner.py
projection=SCHEDULER_READBACK_PROJECTION_READY
correlation=RECEIPT_CORRELATION_PARTIAL_READY
drift=DRIFT_WATCH_CLEAR_READ_ONLY
missing_receipt_jobs=[]
```

## Rollback

Use the backup path emitted in the Phase44 report:

```bash
cp <backup_path> ~/.hermes/cron/jobs.json
```

## Boundary

This rollout did not send external messages and did not require credentialed model generation for verification.
