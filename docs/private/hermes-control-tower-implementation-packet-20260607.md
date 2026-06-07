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

### Phase 5 — Evidence-bound closeout authorization projection

Phase 5 implements the minimum safe human-authorization layer before any
execution-capable closeout/write-through adapter exists.

Implemented scope:

1. Add durable `closeout_authorizations` ledger table.
2. Bind each closeout authorization request to an `evidence_review_id` and
   `evidence_state_hash`.
3. Require satisfied evidence-gate reviews before authorization can be requested.
4. Fail closed when evidence is insufficient, stale, conflicting, when the
   referenced evidence review is no longer the current task evidence state, or
   when the evidence state changes between authorization request and decision.
5. Add conservative role matrix:
   - L0/L1: operator
   - L2: owner
   - L3/L4+: admin
6. Add approval TTL (`DEFAULT_CLOSEOUT_AUTH_TTL_HOURS = 24`).
7. Expired closeout authorization cannot be decided; it emits
   `CloseoutAuthorizationExpired` and requires a fresh evidence-bound request.
8. Add single-use/idempotent decision semantics:
   - same decision retry returns existing decision;
   - conflicting second decision fails closed.
9. Keep `closeout_allowed = false` even after approval.
10. Keep all closeout authorization APIs non-executing and ledger-only.

New API routes:

- `POST /api/plugins/control-tower/evidence-gates/{evidence_review_id}/closeout-authorization`
- `POST /api/plugins/control-tower/closeout-authorizations/{closeout_authorization_id}/decision`

New audit events:

- `CloseoutAuthorizationRequested`
- `CloseoutAuthorizationGranted`
- `CloseoutAuthorizationRejected`
- `CloseoutAuthorizationExpired`

Snapshot adds `closeout_authorizations`:

- `mode = human_authorization_projection_only`
- `closeout_execution_enabled = false`
- `required_role_by_risk`
- `ttl_hours`
- `pending_count`
- `expired_count`
- `decided_count`
- recent pending/decided rows

Acceptance:

- Human closeout authorization is durable, evidence-bound, role-checked,
  expiring, and audited.
- Approval still does not close tasks, mutate lifecycle state, execute commands,
  or write through adapters.
- Cron remains watchdog/recovery/report only; the control plane remains
  event-led and ledger-backed.

### Phase 6 — Read-only executor eligibility projection

Phase 6 promotes executor policy from "everything blocked" to a still
non-executing readiness gate for a tiny read-only whitelist. It does not add a
real executor consumer and does not mutate adapters.

Implemented scope:

1. Define first read-only executor command whitelist:
   - `CopyCliCommand`
   - `RefreshProjection`
   - `RunReadOnlyProofCheck`
   - `RunReadOnlySelfCheck`
2. Limit whitelist eligibility to L0/L1 only.
3. Keep generated executor plans as `dry_run_copy_command_only` and
   `execution = not_executed`.
4. `review_executor_plan_policy(...)` now records:
   - `policy_status = read_only_eligible`
   - `allowed_to_execute = true`
   - `writes_enabled = false`
   - `blocked_reason = read_only_executor_whitelisted`
   for whitelisted L0/L1 plans.
5. Non-whitelisted command types remain blocked with
   `command_type_not_in_read_only_executor_whitelist`.
6. Whitelisted commands above L1 remain blocked with
   `risk_level_exceeds_read_only_executor_whitelist`, even after human approval.
7. Snapshot `execution_policy` now reports whitelist/risk boundary plus
   `read_only_eligible_count` and `blocked_count`.

Acceptance:

- Read-only eligibility is durable and visible.
- Eligibility still does not execute the command.
- Eligibility still does not write adapters, close tasks, or mark commands done.
- The next boundary is adding a real executor consumer for the eligible rows.

### Phase 7 — Read-only executor consumer

Phase 7 consumes the Phase 6 `read_only_eligible` policy result for the same tiny
L0/L1 whitelist. This is the first real executor consumer, but it is deliberately
non-mutating outside the Control Tower Ledger: no adapter writes, no task
lifecycle changes, no closeout, and no arbitrary shell.

Implemented scope:

1. Add `read_only_executor_runs` ledger table.
2. Add `execute_read_only_executor_plan(...)`.
3. Require all gates before a run:
   - executor plan exists and is still `planned` / `not_executed`;
   - execution-policy review exists;
   - review is `read_only_eligible` and `allowed_to_execute = true`;
   - `writes_enabled = false`;
   - command is still `proposed`;
   - current command type/risk/payload exactly matches the generated plan tuple;
   - current command type/risk/payload still recomputes to read-only eligible, so
     stale policies fail closed even if the command changes from one L0/L1
     whitelist type to another.
4. Supported command types remain:
   - `CopyCliCommand`
   - `RefreshProjection`
   - `RunReadOnlyProofCheck`
   - `RunReadOnlySelfCheck`
5. A successful run records `ReadOnlyExecutorRunCompleted`, updates the ledger
   command to `executed`, and updates the executor plan to
   `execution = read_only_completed`.
6. The run result contains only bounded read-only summaries; it does not run
   shell commands and does not write adapters.
7. Dashboard API endpoint:
   - `POST /api/plugins/control-tower/executor-plans/{plan_id}/execute-read-only`
8. Snapshot `read_only_executor` reports run count/recent runs and explicitly
   keeps `writes_enabled = false`, `adapter_write_through_enabled = false`, and
   `closeout_execution_enabled = false`.

Acceptance:

- Eligible read-only plans can be consumed exactly once and retried
  idempotently.
- Missing/stale/blocked policy reviews fail closed.
- `tasks` and `adapter_observations` remain unchanged by executor runs.
- Write-through adapter and closeout execution remain outside this phase.

### Phase 8 — Evidence-bound Control Tower ledger closeout

Phase 8 consumes an approved closeout authorization and performs the first
lifecycle-affecting action, but only inside the Control Tower Ledger. This is not
external adapter write-through: taskpool/Kanban adapters remain read-only
projections, no arbitrary shell runs, and rollback/reopen remains a separate
future boundary.

Implemented scope:

1. Add `closeout_execution_runs` ledger table.
2. Add `execute_closeout_authorization(...)`.
3. Require all gates before a closeout execution:
   - closeout authorization exists and is `approved`;
   - actor role still satisfies the authorization's required role;
   - authorization TTL has not expired;
   - current task evidence hash exactly matches the authorization-bound hash;
   - bound evidence-gate review still exists, is `satisfied`, has zero
     stale/conflicting evidence, and matches the same evidence hash;
   - a Control Tower `tasks` row exists and is not already closed.
4. A successful run records `closeout_execution_runs`, appends
   `CloseoutExecutionCompleted`, updates the Control Tower `tasks.status` to
   `closed`, clears `blocked_reason`/`next_action`, and marks the corresponding
   closeout authorization/evidence gate projection as closeout-consumed.
5. Repeating the same authorization execution is idempotent via
   `UNIQUE(closeout_authorization_id)` and IntegrityError recovery.
6. Snapshot `closeout_execution` reports run count/recent runs and explicitly
   keeps `external_adapter_write_through_enabled = false` and
   `rollback_reopen_enabled = false`.
7. Dashboard API endpoint:
   - `POST /api/plugins/control-tower/closeout-authorizations/{closeout_authorization_id}/execute`

Acceptance:

- Approved, fresh, satisfied evidence-bound closeout can close a Control Tower
  task row exactly once.
- Unapproved authorizations fail closed.
- Evidence changes after approval fail closed.
- Adapter observations remain unchanged; no external taskpool/Kanban write occurs.
- Dashboard can trigger the ledger-only closeout endpoint.

### Phase 9 — External adapter write intent projection

Phase 9 advances to the edge of external taskpool/Kanban write-through without crossing it. It prepares an exact, digest-bound external adapter closeout mutation intent after a completed Control Tower Ledger closeout, but the intent remains blocked until adapter mutation contracts and rollback/reopen semantics are explicitly approved.

Implemented scope:

1. Add `external_adapter_write_intents` ledger table.
2. Add `prepare_external_adapter_write_intent(...)`.
3. Require all gates before preparing an intent:
   - a completed `closeout_execution_runs` row exists;
   - the Control Tower task row exists and is already `closed`;
   - actor role is at least `owner`;
   - adapter is in the narrow intent allow-list (`taskpool`, `kanban`);
   - provided `external_ref` matches the task `external_ref` when one is present.
4. The prepared intent stores:
   - adapter name and external ref;
   - operation `closeout`;
   - canonical mutation payload;
   - `mutation_digest`;
   - `writes_enabled = false`;
   - `external_adapter_write_through_enabled = false`;
   - `rollback_required = true`;
   - `rollback_available = false`.
5. A successful preparation appends `ExternalAdapterWriteIntentPrepared` and is idempotent by `(closeout_execution_id, adapter_name, external_ref)`.
6. Snapshot `external_adapter_write_intents` reports intent count/recent intents and explicitly keeps write-through disabled.
7. Dashboard API endpoint:
   - `POST /api/plugins/control-tower/closeout-executions/{closeout_execution_id}/external-write-intent`

Acceptance:

- A completed ledger closeout can produce an exact blocked external write intent.
- The intent records a stable mutation digest and rollback requirement.
- No external adapter row or taskpool/Kanban state is written.
- Missing execution, unsupported adapter, role mismatch, or external ref mismatch fail closed.

### Phase 10 — External adapter write readiness review projection

Phase 10 confirms the best long-term path before external mutation: every write intent must pass a ledger-only readiness review that binds the exact mutation digest to an adapter contract version, but still denies execution because write-through and rollback/reopen are not enabled.

Implemented scope:

1. Add `external_adapter_write_intent_reviews` ledger table.
2. Add `review_external_adapter_write_intent(...)`.
3. Require all gates before recording a review:
   - external write intent exists and is still `blocked_pending_adapter_contract`;
   - actor role is at least `owner`;
   - adapter/operation maps to a v0 contract version such as `taskpool.closeout.v0`;
   - canonical mutation payload recomputes to the stored `mutation_digest`;
   - intent has not unexpectedly enabled writes or adapter write-through.
4. The review records:
   - `adapter_contract_version`;
   - `review_status = blocked`;
   - blocked reasons including `external_write_through_disabled` and `rollback_unavailable`;
   - `write_through_allowed = false`;
   - `writes_enabled = false`;
   - `external_adapter_write_through_enabled = false`.
5. A successful review appends `ExternalAdapterWriteIntentReviewed` and is idempotent by `external_write_intent_id`.
6. Snapshot `external_adapter_write_intent_reviews` reports review count, blocked count, and zero write-through-allowed count.
7. Dashboard API endpoint:
   - `POST /api/plugins/control-tower/external-write-intents/{external_write_intent_id}/review`

Acceptance:

- A digest-bound external write intent can be reviewed into a blocked readiness projection.
- Digest drift, unsupported actor, missing intent, or malformed source fail closed.
- Review keeps external adapter rows unchanged and write-through disabled.
- The final boundary remains real external write execution plus rollback/reopen semantics.

### Phase 11 — External adapter write authorization projection

Phase 11 adds a final ledger-only authorization layer for external writes. It lets an admin request and decide an authorization bound to the exact external-write review, adapter contract version, and mutation digest, while still keeping `write_through_allowed = false` because rollback/reopen and real adapter mutation contracts are not implemented.

Implemented scope:

1. Add `external_adapter_write_authorizations` ledger table.
2. Add `request_external_adapter_write_authorization(...)` and `decide_external_adapter_write_authorization(...)`.
3. Require all gates before request:
   - external write review exists and is still blocked;
   - actor role is at least `admin`;
   - current mutation payload recomputes to the review-bound `mutation_digest`;
   - source review/intent have not unexpectedly enabled writes or write-through.
4. Require all gates before decision:
   - authorization exists and is still `requested`;
   - actor role satisfies the authorization `required_role`;
   - authorization is not expired;
   - current intent/review digest still matches the authorization-bound digest.
5. The authorization records:
   - status `requested`, `approved`, `denied`, or `expired`;
   - `required_role = admin`;
   - `write_through_allowed = false`;
   - `writes_enabled = false`;
   - `external_adapter_write_through_enabled = false`;
   - rollback required/unavailable state.
6. Snapshot `external_adapter_write_authorizations` reports requested/approved/denied/expired counts and zero write-through-allowed count.
7. Dashboard API endpoints:
   - `POST /api/plugins/control-tower/external-write-reviews/{external_write_review_id}/authorization`
   - `POST /api/plugins/control-tower/external-write-authorizations/{external_write_authorization_id}/decision`

Acceptance:

- A reviewed external write intent can produce a digest-bound authorization request and admin decision.
- Approval remains a non-executing record and never enables external adapter write-through.
- TTL expiration, role mismatch, missing review, and digest drift fail closed.
- Adapter observations and taskpool/Kanban state remain unchanged.

### Phase 12 — External adapter write execution preflight projection

Phase 12 adds one more non-executing boundary layer: a command-digest preflight for an approved external write authorization. It constructs the exact `ExternalAdapterWriteThrough` command payload and records `command_execution_digest`, but keeps the executor disabled and all write-through flags false.

Implemented scope:

1. Add `external_adapter_write_execution_preflights` ledger table.
2. Add `prepare_external_adapter_write_execution_preflight(...)`.
3. Require all gates before preflight:
   - external write authorization exists and is `approved`;
   - actor role is at least `admin`;
   - authorization has not expired;
   - source authorization, review, and intent still have writes disabled;
   - current mutation payload recomputes to the authorization/review/intent `mutation_digest`.
4. The preflight records:
   - `preflight_status = blocked_pending_external_write_executor`;
   - canonical `command_execution_payload_json`;
   - `command_execution_digest` over that canonical payload;
   - `write_through_allowed = false`;
   - `writes_enabled = false`;
   - `external_adapter_write_through_enabled = false`;
   - `external_write_executor_enabled = false`;
   - rollback required/unavailable state.
5. Snapshot `external_adapter_write_execution_preflights` reports preflight counts and zero write-through-allowed count.
6. Dashboard API endpoint:
   - `POST /api/plugins/control-tower/external-write-authorizations/{external_write_authorization_id}/execution-preflight`

Acceptance:

- An approved external write authorization can produce exactly one command-digest preflight.
- The preflight is idempotent per authorization and recomputable from canonical JSON.
- Role mismatch, missing authorization, non-approved authorization, expired authorization, and digest drift fail closed.
- Adapter observations and taskpool/Kanban state remain unchanged.
- The next boundary remains enabling a real external write executor plus rollback/reopen semantics.

### Phase 13 — Digest-bound Kanban external write-through executor

Phase 13 crossed the first external adapter mutation boundary for a narrow, explicitly approved Kanban closeout write. Phase 14 subsequently enabled Kanban rollback/reopen for those write runs.

Implemented scope:

1. Add `external_adapter_write_execution_runs` ledger table.
2. Add `execute_external_adapter_write_preflight(...)`.
3. Enable only `adapter_name = kanban` and `operation = closeout`.
4. Require:
   - admin actor role;
   - exact expected command execution digest;
   - existing preflight status `blocked_pending_external_write_executor`;
   - preflight write-through flags still false;
   - source authorization still approved and unexpired;
   - source authorization/review/intent mutation digest still fresh;
   - recomputed command execution digest still equals the preflight digest.
5. Mutate Kanban through `kanban_db.complete_task(...)` only after all gates pass.
6. Insert exactly one idempotent `external_adapter_write_execution_runs` row per preflight.
7. Record `ExternalAdapterWriteExecuted` event and one deduped `adapter_observations` row.
8. Capture `rollback_snapshot_json` with prior Kanban status/result.
9. Dashboard API endpoint:
   - `POST /api/plugins/control-tower/external-write-preflights/{external_write_preflight_id}/execute`
10. Snapshot `external_adapter_write_execution_runs` reports execution counts and recent runs.

Acceptance:

- Approved digest-bound Kanban preflight can execute once and sets the Kanban task to `done`.
- Repeating the same execution is idempotent and returns the existing run.
- Wrong digest, insufficient actor role, unsupported adapter, stale source chain, or payload drift fail closed before adapter mutation.
- Kanban rollback/reopen is covered by Phase 14.
- Taskpool write-through and conflicting evidence override remain outside this phase.

### Phase 15 — Digest-bound taskpool external write-through executor

Phase 15 extends the already approved external write executor from Kanban to a narrow taskpool adapter slice. It consumes the same Phase 12 command-digest preflight and reuses the same authorization/review/intent freshness gates, but mutates only a single taskpool Markdown task frontmatter status from a writable pre-closeout state to `done`. It stores a rollback snapshot and enables digest-bound taskpool rollback/reopen back to the pre-write status.

Implemented scope:

1. Add `taskpool` to `EXTERNAL_WRITE_EXECUTOR_ADAPTERS` for `operation = closeout` only.
2. Resolve taskpool targets under `HERMES_BRAIN_VAULT` / `OBSIDIAN_VAULT_PATH` / `~/Documents/brain` at `05-实践与项目/tasks/{external_ref}.md`.
3. Require a frontmatter-backed task note with an existing `status:` field.
4. Allow closeout only from `review`, `running`, `ready`, `blocked`, or `scheduled`.
5. Write only frontmatter `status: done` plus an auditable HTML comment marker containing Control Tower digest metadata.
6. Record an idempotent `external_adapter_write_execution_runs` row, `ExternalAdapterWriteExecuted` event, and deduped `adapter_observations` row with `adapter_name = taskpool`.
7. Enable rollback/reopen only against completed digest-bound taskpool write runs with the same `command_execution_digest`.
8. Rollback restores the stored pre-write status and appends a `control_tower_external_taskpool_rollback` audit marker.

Acceptance:

- Approved digest-bound taskpool preflight can execute once and sets the task note to `status: done`.
- Repeating the same execution is idempotent and returns the existing run.
- Wrong digest, insufficient actor role, unsupported adapter, stale source chain, payload drift, or target drift fail closed before mutation.
- Digest-bound taskpool rollback restores the previous taskpool status.
- Conflicting evidence override remains outside this phase.

## First implementation slice status

Implemented:

1. Read-only `control_tower_snapshot` API via `GET /api/plugins/control-tower/snapshot`.
2. Dashboard plugin host under `plugins/control-tower/`.
3. Schema-versioned JSON snapshot.
4. Kanban projection adapter that records deduped observations in the Control Tower Ledger.
5. Proposed-command registry via `POST /api/plugins/control-tower/commands/propose`.
6. High-risk proposed commands routed to Human Inbox without execution.
7. Focused tests for ledger schema, snapshot, dashboard plugin, adapter observations, command proposals, idempotency, human approval requests, approval decisions, workflow-map validation/projection, executor dry-run planning, read-only executor eligibility projection, read-only executor consumption, evidence recording, and evidence-gate projection.
8. Approval decision endpoint via `POST /api/plugins/control-tower/approvals/{approval_id}/decision`.
9. Ledger-backed workflow map projection with disabled executor boundary.
10. Executor dry-run plan endpoint via `POST /api/plugins/control-tower/commands/{command_id}/executor-plan`.
11. Executor plan projection via snapshot `executor_plans`.
12. Execution-policy review endpoint via `POST /api/plugins/control-tower/executor-plans/{plan_id}/policy-review`.
13. Execution-policy projection via snapshot `execution_policy`.
14. Evidence record endpoint via `POST /api/plugins/control-tower/evidence`.
15. Evidence-gate evaluation endpoint via `POST /api/plugins/control-tower/evidence-gates/evaluate`.
16. Evidence-policy projection via snapshot `evidence_policy`.
17. Closeout authorization request endpoint via `POST /api/plugins/control-tower/evidence-gates/{evidence_review_id}/closeout-authorization`.
18. Closeout authorization decision endpoint via `POST /api/plugins/control-tower/closeout-authorizations/{closeout_authorization_id}/decision`.
19. Closeout authorization projection via snapshot `closeout_authorizations`.
20. Read-only executor run endpoint via `POST /api/plugins/control-tower/executor-plans/{plan_id}/execute-read-only`.
21. Read-only executor projection via snapshot `read_only_executor`.
22. Closeout execution endpoint via `POST /api/plugins/control-tower/closeout-authorizations/{closeout_authorization_id}/execute`.
23. Closeout execution projection via snapshot `closeout_execution`.
24. External adapter write intent endpoint via `POST /api/plugins/control-tower/closeout-executions/{closeout_execution_id}/external-write-intent`.
25. External adapter write intent projection via snapshot `external_adapter_write_intents`.
26. External adapter write readiness review endpoint via `POST /api/plugins/control-tower/external-write-intents/{external_write_intent_id}/review`.
27. External adapter write readiness projection via snapshot `external_adapter_write_intent_reviews`.
28. External adapter write authorization request endpoint via `POST /api/plugins/control-tower/external-write-reviews/{external_write_review_id}/authorization`.
29. External adapter write authorization decision endpoint via `POST /api/plugins/control-tower/external-write-authorizations/{external_write_authorization_id}/decision`.
30. External adapter write authorization projection via snapshot `external_adapter_write_authorizations`.
31. External adapter write execution preflight endpoint via `POST /api/plugins/control-tower/external-write-authorizations/{external_write_authorization_id}/execution-preflight`.
32. External adapter write execution preflight projection via snapshot `external_adapter_write_execution_preflights`.
33. Digest-bound Kanban/taskpool external write execution endpoint via `POST /api/plugins/control-tower/external-write-preflights/{external_write_preflight_id}/execute`.
34. Digest-bound Kanban/taskpool external write execution projection via snapshot `external_adapter_write_execution_runs`.
35. Digest-bound Kanban/taskpool rollback/reopen endpoint via `POST /api/plugins/control-tower/external-write-runs/{external_write_execution_id}/rollback`.
36. Digest-bound Kanban/taskpool rollback/reopen projection via snapshot `external_adapter_write_rollback_runs`.

Still intentionally not implemented:

- No conflicting-evidence human override; evidence changes after authorization fail closed.
- Read-only executor whitelist is now executable only through the bounded
  read-only consumer; it still cannot write adapters or mutate external task lifecycle.
- Closeout execution is limited to Control Tower Ledger task rows and keeps
  `external_adapter_write_through_enabled = false`.
- External adapter write intents are digest-bound blocked projections only;
  they do not write taskpool/Kanban and keep `writes_enabled = false` until the
  adapter contract plus rollback/reopen semantics are explicitly approved.
- External adapter write readiness reviews bind the mutation digest to a v0
  adapter contract projection but still keep `write_through_allowed = false`.
- External adapter write authorizations can be requested/decided by admin role,
  but they remain non-executing and keep `write_through_allowed = false`.
- External adapter write execution preflights bind a canonical command execution
  payload/digest and still keep their own preflight flags disabled.
- Kanban/taskpool closeout write-through is executable only by consuming an approved
  preflight with the exact `command_execution_digest`.
- Kanban/taskpool rollback/reopen is executable only against a completed digest-bound
  write run with the exact same `command_execution_digest`; it uses the stored
  rollback snapshot and restores the pre-write adapter status/result where supported.

Why this boundary:

- The system now records durable operator intent, human decisions, executor dry-run plans, execution-policy reviews, bounded read-only executor runs, evidence entries, evidence-gate closeout projections, evidence-bound closeout authorization decisions, approved Control Tower Ledger closeout executions, blocked external adapter write intents with mutation digests, blocked readiness reviews for those intents, external write authorization decisions, command-digest preflights, digest-bound Kanban/taskpool write execution runs, and digest-bound Kanban/taskpool rollback/reopen runs.
- The ledger remains the only fact source.
- The next step changes authority from "digest-bound adapter closeout write-through with rollback" to "conflicting evidence override / broader adapter operations", which requires explicit evidence override policy and per-operation rollback/escape paths.

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
   - Implemented for Human Inbox approvals: decisions are single-use, same-decision retries are idempotent, conflicting second decisions fail closed, and decisions emit audit events.
   - Implemented for closeout authorizations: requests bind to evidence digest, require satisfied/non-conflicting evidence, enforce role matrix and TTL, expire fail-closed, and keep `closeout_allowed = false` until explicit closeout execution.
   - Implemented for closeout execution: execution rechecks role, TTL, evidence hash freshness, and satisfied/non-conflicting evidence before closing a Control Tower Ledger task row.

2. Executor boundary:
   - Implemented: dry-run/copy-command executor plans may be generated for L0/L1 proposed commands and approved L2+ commands, then reviewed through an execution-policy projection.
   - The projection reports `read_only_eligible`, `allowed_to_execute = true`, `writes_enabled = false`, and `blocked_reason = read_only_executor_whitelisted` only for whitelisted L0/L1 read-only command types.
   - Implemented: eligible rows may be consumed by `execute_read_only_executor_plan(...)`, which records a `read_only_executor_runs` row and `ReadOnlyExecutorRunCompleted` event, then marks the ledger command/plan executed.
   - Non-whitelisted command types and any command above L1 remain execution-blocked.
   - Still undecided for write-through phase: which adapters may be mutated, what rollback/escape path is required per action, and whether command-execution approvals bind to command payload digests.

3. Evidence and closeout boundary:
   - Implemented: evidence can be recorded and evaluated against risk-level maturity requirements and acceptance refs.
   - Evidence-gate projection remains a pre-execution gate; closeout execution requires a separate approved authorization and rechecks evidence freshness.
   - Closeout authorization can be requested/decided as a durable evidence-bound human projection.
   - Closeout execution can close Control Tower Ledger task rows only; external adapter closeout/write-through remains undecided.
   - Still undecided for external closeout-capable phase: which adapter receives the final done/closed mutation, whether conflicting evidence can ever be human-overridden, and what rollback/reopen path is mandatory.

4. Write-through adapter timing:
   - Keep Kanban/taskpool read-only until command executor is stable, or
   - Allow selected commands to write through adapters after approval.

5. Human approval UX:
   - Dashboard-only, Gateway-first with dashboard mirror, or both.

6. External dependency stance:
   - Hermes-native only for now, or
   - leave optional future Temporal/OpenTelemetry integration hooks.

## Stop condition

Stop taskpool write-through and conflicting evidence override implementation here. Proposed commands, Human Inbox requests, approval decisions, workflow-map blocked reasons, dry-run executor plans, read-only executor eligibility projections, bounded read-only executor runs, evidence entries, evidence-gate closeout projections, evidence-bound closeout authorization requests/decisions, Control Tower Ledger closeout executions, digest-bound Kanban closeout write-through, and digest-bound Kanban rollback/reopen are now durable, audited, idempotent where safe, and expiring where required. The next boundary is taskpool adapter write-through plus conflicting-evidence override; do not mutate taskpool or override conflicting evidence until the taskpool mutation contract, conflicting-evidence override semantics, taskpool rollback/escape path, and command-execution approval digest semantics are explicitly implemented and verified.
