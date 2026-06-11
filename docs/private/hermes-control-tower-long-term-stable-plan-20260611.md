# Hermes Control Tower long-term stable plan

Date: 2026-06-11
Status: canonical target model for the Control Tower optimization program
Related implementation packet: `docs/private/hermes-control-tower-implementation-packet-20260607.md`

## Objective

Control Tower becomes the long-term operational console for Hermes workflow execution. It is not a decorative dashboard or a second task list. It is the operator cockpit over a single ledger-backed control plane that lets Hermes diagnose, execute, verify, recover, and escalate with minimal human steering.

## Success criteria

1. The first screen answers whether Hermes can make progress right now.
2. Every stopped workflow has a machine-readable blocker class, evidence requirement, owner, next safe action, and escalation boundary.
3. Human actions are decisions/authorizations, not manual orchestration chores.
4. Automation can run safe read-only diagnosis and bounded executors without asking again until a declared gate is reached.
5. Every write-through action is digest-bound, role-gated, idempotent, read back, and rollback/reopen capable.
6. Mission, taskpool, Kanban, worker, cron, gateway, evidence, and human-inbox state are projections/adapters; Control Tower Ledger remains the canonical workflow fact source for Control Tower decisions.
7. The UI, API, ledger, executor, persistence, read-back, tests, runtime verification, and scoped commit all advance together for each phase.

## Hard constraints

- Do not create a competing task pool.
- Do not let dashboard UI become a fact source.
- Do not hide stale signals, blocked tasks, or proof/evidence debt behind green summaries.
- Do not consume conflicting-evidence overrides until an explicit consumption contract, stale-evidence escape path, and rollback/audit semantics are implemented and verified.
- Do not execute destructive, external, credential, payment, publishing, or broad config actions without a human gate.
- Do not mark plan-only, mock-only, button-only, read-only-only, or placeholder slices as full completion when the target is full landing.

## Target architecture

### Canonical layer

- `~/.hermes/control_tower.db` is the Control Tower Ledger.
- Core records: commands, events, tasks, workflow runs, worker runs, evidence, policy decisions, human approvals, executor plans, read-only runs, closeout authorizations, adapter write intents, adapter write authorizations, write execution runs, rollback runs, conflict override projections.
- Existing stores remain external context or projection adapters: Kanban DB, taskpool Markdown notes, Mission ledger, cron jobs, gateway sessions, worker job registries.

### Console layer

Primary surfaces:

1. Operator Overview — health, progressability, blockers, stale signals, evidence debt, human decisions, drift.
2. Workflow Map — state/gate graph per command/task/mission.
3. Blocker Triage — classified reasons and next safe actions.
4. Evidence Spine — acceptance refs, required maturity, current maturity, conflicts/staleness.
5. Human Inbox — choices/approval packets with blast radius and rollback path.
6. Executor Console — dry-run, read-only executor, digest-bound write executor, rollback/reopen.
7. Mission Flow — event-driven wakeups, stale pending signals, consumed latency, cron runner health.
8. Worker Runtime — claimed/running/retry jobs, provider fallback, proof output, leases/heartbeats.
9. Source Drift — git branch/ahead/dirty/post-update-check/test health.
10. Debug — raw snapshot, downloadable evidence bundle, copy commands.

### Automation layer

Safe automation order:

1. Read-only diagnosis.
2. Command proposal.
3. Policy review / human gate if needed.
4. Dry-run plan with canonical digest.
5. Read-only executor for whitelisted L0/L1 commands.
6. Evidence-bound ledger closeout.
7. Digest-bound adapter write-through for allowlisted operations.
8. Digest-bound rollback/reopen.
9. Watchdog/self-healing only for explicitly safe classes.

## Phase roadmap

### Phase A — Operator Overview foundation

Goal: make the first screen operational.

Minimum closed loop:
- Backend snapshot adds `operator_overview`.
- UI renders progress status, critical alerts, blocker summary, recommended next safe action, and debug boundary.
- Tests cover no-dispatch/blocker/stale/evidence/source-drift summary.
- Runtime snapshot read-back proves current machine state is visible.

### Phase B — Blocker classifier and diagnosis queue

Goal: every stopped workflow is classified.

Closed loop:
- Normalize blocker classes: no_dispatchable, stale_signal, evidence_debt, human_gate, policy_gate, worker_missing, cron_missing, adapter_conflict, source_drift, capability_boundary.
- Add deterministic read-only diagnosis commands.
- UI groups blockers by class with next actions.
- Tests assert classifications and copy-only/dry-run boundaries.

### Phase C — Evidence debt and closeout readiness

Goal: show why tasks cannot close.

Closed loop:
- Acceptance refs and maturity requirements visible per task.
- Evidence debt projection reports missing/stale/conflicting evidence.
- Closeout readiness emits allowed/blocked reason and required role.
- Tests cover satisfied, missing, stale, conflicting, and override-non-consuming paths.

### Phase D — Mission/worker/cron recovery cockpit

Goal: stale event-driven workflows recover or escalate.

Closed loop:
- Mission stale signals show recovery packet.
- Worker jobs and cron watchdogs are projected from real registries/logs.
- Read-only recovery checks run safely.
- Event dispatch remains explicit and auditable.

### Phase E — Production action UX

Goal: no contextless debug buttons.

Closed loop:
- Select object first, then show legal actions.
- Every write action displays target, digest, expected mutation, role, snapshot freshness, and rollback path.
- Dev-only sample/debug actions hidden from production UI.
- Tests prevent `first pending` style contextless actions from being the primary production path.

### Phase F — Safe executor expansion

Goal: controlled automated execution.

Closed loop:
- Expand only one adapter/operation at a time.
- Require digest, idempotency, read-back, rollback, and proof.
- Human gate remains required for higher-risk or external side effects.

### Phase G — Self-healing and long-term optimization loop

Goal: Control Tower produces its own next improvements.

Closed loop:
- Repeated blockers become governed improvement candidates.
- Candidates land as SSOT docs, skills, tests, or taskpool items based on abstraction level.
- No stale WIP memory pollution.
- Control Tower shows optimization backlog and completion evidence.

## Current first slice

Implement Phase A now. Continue automatically into later phases after each verified scoped commit unless a human/permission/external-side-effect/evidence boundary blocks progression.
