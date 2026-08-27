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
- write slices additionally require workspace_path and workspace_base_commit
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
BR-06. A WorkerResult is accepted only for a valid owner, lease/fencing token and matching input digest. A successful proposal must cover the slice's assigned positive/negative claims, reference valid evidence, report every required verification command with exit code zero, propose only its own slice completion and carry a non-empty candidate digest. Exact same-owner duplicate results are idempotently read back only after owner validation.
BR-07. WorkerResult cannot grant authority, mark the task complete, mutate external adapters or rewrite canonical source requirements.
BR-08. Bounded pool capacity is fixed by config. A claim may be released before the child runner starts when the dispatch path records a fresh packet/fence-bound `runner_not_started` receipt; after runner start, every failure remains quarantined until a trusted OS-process supervisor can prove process-group reap and no residual writer.
BR-09. Full Worker output is stored as an artifact/evidence reference; ingress contexts receive only bounded projections.
BR-10. Candidate verification, review and closure bind the same candidate digest; close requires final-candidate-bound evidence and zero-exit verification results.
BR-11. Fresh closure uses a digest-bound closure context packet over original sources/events, current frozen spec, implementation graph, evidence graph, candidate identity and all current revisions; it rejects stale/tampered packets and does not inherit implementation conversation history.
BR-12. `verified` is distinct from worker success, tests-passed, queue-empty and conversation-final.
BR-13. Direct one-turn chat remains available when task admission is not triggered or is explicitly declined.
BR-14. Task admission and observation are side-effect-free with respect to external adapters, active config, scheduler, Taskpool and gateway lifecycle.
BR-15. Cross-channel steering is auditable: every accepted user correction records channel/thread/message/session source identity without storing secrets.
BR-16. Existing prompt caching remains byte-stable for a conversation; dynamic task state enters as bounded turn input or service result, never by mutating prior/system messages.
BR-17. An exact external entrypoint binding is immutable: exact replay is idempotent and cross-task replacement fails closed.
BR-18. A write slice executes only in a clean linked Git worktree whose HEAD matches the packet's workspace_base_commit; post-run HEAD must remain unchanged and the actual Git write set must exactly equal WorkerResult.changed_artifacts.
BR-19. Every durable state_revision has exactly one append-only TaskStateLedger event in the same transaction; events are digest-chained, readback-verified and profile-owned. Legacy databases receive one explicit current-projection anchor and never fabricated history.
BR-20. Every ImplementationGraph slice must map only to frozen spec claims and known source references, carry a non-empty verification contract, collectively cover every closure criterion, and keep owned paths disjoint from forbidden paths.
BR-21. The durable owner identity is explicit when supplied; otherwise it is derived from `HERMES_PROFILE` or the canonical `HERMES_HOME` identity. Two distinct homes sharing a DB must not collapse to the same implicit `default` owner.
BR-22. Natural lease expiry is a durable fail-closed transition: the slice and active packet become quarantined and a digest-chained state event records that termination is unproven. It never makes the slice retryable.
BR-23. WorkerResult uses a closed-world top-level schema; unknown fields are rejected before integration and cannot become an authority side channel.
BR-24. Closure evidence and verification must already be admitted in the current candidate's EvidenceGraph, and every verification command must belong to the frozen ImplementationGraph. Caller-supplied references are not evidence by themselves.
BR-25. A new source event appended after closure invalidates closure, candidate and evidence, fences prior packets/results, resets slices and places the task in `draft`; the stale plan is not claimable until a fresh spec/plan transition.
BR-26. Integrator and closure-verifier identities exposed through the model tool are bound to the runtime-supplied current session; model arguments cannot override those actor identities, and the public tool schema does not advertise such overrides.
BR-27. Once the worker runner has started, every abnormal terminal status, malformed result or workspace readback violation quarantines the claim; injected `worker_terminated`, function return, timeout, cancellation, lease expiry, and ordinary exception status are not trusted termination proof.
BR-28. A claim may be reopened after runner start only with a controller-issued process cleanup attestation bound to exact task, packet, fence and workspace, proving observed process-group termination and residual readback; runner-supplied `worker_terminated`, `runner_returned`, and generic process-exit assertions remain insufficient.
BR-29. A successful managed-workspace result is frozen only after result acceptance into a controller-owned deterministic archive bound to exact owner/packet/fence/source/base/write-set/content digest; workspace reaping requires controller-issued process cleanup and persisted candidate evidence.
BR-30. Managed candidate integration reconstructs an exact Git tree from the verified archive and base commit using an isolated index, creates a deterministic candidate commit and CAS-protected `refs/hermes/candidates/*` ref, verifies the resulting diff write set, requires canonical source HEAD to remain at the bound base commit, and never moves the canonical checkout HEAD.
BR-31. If controller-owned candidate finalization fails after result acceptance, the exact result becomes non-integrable, its packet and slice become quarantined through one append-only audit event, and the managed workspace plus process-cleanup readback are retained for controller repair rather than being released for duplicate execution.

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
N-16. Replaying an existing entrypoint binding must not bump state_revision, and the same entrypoint/session identity must not be silently rebound to another task.
N-17. A missing, dirty, non-linked, path-escaping or wrong-base worker workspace must not start execution.
N-18. A worker must not hide a changed path or move the fenced workspace HEAD by committing its own changes.
N-19. Ledger UPDATE/DELETE, digest/payload tamper, revision gaps, cross-profile reads or mutations, and idempotent replay that appends a second event must fail closed.
N-20. Cross-profile exact WorkerResult replay must not reveal an existing receipt before owner validation.
N-21. Empty/incomplete claims, invalid evidence references, missing/failing verification results, an invalid state delta or an empty candidate digest must not be accepted for integration.
N-22. A timed-out worker without explicit termination proof must not release its slice for a concurrent retry; the slice must remain quarantined.
N-23. An ImplementationGraph must not assign unknown claims/sources, omit a closure criterion or verification command, or overlap owned and forbidden paths.
N-24. Closure must not accept a stale/tampered context packet, evidence or verification bound to a different candidate, or a verifier that participated as worker/integrator.
N-25. Distinct `HERMES_HOME` identities sharing one runtime DB must not read each other's tasks when `HERMES_PROFILE` is absent.
N-26. A WorkerResult with an unknown top-level field such as `authority_granted` must not be accepted.
N-27. Fabricated closure references or commands that never entered the current candidate's EvidenceGraph/frozen plan must not close a task.
N-28. Appending a new source event after closure must not leave the old closure, candidate, evidence or completed slice projection valid, and the stale pre-correction plan must not remain claimable.
N-29. A model/tool caller must not impersonate a fresh verifier or a different integrator by supplying an actor-session argument.
N-30. A started worker that returns cancellation/error, malformed JSON, a moved workspace base or a mismatched write set without termination proof must not release its claim for retry.
N-31. `release_claim` must reject a missing, unknown-field, unsupported-kind, wrong-worker, wrong-packet, wrong-fence, malformed-time or stale `runner_not_started` receipt and must leave the claim fenced; runner-supplied `worker_terminated`, `runner_returned`, and generic process-exit assertions must not make a started claim retryable.
N-32. Missing, duplicate, unexpected, path-escaping, hardlink, special-file, wrong-mode, hash-mismatched or content-digest-mismatched candidate archive members must not integrate.
N-33. A candidate with wrong source/base/ref binding, advanced canonical source HEAD, conflicting candidate ref or post-integration write-set mismatch must not mutate the canonical checkout or be recorded as integrated.
N-34. Candidate archive/finalization failure after accepted result persistence must not leave that result integrable, release the slice for duplicate execution, reap the only recoverable workspace, or omit an append-only quarantine event.

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
| Append-only state history | Revision/event alignment, digest-chain readback, legacy-anchor migration, DB UPDATE/DELETE triggers, tamper and cross-profile negative tests |
| CLI normal-path integration | Parser/unit tests and temporary-HERMES_HOME process E2E |
| Telegram normal-path integration | Real adapter/router/session code path under no-send harness; exact binding tests |
| Cross-channel continuity | One task created through CLI fixture and steered through Telegram fixture with same task_id/revisions |
| Fresh worker isolation | Distinct session IDs, minimal packet input and absent parent transcript proof |
| Isolated write set | Clean linked-worktree and base-commit gates; thread-local cwd; exact actual-vs-declared write-set and worker-commit negative tests |
| Bounded pool | Concurrency/refill/timeout tests with hard deadlines and no residual processes |
| Stale result rejection | Spec/plan/state revision and fencing-token negative matrix |
| Owner/profile isolation | Explicit owner plus `HERMES_PROFILE`/`HERMES_HOME` derivation tests against one shared DB; foreign read and exact replay denial |
| Lease-expiry safety | Natural expiry quarantines slice+packet, appends one digest-chained event and admits no retry without an explicit release path |
| WorkerResult authority boundary | Closed-world field allowlist plus unknown-authority-field rejection |
| Invalidation | Requirement correction invalidates every dependent packet/result/evidence/closure; post-close source append clears closure/candidate/evidence and reopens slices |
| Bounded ingress context | Payload-size/field allowlist tests; full artifacts remain by reference |
| Candidate-bound closure | Candidate mismatch negative test; references must be present in current-candidate EvidenceGraph; commands must be frozen-plan members; fresh closure E2E |
| Runtime actor identity | Tool schema omits integrator/verifier overrides; direct override attempts fail and registry dispatch binds actor identity from runtime `session_id` |
| No side-effect regression | Counters/readbacks remain zero for external sends, config, Mission/Taskpool/Cron and active gateway mutation |
| Existing behavior preserved | Focused CLI, gateway, session, compression, delegation and execution-graph suites |
| Activation complete | Exact running source/config readback, CLI live smoke, Telegram message_id receipt, rollback proof |

### Current candidate evidence ledger (not final closure)

| Scope | Candidate identity | Fresh evidence | Status / boundary |
|---|---|---|---|
| Fresh worker session | `1bb8a8349e26498129403789147d107775b3f087` / tree `1577161f6047ec8d8f8b6158d082179562c96d6f` | distinct forced child session and structured result tests | local code only |
| Bounded pool and process recovery | `618c77fe2757ee6122373f4b4aa711cd7f026d59` / tree `2f53e3d87bfe0f54b1167d4bb8fab033afb3322e` | cap=2 never exceeded; CLI/TG durable cross-process fixture | TG fixture is no-send, not real adapter |
| Stale evidence and ingress conflicts | `8b5939fda15a03b0f3205a07ad3b92dcfff90695` / tree `fa313e9fcefb0c383409d3343204c9777034d85f` | revision invalidation; exact replay idempotency; conflicting event rejection | local code only |
| Lease, tamper and timeout recovery | `4a44619af37c3b29ff8a6adc6dcc62360f67e560` / tree `cf9606011e350d2ca6840c99b7d1a61ca3509355` | fencing rotation/stale-owner negatives; stored packet tamper rejection; terminal receipt mapping | timeout child-exit/reaper proof still open |
| Profile scope and source append-only | `2181164f89b5f76a8a74312200977376f27ccda9` / tree `a22b0d2e0119e3cdcad84a1b00fa80b1734d145d` | profile-scoped default DB; normalized source events; DB UPDATE/DELETE triggers | state-ledger follow-up recorded below |
| Immutable binding and isolated write set | `7276693124429adf2217f56955e568f277a2db2b` / tree `2bfca27c08fbeab58c62618362ba39e92536653e` | `57 passed in 2.58s`; delegate regression `178 passed, 6 subtests`; cross-task rebind, dirty/wrong-base/path escape, hidden write and worker commit negatives | no automatic worktree allocator/reaper; no OS filesystem sandbox |
| TaskStateLedger and explicit owner | `ac7dcee2cb600fecd3a486262854afafea3a5028` / tree `450c8db810042d9ec4029657981fb9fb4e138035`; adversarial reconciliation `488f906d26aebc79c775eb7978c4aec15ba6d218` / tree `55a7710f8bfd7f0406e1ec9b9b5e524d0805db2d` | owner-before-result-replay and non-default legacy-owner migration negatives; every ledger tip binds a canonical digest of the authoritative task/source/binding/slice/packet/result projection; forged closed/candidate projection fails readback; exact closure replay is revision/event/closed-at neutral while conflicting replay fails; runtime focus `74 passed in 2.55s` | predecessor review `deleg_a92bc907` reported four P1: owner replay and owner migration were already fixed in the successor; projection binding and closure idempotency are fixed by `488f906d26`; a fresh exact-successor review is still required |
| Telegram GatewayRunner task ingress | `8c4b3a7c1be4d34368475865a2da16d61fbf4aa7` / tree `3d9ed3ddf76321443eeff54bff934a4cde16c63d` | GatewayRunner `_run_agent` passes exact `platform=telegram` and durable session ID into task ingress; bound task projection is byte-bounded; source event persists; adapter `send` remained zero; `7 passed` gateway file and `10 passed` ingress/process/session focus | no provider call or external send; full platform receive→response-delivery adapter harness remains open |
| Bounded ingress field allowlist | `7735231569e59b7bf84ed2046799967d3fe91f63` / tree `f53eb180bf2ab756057efed50938d308c3be6648` | exact top-level projection key set pinned; original source and current user-message bodies absent; latest combined runtime/gateway focus `68 passed in 2.49s` | full Worker artifacts remain store-only; independent review pending |
| Owner-before-result-replay | `2dabe8ba7abc30c6eb8254edc4d7183995a15395` / tree `cd5ac1777c709d021479ccce7a70267a271f7391` | exact cross-profile replay fails with `task_owner_profile_mismatch`; same-owner replay remains revision/event-neutral; combined focus `69 passed in 2.64s` | local code only |
| Strict WorkerResult and timeout quarantine | `662cc5c85b5aba7ae34151c1928720c2b75d4210` / tree `e91b266b3ac811015b9d89cc79c03770e55d6429`; exception hardening `57dcaf66319f4f57b338e3f5b2c28e950fc65742` / tree `7eaf65894b4053cbb3ea23938cbc227e2321b83c` | success claims/evidence/verification/state-delta/candidate fail-closed matrix; timeout or runner exception without termination proof becomes non-retryable `quarantined`; explicit `worker_terminated=true` permits release; corrected combined focus `73 passed in 2.94s` | hard process termination and residual-writer readback remain open |
| Frozen-plan semantic binding | `d50e53c26579b463de7f02b133772670f383ea39` / tree `b9a9d47a076315154611594ad7945040733d7775` | unknown claim/source, omitted closure/verification and owned-forbidden overlap negatives; failed plan leaves revisions/projection unchanged; selected focus `40 passed in 2.16s` | automatic workspace allocation/reaper remains open |
| Telegram offline delivery rail | `94f002c5c98c67991927e00c7c51daa9f64d8e72` / tree `4aa367abaa1eae1765af5bae89917c50f242769e` | `BasePlatformAdapter._process_message_background` → registered handler → `GatewayRunner._run_agent` → bounded task hydration → one in-memory response delivery; focused `2 passed`, full file `8 passed` | fake adapter and fake Agent; no provider, network or external message; not a live Telegram E2E |
| Candidate-bound fresh closure | `024da41c820a74545dd93a39630407235ca47ffe` / tree `19860d7331d2ce9f5b2eae31ab73f3f7d3903c91` | closure packet binds current sources/spec/plan/state/evidence/candidate; stale packet, candidate mismatch, incomplete claims/evidence/verification and participating worker negatives; corrected combined focus `73 passed in 2.98s` | exact-candidate independent adversarial verdict still required |
| Predecessor P1/P2 closure | `3ba93172c68eeccd81ad97fbbcafbb038a06dd43` / tree `7fa1e4e6432c8822043857b9ae0bbfef943e448e` | distinct implicit-home owners cannot cross-read one shared DB while standard `~/.hermes` preserves legacy `default`; natural lease expiry quarantines and appends a bound event without retry; WorkerResult rejects unknown authority fields; closure accepts only current-candidate EvidenceGraph/frozen-plan evidence; post-close source append clears closure/candidate/evidence, enters `draft` and blocks stale-plan claims; integrator/verifier tool identities are runtime-session-bound; focused related suite `668 passed, 1 skipped in 45.51s` | closes findings from `deleg_50f931cc` in code/tests, but a fresh exact-successor independent review is still required; no hard process kill, workspace allocator/reaper or live activation |
| Started-worker exit fencing | `9c3f9040741997fca084379a18e7b9982433b6b9` / tree `cce973f0ef9d3c1ffaaec2be144e3f3991639ade` | cancellation/error, malformed JSON, moved workspace base and write-set mismatch all quarantine after runner start when termination is unproven; pre-run validation failures remain safely retryable; worker file `15 passed in 1.42s`; related focus `669 passed, 1 skipped in 50.25s` | fail-closed containment only; production hard kill/join and no-residual-writer proof remain open |

Open evidence gaps remain material: automatic workspace lease/reaper lifecycle, hard timeout process termination and residual-writer readback, and independent no-P0/P1 review. The offline Telegram delivery rail now covers the platform base processing and response-dispatch path but deliberately substitutes an in-memory adapter and fake Agent; it is not provider/network/live-delivery evidence. The current-candidate broad `tests/agent --maxfail=20` run is not green (`20 failed, 3491 passed in 162.02s`); the failures are the same cross-cutting clusters previously observed—Copilot ACP, execution-graph governance, file safety, image routing and model metadata—and the run again emitted shared logging/temp-home errors. None of those failing implementation/test files changed in the remediation range `ebc96c9e4a..9c3f904074`, while the related task-runtime focus passed. They are classified as pre-existing/cross-domain evidence debt rather than silently waived, but broad-suite all-green remains false. Therefore this ledger is an implementation checkpoint, not a candidate freeze or activation authorization.

## 8. Converge rule

No completion claim is valid until every BR and N row has fresh code plus test/API/log/readback evidence on one exact candidate tree; all P0/P1 findings are closed by a later candidate-bound review; runtime activation and real CLI/TG readback are separately authorized and verified. Local code passing is not runtime activation.
