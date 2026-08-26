# Task-Centric Multi-Session Runtime SSOT

Status: FROZEN-SPEC-DRAFT / LOCAL-IMPLEMENTATION-AUTHORIZED / RUNTIME-ACTIVATION-NOT-AUTHORIZED
Task ID: HTDA-TCMSR-20260826
Spec revision: 1
Baseline commit: c1f9e8d58413fa73bce23c1c95bae65f9571b258
Baseline tree: 34785ff901f702259786b969e6d5af6557120318
Canonical implementation worktree: /Users/charles/.hermes/worktrees/htda-g2-contracts-20260826

Authority declaration:

- This document is the sole canonical specification for `HTDA-TCMSR-20260826`.
- `/Users/charles/.hermes/plans/HTDA-CURRENT.md` is a portfolio/index projection only and cannot amend this specification.
- `/Users/charles/.hermes/docs/private/hermes-self-optimization-ssot-20260526.md` governs the general Hermes self-optimization process and authority boundaries; it does not replace this task-local specification.
- `docs/architecture/session-convergence-governance.md` is a cited architecture reference, not a competing source of task requirements or completion state.
- Git commit/blob identity and an external SHA-256 receipt bind frozen bytes; no self-referential digest is embedded in this file.

## 1. Original-source facts

S-U1 — user report, copied verbatim:

> 还有一个问题似乎一个任务越大，你实现时就越不会去关注任务的细节，似乎是因为长任务上下文太长导致的注意力分散？

S-U2 — active implementation directive, copied verbatim:

> loop推进，直至当前 Hermes 普通 CLI/TG 任务已经完整按这套模型运行。

S-U3 — exact local authority selection, copied verbatim:

> 授权接管现有 dirty HTDA-G2 工作树（高冲突风险）

Source interpretation boundary:

- S-U1 establishes the failure class: long-task detail loss caused by conversation-level attention dilution.
- S-U2 requires implementation and iterative convergence, not architecture prose only.
- “普通 CLI/TG” means the normal CLI and gateway/Telegram request paths must be able to use the runtime; the feature must not be confined to Mission, Cron, a one-off script, or a test harness.
- Small one-turn questions may remain direct. Eligible multi-step/long tasks must use the task runtime through ordinary entrypoints.
- S-U3 authorizes local source/test/document changes and local commits in the canonical worktree. It does not authorize push, merge, deploy, config mutation, gateway restart, live Taskpool/Cron lifecycle writes, or external Telegram sends.

## 2. Objective model

### Success criteria

1. A durable task, not a conversation, owns canonical Spec, Plan, State and Evidence.
2. Ordinary CLI and Telegram ingress can create, bind, resume and steer the same task identity.
3. A conversation/session is a replaceable projection or worker context; destroying or compressing it does not destroy task truth.
4. Worker sessions receive immutable, digest-bound ContextPackets and return schema-validated WorkerResults.
5. A bounded fresh-session pool executes ready slices; no unbounded recursive Agent tree is required.
6. A deterministic controller owns revisions, dependency admission, leases/fencing, idempotency, stale-result rejection, invalidation propagation and terminal-state admission.
7. Full artifacts/evidence remain outside the ingress conversation. CLI/TG receive bounded status projections and references.
8. Completion requires a fresh closure context bound to the same candidate digest and requirement/evidence matrix.
9. Existing prompt-cache stability, message alternation, session resume, compression, direct-chat behavior and channel delivery contracts remain intact.
10. Local and runtime evidence prove both positive behavior and negative boundaries before activation.

### Constraints

- Preserve public CLI and gateway behavior unless an additive command/field is explicitly introduced.
- Keep the Agent core narrow; prefer a domain runtime/service plus thin ingress integration.
- No mid-conversation system-prompt mutation or history rewriting.
- No new model tool unless an existing tool/CLI/service boundary cannot satisfy the concrete consumer.
- No automatic external side effects from task classification, task creation, worker result acceptance or closure.
- No implicit cross-channel task binding from fuzzy recency alone.
- SQLite and filesystem writes must be owner/profile scoped, crash-safe and idempotent.
- Runtime activation remains a later exact authority gate with rollback and fresh readback.

### Non-goals

- Replacing every one-turn chat with a heavyweight task.
- Disabling ordinary conversation resume, compression, relay or delegation globally.
- Treating model consensus, a final-looking answer, an empty queue or Worker success as verified completion.
- Sending a Telegram message, restarting the gateway, changing active config, pushing or deploying during local implementation.
- Building a second parallel Mission/Taskpool product with incompatible lifecycle semantics.

### Current state and evidence

- Existing workflow contracts and workflow kernel provide event-sourced workflow primitives, schemas, replay and closure-related types.
- Existing execution-graph governance bounds model-call graphs and child relay but does not itself make Task the ordinary CLI/TG truth owner.
- Existing session sidecars can carry task IDs but are run summaries, not the authoritative Task SSOT.
- Existing Mission/Taskpool/Cron capabilities are partial producers/consumers and must be reused where semantics match, not presumed equivalent.
- Current live gateway runs from a different clean worktree and is outside this local implementation authority.
- The inherited HTDA-G2 local-requirements-audit contract was frozen as commit c1f9e8d58413fa73bce23c1c95bae65f9571b258 after 448 TypeScript contract tests and 188 Python schema-parity tests passed.

### Viable paths

A. Extend normal ingress with a thin task-runtime service over existing workflow kernel/contracts. Chosen.
B. Route normal requests into Mission/Taskpool by translation. Rejected as primary path until semantic parity is proven; risks lifecycle and authority mismatch.
C. Keep long conversation as owner and add more subagents/compression. Rejected; it preserves the original semantic single point of failure.
D. Build an unrelated external orchestrator. Rejected; duplicates Hermes state, routing and lifecycle ownership.

### Chosen path

Add a profile-scoped task runtime whose persisted canonical objects are independent of sessions. Integrate it through small, cache-stable ingress adapters in CLI and gateway. Reuse existing workflow contracts/kernel where compatible; add only missing contracts and consumers. Implement in reversible, test-first slices and keep rollout default-off until exact activation authorization.

## 3. Canonical domain objects

### TaskRecord

Owns task_id, owner/profile, created source, lifecycle status, current revisions, channel bindings and terminal reason.

### SpecBundle

Owns immutable source references, requirements, negative requirements, constraints, decisions, unknowns and spec_revision. Agent summaries are indexes, never source authority.

### ImplementationGraph

Owns slices, dependencies, write sets, integration order, verification commands and plan_revision. Unknown dependencies default to serial.

### TaskStateLedger

Append-only events plus current projection. Owns state_revision, ready/running/blocked/verified/superseded sets, leases/fencing tokens, idempotency keys and invalidation edges.

### EvidenceGraph

Owns claim-to-evidence relations, artifact identities, verification results, candidate digests, review findings and closure verdicts.

### ContextPacket

Immutable and digest-bound. Required fields:

- packet_id, task_id, spec_revision, plan_revision, state_revision
- slice_id, role, objective
- required_clauses, negative_requirements, source_refs
- architecture_invariants, dependencies, owned_paths, forbidden_changes
- verification_commands, evidence_sink, authority, packet_digest

### WorkerResult

Schema-validated proposal, never direct lifecycle authority. Required fields:

- slice_id, input_digest, claims
- changed_artifacts, evidence_refs, verification_results
- risks, unknowns, state_delta_proposal, candidate_digest

### StatusProjection

Bounded ingress payload:

- task_id, task_status, spec_revision, plan_revision, state_revision
- active_slice_count, blocked_count, verified_count
- current_decisions, latest_candidate_digest, evidence_refs, next_action

## 4. Business rules

BR-01. Every task event is owner/profile scoped and append-only.
BR-02. Every accepted state transition uses optimistic revision/CAS semantics.
BR-03. Channel binding uses exact platform/chat/thread/session identifiers plus task_id; fuzzy recency may suggest but never commit a binding.
BR-04. A task correction creates a new spec revision and deterministically invalidates dependent packets, results, evidence and candidates.
BR-05. A ContextPacket is accepted only when all referenced revisions are current and its canonical digest matches.
BR-06. A WorkerResult is accepted only for a valid lease/fencing token and matching input digest; stale or duplicate results are rejected or idempotently read back.
BR-07. WorkerResult cannot grant authority, mark the task complete, mutate external adapters or rewrite canonical source requirements.
BR-08. Bounded pool capacity is fixed by config; completion/refusal/timeout frees capacity and ready work may refill it.
BR-09. Full Worker output is stored as an artifact/evidence reference; ingress contexts receive only bounded projections.
BR-10. Candidate verification, review and closure bind the same candidate digest.
BR-11. Fresh closure reads original source refs, current frozen spec, candidate identity, evidence matrix and unresolved findings; it does not inherit implementation conversation history.
BR-12. `verified` is distinct from worker success, tests-passed, queue-empty and conversation-final.
BR-13. Direct one-turn chat remains available when task admission is not triggered or is explicitly declined.
BR-14. Task admission and observation are side-effect-free with respect to external adapters, active config, scheduler, Taskpool and gateway lifecycle.
BR-15. Cross-channel steering is auditable: every accepted user correction records channel/thread/message/session source identity without storing secrets.
BR-16. Existing prompt caching remains byte-stable for a conversation; dynamic task state enters as bounded turn input or service result, never by mutating prior/system messages.

## 5. Negative cases

N-01. Restarting CLI or replacing a gateway session must not lose Task Spec/Plan/State/Evidence.
N-02. Telegram message in an unbound thread must not mutate a CLI-created task merely because it is recent.
N-03. A stale spec_revision WorkerResult must not be accepted.
N-04. A duplicate idempotency key must not append a second semantic event.
N-05. An expired/revoked lease or wrong fencing token must not commit a result.
N-06. A spec correction must not leave dependent verified slices or closure verdicts valid.
N-07. Worker prose containing `mark_done`, `authority_granted`, external writes or lifecycle fields must not smuggle authority.
N-08. Queue idle or all workers exited must not imply verified completion.
N-09. Compression/relay/root-turn reset must not mint new Task revisions, pool capacity or graph budget.
N-10. Ingress projection must not inject complete Worker logs or unbounded evidence into the conversation.
N-11. Direct chat must not silently create external messages, Mission rows, Taskpool rows, Cron jobs or active gateway mutations.
N-12. Closure against candidate A must not approve candidate B.
N-13. Task owner/profile A must not read or mutate profile B task state.
N-14. A malformed/corrupt ledger must fail closed with stable errors; it must not reconstruct authoritative state from conversation summaries.
N-15. A task classified as simple must remain promotable later without rewriting original source events.

## 6. Dependency-ordered implementation DAG

G0 — inherited dirty G2 freeze
- Complete local-requirements-audit contract, verify and commit.
- Evidence: commit c1f9e8d58413fa73bce23c1c95bae65f9571b258.

G1 — contracts and storage
- G1.1 TaskRecord/SpecBundle/ImplementationGraph/ContextPacket/WorkerResult schemas.
- G1.2 profile-scoped SQLite task store, migrations, append/replay/CAS/idempotency.
- G1.3 deterministic projections and invalidation graph.

G2 — controller
- G2.1 task admission/create/bind/resume/correct APIs.
- G2.2 dependency admission, lease/fencing and result acceptance.
- G2.3 evidence/candidate binding and closure admission.

G3 — ordinary CLI integration
- G3.1 additive task commands/readback.
- G3.2 normal-turn admission/binding and bounded task projection.
- G3.3 session destruction/resume E2E with persistent task continuity.

G4 — ordinary gateway/Telegram integration
- G4.1 exact channel/chat/thread/session binding.
- G4.2 normal message admission/steering and bounded projection.
- G4.3 no-send adapter harness and session replacement E2E.

G5 — bounded fresh-session execution
- G5.1 immutable packet builder and fresh worker session factory.
- G5.2 bounded pool/refill/timeout/cancel behavior.
- G5.3 structured WorkerResult capture and stale-result rejection.

G6 — fresh closure and convergence
- G6.1 fresh closure context and candidate-bound evidence matrix.
- G6.2 unresolved P0/P1/negative-case gating.
- G6.3 exact candidate freeze, independent adversarial review and amendment loop.

G7 — activation, separately authorized
- Build exact activation packet with source commit/tree, config delta, gateway target, rollback, canary target and readback commands.
- Activate only after exact user grant.
- Verify real CLI continuity and exact allowlisted Telegram canary with delivery receipt/message_id.

## 7. Evidence matrix

| Claim | Required evidence |
|---|---|
| Durable task truth | Store/replay/CAS tests plus process-restart readback |
| CLI normal-path integration | Parser/unit tests and temporary-HERMES_HOME process E2E |
| Telegram normal-path integration | Real adapter/router/session code path under no-send harness; exact binding tests |
| Cross-channel continuity | One task created through CLI fixture and steered through Telegram fixture with same task_id/revisions |
| Fresh worker isolation | Distinct session IDs, minimal packet input and absent parent transcript proof |
| Bounded pool | Concurrency/refill/timeout tests with hard deadlines and no residual processes |
| Stale result rejection | Spec/plan/state revision and fencing-token negative matrix |
| Invalidation | Requirement correction invalidates every dependent packet/result/evidence/closure |
| Bounded ingress context | Payload-size/field allowlist tests; full artifacts remain by reference |
| Candidate-bound closure | Candidate mismatch negative test and fresh closure E2E |
| No side-effect regression | Counters/readbacks remain zero for external sends, config, Mission/Taskpool/Cron and active gateway mutation |
| Existing behavior preserved | Focused CLI, gateway, session, compression, delegation and execution-graph suites |
| Activation complete | Exact running source/config readback, CLI live smoke, Telegram message_id receipt, rollback proof |

## 8. Converge rule

No completion claim is valid until every BR and N row has fresh code plus test/API/log/readback evidence on one exact candidate tree; all P0/P1 findings are closed by a later candidate-bound review; runtime activation and real CLI/TG readback are separately authorized and verified. Local code passing is not runtime activation.
