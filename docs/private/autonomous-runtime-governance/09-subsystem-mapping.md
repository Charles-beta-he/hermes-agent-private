# Subsystem Mapping to ERG Runtime

## Cron

- Intent Source: schedule/job definition.
- IntentRecord: job_id + scheduled_slot + logical_date/idempotency_key.
- Evidence: cron output, job artifact, scheduler heartbeat, delivery receipt.
- Gate: provider health, grace window, no duplicate run, delivery target policy.
- Compensation: requeue once, mark-only, resend existing artifact, missed, dead_letter.
- Terminal states: completed, silent_ok, compensated, missed, manual_required, dead_letter.

## Gateway

- Intent Source: inbound platform message or outbound projection request.
- Evidence: session event, delivery outbox item, platform message_id, adapter response.
- Gate: pairing/auth, target allowlist, payload policy, external write authority.
- Compensation: retry under outbox policy, dead_letter, manual intervention for ambiguous delivery.

## Taskpool / Kanban

- Intent Source: task candidate or accepted task.
- Evidence: proof log, command/test output, artifact references, certification records.
- Gate: acceptance evidence, review/certification, lifecycle authority.
- Projection: markdown note, kanban DB, Control Tower row.
- Compensation: rollback status, reopen, block with evidence debt, human authorization packet.

## Mission

- Intent Source: durable objective.
- Evidence: stage reports, signals, decisions, tick budget ledger.
- Gate: open human decision, budget exhaustion, authority boundary.
- Compensation: budget extension, dispatch-events, handoff packet, stop/expire.

## Worker / Agent Team

- Intent Source: ExecutionLease.
- Evidence: stdout/stderr artifact, proposal artifact, file diff, tests, review packet.
- Gate: no lifecycle mutation by worker role; output is proposal until certified/authorized.
- Compensation: retry with fallback model, dead_letter, human review.

## Control Tower / Dashboard

- Role: projection/operator cockpit.
- Evidence: reads ledgers and domain stores; does not become canonical fact by UI state alone.
- Gate: any write-through action must be digest-bound, authorized, read back, rollback-capable.

## Model routing / multi-model certification

- Intent Source: review/certification request.
- Evidence: model responses, claim-evidence mapping, discussion result, certification manifest.
- Gate: quorum, truth grounding, claim evidence, no-action vs implementation classification.
- Limit: model consensus cannot replace observed runtime proof.
