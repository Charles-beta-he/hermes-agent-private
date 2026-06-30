# Control Loop and Reconciliation

Startup self-check and dynamic self-check are not isolated cron features. They are instances of the ERG Runtime reconciliation loop.

## MAPE-K mapping

```text
Monitor  -> collect observed state
Analyze  -> classify divergence
Plan     -> choose next safe action
Execute  -> perform only authorized action
Knowledge-> ledger, evidence, projections, tests, skills, SSOT
```

## Observed state sources

- cron: jobs.json, run outputs, missed-run logs, scheduler heartbeat, job artifacts
- gateway: delivery outbox, message IDs, platform delivery receipts, session store
- taskpool: task markdown, kanban DB, proof logs, certification records
- mission: stage reports, open decisions, tick budget, signal queue
- workers: worker job registry, leases, heartbeats, artifacts
- providers: model health, quota state, error class, circuit breakers
- repository: git status, commits, post-update checks, tests
- UI/control surfaces: dashboard/Control Tower projections only, not canonical facts

## Divergence classes

- untriggered intent
- stale in-flight execution
- generated but unmarked artifact
- delivered but unmarked projection
- delivery failed
- delivery ambiguous
- evidence missing
- evidence stale
- policy blocked
- authority missing
- certification degraded/not-certified
- provider degraded
- terminal state missing

## Planning actions

- observe only
- dry-run
- mark-only
- resend existing artifact once
- retry/requeue once
- rollback
- dead_letter
- expire
- produce human authorization packet
- create improvement candidate

## Execution rule

The reconciliation engine may choose an action, but execution still requires:

- exact target;
- side-effect class within authority;
- idempotency key;
- max attempts;
- receipt/readback path;
- rollback or safe terminal state;
- evidence record.

## Cron self-check as example

A daily report schedule becomes:

```text
IntentRecord: job_id + scheduled_slot + logical_date
ExecutionLease: cron worker/run id
Evidence: report artifact + output file + delivery receipt
Projection: cron status + Telegram message + dashboard row
Closure: completed / compensated / missed / manual_required
Compensation: mark-only / resend artifact / requeue once / dead_letter
```

Thus cron startup audit is not a special architecture. It is a subsystem-specific Monitor/Analyze pass within the general reconciliation engine.
