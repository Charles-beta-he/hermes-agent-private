# Modular Workflow Kernel Phase 57 ADR: Dry-run Cron Rollout

## Status

Accepted — Phase57 installs a single local no-agent cron job for Workflow Kernel unattended dry-run receipts.

## Decision

Add/update exactly one cron job:

```text
id=wk57dryrun01
name=Workflow Kernel unattended dry-run receipt
script=workflow-kernel-unattended-dry-run-runner.py
schedule=45 12 * * *
deliver=local
no_agent=true
```

The cron wrapper delegates to `scripts/workflow_unattended_dry_run_scheduler.py --append-ledger --write-report --json` and prints a compact local-only JSON summary.

## Safety

```text
would_send=false
external_side_effects_executed=false
unattended_dispatch_enabled=false
```

A backup of `~/.hermes/cron/jobs.json` is created before mutation and the rollout report records the rollback command.

## Boundary

This phase schedules no-send proof loops only. It does not activate a guarded send runner and does not send Telegram/Lark/GitHub messages.
