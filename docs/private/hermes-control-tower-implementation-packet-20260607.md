# Hermes Control Tower implementation packet

Date: 2026-06-07
Status: Phase 1 read-only slice implemented; write actions remain blocked on human/policy review
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

- Initial action levels beyond read-only L0.
- Integration depth for migration timing, not SSOT ownership.

Resolved:

- UI host: bundled dashboard plugin at `plugins/control-tower/`, mounted by the existing dashboard plugin host under `/api/plugins/control-tower/` and tab path `/control-tower`. This reuses the established `manifest.json + plugin_api.py + dist/` pattern used by Kanban, so the UI remains a projection and does not become a fact source.

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

- Implemented in Phase 1 as:
  - `hermes_cli/control_tower.py`
  - `plugins/control-tower/dashboard/plugin_api.py`
  - `GET /api/plugins/control-tower/snapshot`
  - `plugins/control-tower/dashboard/manifest.json`
  - static placeholder UI in `plugins/control-tower/dashboard/dist/`
- The snapshot returns JSON with:
  - `generated_at`
  - `ledger`
  - `ui_host`
  - `adapters`
  - `taskpool_health`
  - `next_dispatchable`
  - `top_blockers`
  - `worker_runs`
  - `cron_watchdogs`
  - `evidence_summary`
  - `adapter_conflicts`
- Current adapter behavior:
  - Observes existing Kanban tasks when `kanban.db` exists.
  - Records deduped `adapter_observations` in the Control Tower Ledger.
  - Emits `TaskObservedFromAdapter` events for newly observed task states.
  - Does not mutate Kanban/task lifecycle state.

### Phase 2 — Controlled action registry

Tasks:

1. Define command proposal registry.
2. Whitelist L0/L1 as proposed-only actions.
3. Emit audit events for proposed actions.
4. Add idempotency keys for proposed actions.
5. Route L2+ proposed commands into Human Inbox instead of executing them.
6. Defer actual lifecycle execution until policy/human approval semantics are reviewed.

Implemented slice:

- `control_tower.propose_command(...)` records operator intent in the `commands` table with `status = proposed`.
- Every proposed command emits a `CommandProposed` event.
- `POST /api/plugins/control-tower/commands/propose` exposes the proposal API through the dashboard plugin.
- Idempotency keys dedupe retries without duplicate events.
- L0/L1 commands remain non-executed proposed intents.
- L2/L3/L4 commands create `human_approvals` rows and emit `HumanApprovalRequested`.
- Snapshot now includes:
  - `command_registry`
  - `human_inbox`

Acceptance:

- Every proposed action maps to a command row.
- No proposed action executes lifecycle side effects.
- No arbitrary shell.
- Every new proposed action emits an audit event.
- High-risk proposals are visible in Human Inbox.

### Phase 3 — Approval lifecycle and workflow map inspector

Tasks:

1. Record approval decisions without executing commands.
2. Enforce single-use approval semantics with idempotent same-decision retries.
3. Emit approval decision audit events.
4. Derive workflow graph/readiness from ledger state.
5. Validate workflow graph node IDs, dependencies, and cycles.
6. Show next action and blocked reason.

Implemented slice:

- `control_tower.decide_human_approval(...)` records `approved` / `rejected` decisions in `human_approvals`.
- Approval decisions emit:
  - `HumanApprovalGranted`
  - `HumanApprovalRejected`
- Repeating the same decision is idempotent and emits no extra event.
- Conflicting second decisions are rejected.
- Underlying commands remain `status = proposed`; approval does not execute the command.
- Dashboard plugin exposes `POST /api/plugins/control-tower/approvals/{approval_id}/decision`.
- Snapshot `human_inbox` now includes:
  - pending approvals
  - recent decided approvals
- OpenSpec-informed workflow map projection added:
  - schema id: `control-tower-command-lifecycle`
  - nodes: `command_proposed`, `approval_requested`, `approval_decided`, `executor_eligible`
  - readiness and blocked reasons are ledger-derived
  - executor eligibility is explicitly blocked with `executor_disabled_phase_boundary`
- Workflow schema validation rejects duplicate node IDs, missing dependencies, and cycles.

Acceptance:

- Human approval decision state is visible and auditable.
- Approval/rejection does not execute lifecycle side effects.
- Any selected command lifecycle has inspectable graph nodes and blocked reason.
- Executor remains disabled until explicit policy approval.

### Phase 4 — Executor dry-run planning, policy projection, and Evidence Spine enforcement

Phase 4A implemented slice:

Tasks:

1. Generate executor plans for eligible proposed commands.
2. Keep executor plans dry-run / copy-command only.
3. Record plan generation as an audit event.
4. Surface plans in the dashboard snapshot.
5. Keep all lifecycle and adapter writes disabled.

Implemented slice:

- `control_tower.generate_executor_plan(...)` creates one idempotent dry-run plan per command.
- Dashboard plugin exposes `POST /api/plugins/control-tower/commands/{command_id}/executor-plan`.
- Executor plans are stored in `executor_plans` with:
  - `mode = dry_run_copy_command_only`
  - `status = planned`
  - `execution = not_executed`
  - `writes_enabled = false`
  - a copyable command preview
- Plan generation emits `ExecutorPlanGenerated`.
- L0/L1 proposed commands can receive dry-run plans without human approval.
- L2+ commands require an approved Human Inbox decision before planning.
- Pending/rejected L2+ commands fail closed.
- Snapshot includes `executor_plans`.
- Underlying command rows remain `status = proposed`; no command is marked executed.

Phase 4B implemented slice:

Tasks:

1. Record an execution-policy projection for a dry-run executor plan.
2. Show whether a plan would be allowed to execute if real execution were enabled.
3. Show adapter write intent and rollback availability before enabling any writes.
4. Keep policy review non-executing and idempotent per plan.
5. Surface policy reviews in the dashboard snapshot.

Implemented slice:

- `control_tower.review_executor_plan_policy(...)` records one idempotent policy review per executor plan.
- Dashboard plugin exposes `POST /api/plugins/control-tower/executor-plans/{plan_id}/policy-review`.
- Policy reviews are stored in `execution_policy_reviews` with:
  - `policy_status = execution_blocked`
  - `allowed_to_execute = false`
  - `writes_enabled = false`
  - `blocked_reason = real_executor_policy_not_enabled`
  - `adapter_writes_json = []`
  - rollback preview marked unavailable because no lifecycle mutation occurs
- Policy review emits `ExecutionPolicyProjectionRecorded`.
- Missing, non-planned, or non-dry-run plans fail closed.
- Snapshot includes `execution_policy` with `real_executor_enabled = false` and `writes_enabled = false`.
- Dashboard UI can review first plan policy, but this only writes the policy projection to the ledger.

Acceptance:

- Executor planning and policy review are visible and auditable.
- Planning and policy review do not execute lifecycle side effects.
- Planning and policy review do not write Kanban/taskpool/adapters.
- Pending/rejected high-risk commands cannot be planned.
- A generated plan only provides a copyable/dry-run command preview.
- A policy review only previews execution authority and rollback/write status; it never grants execution.

Phase 4C implemented Evidence Spine slice:

Tasks:

1. Implement an evidence maturity model.
2. Link evidence to acceptance criteria.
3. Detect stale/conflicting evidence.
4. Evaluate closeout readiness without closing tasks.
5. Surface evidence policy reviews in the dashboard snapshot.

Implemented slice:

- `control_tower.record_evidence(...)` records evidence entries with maturity, producer, source, artifact metadata, stale/conflict status, and acceptance refs.
- Dashboard plugin exposes `POST /api/plugins/control-tower/evidence`.
- Evidence maturity levels are ordered as:
  - `advisory`
  - `observed`
  - `verified`
  - `certified`
  - `release_confirmed`
- Risk-level evidence requirements are currently:
  - L0/L1: `observed`
  - L2: `verified`
  - L3: `certified`
  - L4+: `release_confirmed`
- `control_tower.evaluate_evidence_gate(...)` records one idempotent evidence-gate review per `(task_id, risk_level, acceptance_refs, evidence_state_hash)` so later evidence changes produce a new projection instead of freezing stale gate results.
- Dashboard plugin exposes `POST /api/plugins/control-tower/evidence-gates/evaluate`.
- Evidence-gate reviews are stored in `evidence_gate_reviews` with:
  - `evidence_status = satisfied | insufficient`
  - `required_maturity`
  - acceptance refs / satisfied refs / missing refs
  - stale/conflicting evidence count
  - `closeout_allowed = false`
  - `blocked_reason = insufficient_<maturity>_evidence | closeout_requires_human_boundary`
- Evidence recording emits `EvidenceRecorded`.
- Evidence gate evaluation emits `EvidenceGateEvaluated`.
- Snapshot includes `evidence_policy` with `closeout_execution_enabled = false`.
- Dashboard UI can record sample evidence and evaluate an evidence gate, but this only writes ledger projections; it never closes a task.

Acceptance:

- `advisory` evidence cannot satisfy L1+ closeout gates.
- Stale/conflicting evidence is counted but not accepted as satisfying evidence.
- Verified/certified/release-confirmed evidence is required by risk class.
- Even satisfied evidence keeps `closeout_allowed = false` until the human closeout boundary is explicitly designed.
- No task rows are marked done, and no Kanban/taskpool adapter is mutated.

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

## First implementation slice status

Implemented:

1. Read-only `control_tower_snapshot` API via `GET /api/plugins/control-tower/snapshot`.
2. Dashboard plugin host under `plugins/control-tower/`.
3. Schema-versioned JSON snapshot.
4. Kanban projection adapter that records deduped observations in the Control Tower Ledger.
5. Proposed-command registry via `POST /api/plugins/control-tower/commands/propose`.
6. High-risk proposed commands routed to Human Inbox without execution.
7. Focused tests for ledger schema, snapshot, dashboard plugin, adapter observations, command proposals, idempotency, human approval requests, approval decisions, workflow-map validation/projection, executor dry-run planning, execution-policy projection, evidence recording, and evidence-gate projection.
8. Approval decision endpoint via `POST /api/plugins/control-tower/approvals/{approval_id}/decision`.
9. Ledger-backed workflow map projection with disabled executor boundary.
10. Executor dry-run plan endpoint via `POST /api/plugins/control-tower/commands/{command_id}/executor-plan`.
11. Executor plan projection via snapshot `executor_plans`.
12. Execution-policy review endpoint via `POST /api/plugins/control-tower/executor-plans/{plan_id}/policy-review`.
13. Execution-policy projection via snapshot `execution_policy`.
14. Evidence record endpoint via `POST /api/plugins/control-tower/evidence`.
15. Evidence-gate evaluation endpoint via `POST /api/plugins/control-tower/evidence-gates/evaluate`.
16. Evidence-policy projection via snapshot `evidence_policy`.

Still intentionally not implemented:

- No lifecycle mutation.
- No command execution.
- No task claim/release/move/done/closeout actions.
- No arbitrary shell.
- No write-through Kanban/taskpool adapter.
- No approval expiration / role authorization matrix yet.
- No executor whitelist activation beyond dry-run/copy-command planning and policy projection.

Why this boundary:

- The system now records durable operator intent, human decisions, executor dry-run plans, execution-policy previews, evidence entries, and evidence-gate closeout projections but never executes them.
- The ledger remains the only fact source.
- The next step changes authority from "preview whether a plan would be executable" to "execute an approved plan", which requires explicit policy semantics, role binding, evidence requirements, and write-through adapter decisions.

## Human decisions required now

Resolved:

1. Event store / fact source:
   - Use `~/.hermes/control_tower.db` as the only canonical Control Tower Ledger.
   - Existing taskpool/Kanban/brain stores are adapters/projections during migration.

2. UI host:
   - Use dashboard plugin under `plugins/control-tower/`.

3. Initial permissions through current slice:
   - L0/L1/L2+ are proposal-only.
   - No proposed command executes side effects.
   - L2+ also create Human Inbox approval requests.

Remaining decisions before execution-capable implementation:

1. Approval decision semantics:
   - Implemented for current non-executing slice: decisions are single-use, same-decision retries are idempotent, conflicting second decisions fail closed, and decisions emit audit events.
   - Still undecided for execution-capable phase: approver role matrix, expiration windows, risk-level evidence requirements, and whether approval binds to a command hash/payload digest.

2. Executor boundary:
   - Implemented for current non-executing slice: dry-run/copy-command executor plans may be generated for L0/L1 proposed commands and approved L2+ commands, then reviewed through an execution-policy projection.
   - The projection currently always reports `execution_blocked`, `allowed_to_execute = false`, `writes_enabled = false`, and `blocked_reason = real_executor_policy_not_enabled`.
   - Still undecided for execution-capable phase: which actions may actually run, which adapters may be mutated, and what rollback/escape path is required per action.

3. Evidence and closeout boundary:
   - Implemented for current non-executing slice: evidence can be recorded and evaluated against risk-level maturity requirements and acceptance refs.
   - Evidence-gate projection currently always reports `closeout_allowed = false` even when evidence is satisfied.
   - Still undecided for closeout-capable phase: who can authorize closeout, whether closeout approval binds to evidence digests, how stale/conflicting evidence is remediated, and which adapter receives the final done/closed mutation.

4. Write-through adapter timing:
   - Keep Kanban/taskpool read-only until command executor is stable, or
   - Allow selected commands to write through adapters after approval.

5. Human approval UX:
   - Dashboard-only, Gateway-first with dashboard mirror, or both.

6. External dependency stance:
   - Hermes-native only for now, or
   - leave optional future Temporal/OpenTelemetry integration hooks.

## Stop condition

Stop execution-capable implementation here. Proposed commands, Human Inbox requests, approval decisions, workflow-map blocked reasons, dry-run executor plans, execution-policy projections, evidence entries, and evidence-gate closeout projections are now durable, audited, idempotent where safe, and non-executing. The next boundary is real executor authority plus closeout/write-through adapter policy; do not run any lifecycle-affecting action until the executor whitelist, closeout authorization model, role matrix, expiration/evidence digest rules, rollback/escape path, and adapter mutation contract are explicitly decided.
