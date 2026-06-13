# Modular Workflow Kernel Phase 41 ADR: Runtime-enable Shadow Dry-run

## Status

Accepted — option B was explicitly authorized by the user (`BC授权`) and executed as a single-wrapper shadow dry-run for `multi-model-smoke-every-4h`.

## Decision

Add:

```text
scripts/workflow_runtime_shadow_dry_run.py
```

The script runs the existing every-4h wrapper in plan-only mode with a temporary receipt ledger and verifies `~/.hermes/cron/jobs.json` is unchanged.

## Evidence

```text
status=RUNTIME_SHADOW_DRY_RUN_READY
payload_status=provider_health_exclusion_plan
receipt_runtime_mode=plan_only
cron_state_mutated=false
credentialed_generation_attempted=false
```

Focused verification:

```text
3 tests passed, 0 failed
```

## Boundary

No cron mutation, cron trigger, credentialed model generation, or external delivery occurred in Phase 41.
