# ADR: Hermes Control Tower — Event-driven Agent Workflow Control Plane

Date: 2026-06-07
Status: proposed — awaiting human review before code/schema/API implementation
Scope: Hermes private overlay / future upstreamable architecture

## 1. Context

Hermes already has multiple control surfaces:

- CLI: `hermes chat`, `hermes kanban`, scripts, taskpool operators, cron tooling.
- Web Dashboard: management UI and plugin host.
- Kanban: durable SQLite-backed multi-agent task board.
- Local brain/taskpool: proof-gated task lifecycle, dispatch dry-runs, selfcheck, multimodel certification, worker jobs.
- Gateway: messaging/event ingress and notifications.
- Cron: scheduled jobs and watchdog-like recovery hooks.
- Skills, memory, session search: reusable procedural knowledge, stable facts, and historical context.
- Delegate/subagents and external coding agents: execution workers.

The current surfaces are powerful but operationally fragmented. A human operator often needs to ask several questions separately:

1. What is the next dispatchable task?
2. Why is automation not moving?
3. Which worker owns a task and is the lease stale?
4. Which gate failed: dependency, proof, policy, multimodel, human confirmation, or project profile?
5. Which evidence actually supports closeout?
6. Which actions are safe to auto-run and which need human approval?

The desired long-term direction is not a decorative workflow graph and not a polling loop. It is a stable AgentOps control plane that makes Hermes workflows visible, recoverable, auditable, and safely automatable.

## 2. Decision

Adopt **Hermes Control Tower** as the long-term architecture for visual workflow operations:

> Hermes Control Tower is an event-driven Agent workflow control plane with **one canonical fact source**: the Control Tower Ledger. The ledger is an append-only command/event/evidence/workflow ledger. Taskpool, Kanban, Dashboard, CLI views, worker queues, event streams, and evidence pages are projections or adapters over that ledger, not competing sources of truth.

This ADR establishes the durable architectural contract before UI or execution code is added. It supersedes the earlier split wording of "task state SSOT / causality SSOT / closeout SSOT" with a single-source rule: **all durable workflow facts live in the Control Tower Ledger; specialized surfaces may cache or project them but must not become independent truth.**

## 3. Foundational arguments and references

### 3.1 Workflow management

Core works:

- Wil van der Aalst, Kees van Hee — *Workflow Management: Models, Methods, and Systems*.
- Wil van der Aalst et al. — *Workflow Patterns*.
- OMG — *BPMN 2.0 Specification*.

Implication for Hermes:

A workflow system must support sequence, branching, parallelism, joins, cancellation, waiting, manual tasks, exception handling, compensation, and subflows. A static Kanban board or prompt chain is insufficient. Control Tower must expose multiple projections over the same lifecycle: Kanban, DAG, timeline, state machine, and evidence graph.

### 3.2 Sagas and long-running transactions

Core works:

- Hector Garcia-Molina, Kenneth Salem — *Sagas*.
- Gregor Hohpe, Bobby Woolf — *Enterprise Integration Patterns*.
- Chris Richardson — *Microservices Patterns*.

Implication for Hermes:

Agent workflows are long-running distributed transactions across files, tools, model calls, external APIs, gateway messages, memory writes, and human approvals. Each side-effecting step needs idempotency, retry policy, timeout policy, compensation guidance, and evidence attachment.

### 3.3 Event sourcing and CQRS

Core works:

- Martin Fowler — *Event Sourcing*.
- Greg Young — CQRS writings/talks.
- Eric Evans — *Domain-Driven Design*.
- Vaughn Vernon — *Implementing Domain-Driven Design*.

Implication for Hermes:

Commands express intent, events express facts, and projections serve UI/CLI/query needs. Current state alone cannot explain why a task is blocked or done. Control Tower must keep causality: actor, command, policy decision, event, projection update, evidence.

### 3.4 Distributed systems reliability

Core works:

- Leslie Lamport — *Time, Clocks, and the Ordering of Events in a Distributed System*.
- Diego Ongaro, John Ousterhout — *In Search of an Understandable Consensus Algorithm*.
- Martin Kleppmann — *Designing Data-Intensive Applications*.

Implication for Hermes:

Multi-agent workflows must tolerate duplicate events, reordered messages, stale claims, worker crashes, clock skew, partial failures, and concurrent writes. Leases, heartbeats, optimistic concurrency, idempotency keys, sequence numbers, and replayable events are required primitives.

### 3.5 Observability and evidence

Core works:

- Google — *Dapper: a Large-Scale Distributed Systems Tracing Infrastructure*.
- CNCF — OpenTelemetry specifications.
- Betsy Beyer et al. — *Site Reliability Engineering*.
- Charity Majors et al. — *Observability Engineering*.

Implication for Hermes:

Each task needs a trace context linking workflow run, worker run, tool call, model call, approval, artifact, log, screenshot, and proof. The dashboard must answer why the system is in a state, not only what the state is.

### 3.6 Human supervisory control

Core works:

- Thomas Sheridan — *Supervisory Control of Automation*.
- Parasuraman, Sheridan, Wickens — levels of automation.
- Mica Endsley — situation awareness.
- Lisanne Bainbridge — *Ironies of Automation*.
- Lee & See — trust in automation.
- Ben Shneiderman — *Human-Centered AI*.

Implication for Hermes:

Human approval is not an exception path. It is a first-class workflow primitive. High-risk actions must request explicit confirmation, show evidence, explain the decision boundary, and offer pause/resume/retry/override paths.

### 3.7 LLM agents and evaluation

Core works:

- Yao et al. — *ReAct*.
- Schick et al. — *Toolformer*.
- Karpas et al. — *MRKL Systems*.
- Shinn et al. — *Reflexion*.
- Madaan et al. — *Self-Refine*.
- Park et al. — *Generative Agents*.
- Wu et al. — *AutoGen*.
- Hong et al. — *MetaGPT*.
- Jimenez et al. — *SWE-bench*.
- Zhou et al. — *WebArena*.
- Mialon et al. — *GAIA*.

Implication for Hermes:

Agent outputs are not final truth. Agent steps should be represented as observe -> plan -> act -> collect result -> verify -> reflect -> emit event/evidence. Multi-model review is advisory/certification input; real closeout still requires grounded evidence.

## 4. Architectural contract

### 4.1 Control plane / execution plane split

Control plane owns:

- Workflow definitions and lifecycle state.
- Command admission.
- Policy decisions.
- Human approval gates.
- Event log.
- Projections.
- Evidence requirements.
- Worker assignment and lease recovery.
- Closeout decisions.

Execution plane owns:

- Running agents/workers.
- Tool calls.
- Browser harnesses.
- Test runners.
- External model calls.
- Producing results and evidence.

Workers may submit result/evidence events. Workers must not directly mark tasks done, bypass proof gates, bypass human gates, or silently mutate durable memory.

### 4.2 Single fact source boundary

The long-term SSOT is the **Control Tower Ledger**.

Canonical durable facts in the ledger:

- Task identity, lifecycle state, priority, ownership, dependencies, and risk class.
- Commands admitted or rejected by policy.
- Events that actually occurred.
- Evidence metadata and maturity.
- Workflow definitions, workflow runs, current step, and gate decisions.
- Worker runs, leases, heartbeats, results, and failures.
- Human approval requests and decisions.
- Policy decisions and overrides.
- Projection rebuild checkpoints.

Non-canonical derived surfaces:

- Kanban columns and task cards are projections over ledger task facts.
- Taskpool health, dispatchability, proof status, and next-action views are projections over ledger events/facts.
- Dashboard pages are projections plus controlled command entrypoints.
- CLI output is a projection/command surface, not independent state.
- Event Stream is a view over ledger events, not a separate event database.
- Evidence Spine UI is a view over ledger evidence records plus artifact files.
- Worker queues are projections of eligible ledger state.
- Cron watchdogs only repair/reconcile ledger-derived state; they do not own workflow truth.

External context boundaries:

- Session DB is historical context only, not current workflow state.
- Memory stores stable facts only, never WIP task progress.
- Artifact files may contain large evidence payloads, but the ledger owns their metadata, hash, maturity, and acceptance-criteria links.

Migration rule:

- Existing Kanban/taskpool/brain stores remain adapters during migration. When they disagree with the Control Tower Ledger, the ledger wins; adapters must reconcile from ledger events or produce explicit conflict events for human review.

### 4.3 Command / event / projection model

Commands express intent and pass through policy:

- `CreateTask`
- `TriageTask`
- `CheckEligibility`
- `ClaimTask`
- `ReleaseTask`
- `StartWorker`
- `AttachEvidence`
- `RequestHumanApproval`
- `ApproveAction`
- `RejectAction`
- `RetryStep`
- `CancelRun`
- `ReconcileLease`
- `StartCertification`
- `CloseTask`

Events record facts:

- `TaskCreated`
- `TaskTriaged`
- `DependencyMet`
- `EligibilityChecked`
- `PolicyDecisionRecorded`
- `HumanApprovalRequested`
- `HumanApprovalGranted`
- `HumanApprovalRejected`
- `WorkerClaimed`
- `WorkerHeartbeatRecorded`
- `WorkerCompleted`
- `WorkerFailed`
- `ToolCallStarted`
- `ToolCallCompleted`
- `EvidenceAttached`
- `ProofCheckPassed`
- `ProofCheckFailed`
- `MultiModelCertificationStarted`
- `MultiModelCertificationPassed`
- `MultiModelCertificationFailed`
- `TaskMovedToReview`
- `TaskBlocked`
- `TaskClosed`
- `TaskReopened`
- `RetryScheduled`
- `CompensationStarted`
- `CompensationCompleted`

Projections serve reads:

- Overview projection.
- Kanban projection.
- Workflow map projection.
- Event stream projection.
- Worker runs projection.
- Evidence spine projection.
- Human inbox projection.
- Risk/policy projection.
- Gateway inbox projection.
- Cron watchdog projection.

### 4.4 Event-driven first, cron fallback only

Primary automation triggers:

- `task.created` -> triage.
- `task.triaged` -> eligibility check.
- `dependency.met` -> dispatch decision.
- `worker.done` -> verifier/proof check.
- `proof.added` -> proof/certification readiness.
- `certification.pass` -> review closeout candidate.
- `human.approved` -> resume.
- `status.changed` -> projection refresh and downstream gate checks.

Cron is limited to:

- Watchdog checks.
- Stale lease reconciliation.
- Missed event repair.
- Daily/weekly reports.
- External services without webhook/event source.

A workflow that relies on polling as the primary trigger must be labeled `polling-only` and treated as a temporary compatibility mode.

### 4.5 Evidence spine

Evidence types:

- Command output.
- Test output.
- Browser screenshot/video/DOM trace.
- API response.
- Git diff.
- File hash.
- Deployment/release log.
- Model review.
- Human approval.
- External callback/confirmation.

Evidence maturity:

1. `advisory` — model or human suggestion only; not closeout authority.
2. `observed` — raw output exists; not independently verified.
3. `verified` — command/API/browser/test evidence verifies the claim.
4. `certified` — policy/multimodel/human gate accepted the verified evidence.
5. `release_confirmed` — external/production/SSOT system confirms final state.

Closeout rule:

- No task may move to done without evidence that satisfies its acceptance criteria and risk class.
- `PASS_ADVISORY_ONLY` is not done authority.
- Stale or conflicting evidence blocks closeout until refreshed or explicitly waived by a human approval event.

### 4.6 Human gate and autonomy levels

Action levels:

- L0 read-only: list, inspect, search, view logs.
- L1 low-risk maintenance: refresh, selfcheck, proof-check, comment.
- L2 task lifecycle: claim, release, retry, block, move to review.
- L3 execution: modify code, run tests, call internal API, write memory, spawn worker.
- L4 high-risk side effects: push, deploy, production DB, external closeout, irreversible message, delete, credential access.

Default policy:

- L0/L1 may run automatically.
- L2 requires audit event and optimistic concurrency.
- L3 requires project allowlist and evidence capture.
- L4 requires explicit human approval unless a specific, reviewed policy says otherwise.

Human gate events must include:

- Requested action.
- Risk level.
- Evidence summary.
- Consequence summary.
- Available escape/rollback path if any.
- Approver identity.
- Decision timestamp.

### 4.7 Worker runtime contract

Worker state:

- `assigned`
- `claimed`
- `running`
- `heartbeat`
- `completed`
- `failed`
- `expired`
- `released`

Worker fields:

- `worker_run_id`
- `task_id`
- `workflow_run_id`
- `worker_type`
- `model/provider` when applicable
- `workspace`
- `lease_owner`
- `lease_expires_at`
- `heartbeat_at`
- `result_ref`
- `evidence_refs`
- `exit_reason`

Invariants:

- Every worker claim has a lease.
- Every side-effecting worker command has an idempotency key.
- Every worker result is linked to a trace/event.
- Worker completion is a candidate result, not final task closeout.

## 5. Visual console design

Hermes Control Tower dashboard modules:

1. **Overview** — health, next best action, top blockers, human inbox, recent events.
2. **Workflow Map** — DAG/state-machine projection with current step, gate status, dependencies, and evidence links.
3. **Taskpool Health** — active counts, dispatchability, proof failures, stale leases, next task.
4. **Kanban Board** — human task management projection.
5. **Event Stream** — structured causality timeline.
6. **Worker Runtime** — workers, leases, heartbeats, logs, artifacts.
7. **Evidence Spine** — evidence graph, maturity, stale/conflict markers, closeout readiness.
8. **Human Inbox** — approvals, confirmations, overrides, blocked decisions.
9. **Policy Gates** — policy decisions, denials, required approvals.
10. **Gateway Center** — inbound events, outbound notifications, delivery state.
11. **Cron Watchdogs** — recovery/report/fallback jobs only.
12. **Skills & Memory Governance** — skill hits, memory influence, proposed updates, memory hygiene.

The dashboard must not execute arbitrary shell. Buttons map to a controlled action registry, which emits commands and audit events.

## 6. Alternatives considered

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Pure Kanban dashboard | Simple; builds on existing board | Cannot explain causality, evidence maturity, policy gates, or worker traces | Rejected as insufficient alone |
| n8n-style node editor | Familiar workflow metaphor | Encourages static prompt chains; weak evidence/governance semantics | Rejected as primary architecture; may be a projection later |
| Cron-driven dispatcher | Easy to implement | Violates event-driven preference; duplicate execution risk; weak causality | Rejected as primary trigger; keep as watchdog/fallback |
| Direct dashboard-to-shell actions | Fast prototype | Unsafe, hard to audit, bypasses command/policy model | Rejected |
| Event-driven Control Tower | Durable, auditable, recoverable, fits multi-agent work | Requires schema/contract work before UI | Accepted as long-term direction |

## 7. Phased implementation plan

### Phase 0 — Contract and schema

Deliverables:

- Command schema.
- Event schema.
- Evidence schema.
- WorkflowRun schema.
- WorkerRun schema.
- PolicyDecision schema.
- Projection naming and source mapping.

Exit criteria:

- Human accepts SSOT boundaries and risk policy.
- No code that mutates task lifecycle is added before this contract is reviewed.

### Phase 1 — Read-only Control Tower

Deliverables:

- Overview read model.
- Taskpool health read model.
- Kanban read projection.
- Worker jobs read projection.
- Cron watchdog read projection.
- Gateway status read projection.
- Evidence list read projection.

Exit criteria:

- The UI/CLI can answer: next dispatchable task, blockers, stale leases, proof failures, running workers, and why automation is or is not moving.

### Phase 2 — Controlled action registry

Deliverables:

- Whitelisted actions for L0/L1/L2.
- Audit event for each action.
- Copy-exact-CLI-command affordance.
- No arbitrary shell execution.

Exit criteria:

- Refresh/selfcheck/proof-check/reconcile/comment/retry-check actions are auditable and idempotent.

### Phase 3 — Workflow Map and Gate Inspector

Deliverables:

- DAG/state-machine projection.
- Gate nodes for dependency/proof/policy/human/certification.
- Event timeline and evidence links per node.

Exit criteria:

- A human can inspect any task and see the route from creation to current state.

### Phase 4 — Evidence Spine enforcement

Deliverables:

- Evidence maturity model.
- Closeout readiness projection.
- Stale/conflicting evidence detection.
- Proof-check and multimodel certification linkage.

Exit criteria:

- Tasks without adequate verified/certified evidence cannot close automatically.

### Phase 5 — Event-driven advancement

Deliverables:

- Event triggers for task.created, status.changed, dependency.met, proof.added, worker.done, certification.pass, human.approved.
- Cron limited to watchdog/recovery/report.
- Missed-event repair path.

Exit criteria:

- New eligible work advances to the next gate without polling as the primary mechanism.

### Phase 6 — Multi-model governance and self-evolution

Deliverables:

- Model vote/certification event types.
- Evidence-grounded certification packets.
- Disagreement records.
- Skill/memory update proposals with hygiene gates.

Exit criteria:

- Multi-model review is tied to evidence and cannot produce unsupported closeout.

## 8. Non-goals

- Do not replace Kanban; Control Tower consumes and augments it.
- Do not replace the CLI; Control Tower exposes controlled visual actions and exact CLI equivalents.
- Do not make cron the main automation engine.
- Do not implement a generic arbitrary shell dashboard.
- Do not persist WIP progress in memory.
- Do not allow worker-originated direct done transitions.
- Do not require all workflows to be static DAGs; runtime replanning and human gates remain valid.

## 9. Invariants

1. UI is projection, not truth.
2. Control Tower Ledger is the only canonical workflow fact source.
3. Kanban/taskpool/brain stores are adapters or projections during migration, not competing SSOTs.
4. Event Stream, Evidence Spine, Worker Runtime, and Human Inbox are ledger projections.
5. Worker completion is not task completion.
6. High-risk actions require policy and often human approval.
7. Cron is fallback, not primary orchestration.
8. Memory stores stable facts only, not task progress.
9. Every side effect has actor, idempotency key, and evidence metadata in the ledger.
10. Every blocked state has reason and next action in the ledger.
11. Every lease can expire and be reconciled through ledger events.
12. Every projection can be rebuilt from the ledger plus referenced artifacts.

## 10. Required human decisions before implementation

Implementation should stop here until these are answered:

1. **Primary fact source and event store location**
   - Decision: create a dedicated Control Tower Ledger at `~/.hermes/control_tower.db`.
   - The ledger is the only canonical workflow fact source.
   - Existing Kanban/taskpool/brain stores are adapters/projections during migration.
   - Adapter conflicts are represented as ledger conflict events, not silent last-write-wins repairs.

2. **Initial UI host**
   - Option A: built-in Dashboard page.
   - Option B: dashboard plugin under `plugins/control-tower/`.
   - Option C: private overlay plugin first, upstream later.

3. **Taskpool/Kanban unification depth**
   - Option A: read-only aggregation first.
   - Option B: shared command gateway writes both.
   - Option C: migrate toward one unified lifecycle DB.

4. **Human approval UX**
   - Dashboard-only.
   - Gateway-first with Dashboard mirror.
   - Both, with approval tokens and audit events.

5. **Which actions may be enabled in Phase 2**
   - L0/L1 only.
   - Add selected L2 actions.
   - Allow project-policy-gated L3 worker start.

6. **External framework stance**
   - Temporal-compatible concepts only.
   - Optional Temporal integration later.
   - No external workflow engine dependency.

7. **Private-first vs upstream-first**
   - Land as private governance docs and plugin prototype first.
   - Or prepare an upstream-friendly minimal public feature spec.

## 11. Verification plan for this ADR

- Read this file back after write.
- Run `git diff --check`.
- Verify no unrelated worktree changes are staged or committed by this ADR step.
- Stop before schema/code mutations because human decisions above affect blast radius.

## 12. Current status

This ADR is the SSOT for the Hermes Control Tower proposal. The current implementation boundary is documentation and architecture contract only. Code, schema, dashboard plugin, and taskpool lifecycle changes are intentionally blocked pending human review of section 10.
