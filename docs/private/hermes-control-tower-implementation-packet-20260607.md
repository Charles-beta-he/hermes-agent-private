# Hermes Control Tower implementation packet

Date: 2026-06-07
Status: proposed / blocked on human decisions
Canonical ADR: `docs/private/hermes-control-tower-adr-20260607.md`

## Purpose

This packet turns the Control Tower ADR into an implementation-ready backlog without crossing the current human-review boundary. It is intentionally limited to planning, boundaries, and first-slice acceptance criteria.

## Product definition

Hermes Control Tower is the visual and operational control plane for Hermes workflows:

- Event-driven by default.
- Backed by one canonical fact source: `~/.hermes/control_tower.db` as the Control Tower Ledger.
- Kanban/taskpool/brain/dashboard/CLI/gateway surfaces are adapters or projections, not competing sources of truth.
- Evidence-gated for completion.
- Human-in-command for high-risk actions.
- Multi-agent-aware for worker execution and review.
- Dashboard/CLI/Gateway unified through command/event contracts.

## Target modules

### 1. Overview

Answers:

- Is the system healthy?
- What should happen next?
- Why is automation stopped?
- Which decisions need the human?

Read sources:

- taskpool `list`, `next`, `dispatch-dry-run`, `selfcheck`, `proof-check`.
- Kanban board/task list.
- worker job registry.
- cron job list and recent status.
- gateway status where available.
- evidence/proof files.

### 2. Taskpool Health

Fields:

- active count.
- ready count.
- blocked count.
- claimed/running/review count.
- stale/expired lease count.
- proof failures.
- dispatch deny reasons.
- next dispatchable task.

### 3. Workflow Map

Node types:

- event source.
- command.
- task state.
- dependency gate.
- proof gate.
- policy gate.
- human gate.
- worker step.
- verification step.
- evidence node.
- closeout candidate.

### 4. Event Stream

Fields:

- event id.
- type.
- actor.
- source.
- task id.
- workflow run id.
- timestamp.
- correlation id.
- causation id.
- payload summary.
- linked evidence.

### 5. Worker Runtime

Fields:

- worker run id.
- task id.
- worker type.
- model/provider.
- workspace.
- status.
- lease owner.
- lease expiry.
- heartbeat.
- result ref.
- evidence refs.
- failure reason.

### 6. Evidence Spine

Fields:

- evidence id.
- task id.
- evidence type.
- maturity.
- source.
- artifact path/url.
- hash.
- verifier.
- created at.
- stale/conflict status.
- acceptance criterion link.

### 7. Human Inbox

Items:

- approval requests.
- blocked tasks needing decision.
- policy overrides.
- evidence waivers.
- high-risk action confirmations.

### 8. Policy Gates

Displays:

- action level L0-L4.
- allow/deny/needs-human result.
- policy source.
- actor.
- reason.
- required evidence.

### 9. Cron Watchdogs

Only show:

- stale lease watchdog.
- missed-event repair.
- reports.
- compatibility polling for external no-webhook sources.

Do not present cron as the primary workflow engine.

## Phase backlog

### Phase 0 — Contract/schema design

Tasks:

1. Event store / fact source location is decided: `~/.hermes/control_tower.db`.
2. Define command/event/evidence/workflow_run/worker_run/policy_decision schemas inside the Control Tower Ledger.
3. Map existing taskpool/Kanban/cron/gateway data into ledger adapters and read projections without allowing them to become competing SSOTs.
4. Define conflict-detection events for adapter mismatches.
5. Define action-level policy matrix.
6. Define closeout evidence rules by risk class.

Acceptance:

- Written schema doc exists.
- Ledger is named as the only canonical workflow fact source.
- Every non-ledger store is labeled adapter/projection/external context.
- No lifecycle mutation code lands before schema review.
- Projection sources are explicit.

Blocked on human:

- UI host choice.
- Initial action levels.
- Integration depth for migration timing, not SSOT ownership.

### Phase 1 — Read-only Control Tower

Tasks:

1. Add read-only backend aggregator endpoint or script.
2. Build Overview projection.
3. Build Taskpool Health projection.
4. Build Worker Runtime read projection.
5. Build Cron Watchdog read projection.
6. Build Evidence list projection.
7. Expose via dashboard plugin/page.

Acceptance:

- Operator can see next dispatchable task.
- Operator can see why a task is blocked.
- Operator can see stale lease/proof failures.
- No write actions exist.

Suggested first technical slice after approval:

- Create a read-only `control_tower_snapshot` command/API that returns JSON with:
  - `generated_at`
  - `taskpool_health`
  - `next_dispatchable`
  - `top_blockers`
  - `worker_runs`
  - `cron_watchdogs`
  - `evidence_summary`

### Phase 2 — Controlled action registry

Tasks:

1. Define action registry.
2. Whitelist L0/L1 actions.
3. Emit audit events for actions.
4. Add copy-CLI-command UI affordance.
5. Add idempotency keys for write actions.

Acceptance:

- Every action maps to known command.
- No arbitrary shell.
- Every action emits audit event.

### Phase 3 — Workflow Map and Gate Inspector

Tasks:

1. Derive workflow graph from task/event/proof state.
2. Implement gate status nodes.
3. Link event timeline and evidence to graph nodes.
4. Show next action and blocked reason.

Acceptance:

- Any selected task has visible path from creation to current state.
- Gate reason is inspectable.

### Phase 4 — Evidence Spine enforcement

Tasks:

1. Implement evidence maturity model.
2. Link evidence to acceptance criteria.
3. Detect stale/conflicting evidence.
4. Block closeout without adequate evidence.

Acceptance:

- `advisory` evidence cannot close tasks.
- Verified/certified evidence is required by risk class.

### Phase 5 — Event-driven advancement

Tasks:

1. Add durable event trigger registry.
2. Wire task.created/status.changed/dependency.met/proof.added/worker.done/certification.pass/human.approved.
3. Add missed-event repair watchdog.
4. Mark polling-only workflows explicitly.

Acceptance:

- Eligible work advances on events.
- Cron remains watchdog/recovery/report only.

### Phase 6 — Multi-model governance

Tasks:

1. Define certification packet schema.
2. Record model votes as events.
3. Require evidence-grounded claims.
4. Store disagreements and non-convergence.
5. Route repeated failures to skill/policy proposals, not memory ghosts.

Acceptance:

- Multi-model PASS without evidence cannot close a task.
- Disagreement is visible and recoverable.

## First implementation slice proposal

After human approval, the lowest-risk code slice is:

1. Add a read-only `control_tower_snapshot` script/CLI endpoint.
2. It shells out to existing read-only taskpool/Kanban/cron/worker commands or imports existing read APIs when safe.
3. It returns schema-versioned JSON.
4. Add focused tests with fixture outputs.
5. Add dashboard page/plugin only after snapshot shape stabilizes.

Why this slice:

- No lifecycle mutation.
- No new gate authority.
- No schema migration required if implemented as aggregation.
- Immediately improves operator visibility.
- Creates stable API for UI later.

## Human decisions required now

Resolved:

1. Event store / fact source:
   - Use `~/.hermes/control_tower.db` as the only canonical Control Tower Ledger.
   - Existing taskpool/Kanban/brain stores are adapters/projections during migration.

Remaining decisions before code/UI implementation:

1. UI host:
   - built-in dashboard page, or
   - plugin under `plugins/control-tower/`, or
   - private profile plugin first.

2. Initial permissions:
   - read-only only, or
   - read-only + L1 maintenance, or
   - include selected L2 lifecycle actions.

3. Integration timing:
   - ledger-first read-only aggregation, then adapters write through command gateway, or
   - immediate write-through command gateway for selected flows.

4. External dependency stance:
   - Hermes-native only for now, or
   - leave optional future Temporal/OpenTelemetry integration hooks.

## Stop condition

Stop implementation at this packet until the remaining human decisions are made. The SSOT ownership is now fixed, but code/schema/UI changes still require confirmation of UI host and initial permission level.
