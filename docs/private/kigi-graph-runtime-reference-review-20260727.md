# Kigi-CLI → Hermes unified graph runtime reference review

Status: bounded local implementation verified; current-byte independent review pending after evidence-binding hardening
Date: 2026-07-27; protocol amendment and closeout: 2026-07-28
Target repo: `/Users/charles/.hermes/hermes-agent`
Reference only: `/Users/charles/.hermes/runs/reference-repos/Kigi-CLI`
Pinned reference commit: `a07d889ad95f9a27ae79ad0296be9268de87feb1`

## Objective model

### Goal
Raise the 14-point Graph Engineering **material, unified, locally executable** coverage from the measured 66.1% to at least 90%, without conflating this slice with global Agent OS certification.

### Success criteria
1. `WorkflowDefinitionV1` remains the canonical static contract and continues to compile deterministically.
2. A single local runtime consumes the compiled `workflow_execution_plan_v1`; it does not replace the plan with a second unrelated DAG model.
3. Runtime scheduling is dependency-driven: independent ready nodes may fan out; a downstream node starts when its own dependencies finish, not after an unrelated global wave barrier.
4. Runtime supports deterministic edge materialization/reduce, conditional routing, barrier/join semantics, and an independently bound verifier gate.
5. Per-node runtime bindings declare model tier and isolation mode; unsupported/high-risk modes fail closed.
6. The runtime is authority-bounded and default-off. The authority-neutral plan itself remains `authority_granted=false` and `executable=false`; a separate exact local execution grant opens only injected local node runners.
7. Node failures block dependants while independent chains continue. Receipts/readback expose node state, outputs, verification, skipped/blocked/failure state, execution order, and concurrency evidence.
8. Focused tests cover positive and negative cases, and the final 14-point reassessment is >=90% by the previously published equal-weight rubric.

### Constraints / non-goals
- No external dispatch, adapter write, publish/deploy/push, credential handling, cron mutation, or implicit model calls.
- Kigi-CLI is a reference architecture, not a runtime dependency; do not copy its product identity or vendor-specific policy.
- Preserve existing Hermes public behavior and existing `WorkflowDefinitionV1` schema unless a backward-compatible extension is strictly required.
- Do not modify unrelated pre-existing dirty files.
- Do not claim global Agent OS certification from local graph-runtime success.
- Dynamic LLM planning, production worktree merge-back, and external-agent execution are out of scope for this bounded slice; contracts may represent them but execution stays local/injected.

### Current evidence
- `workflow_kernel/workflow_definition.py` validates dependencies, cycles, write ownership, verifier ownership/dependency, authority bounds, loops and stop conditions, but emits `executable=false` and has no executor.
- The local Hermes overlay historically had separate task-pipeline/pattern execution surfaces, but none consumed `workflow_execution_plan_v1`; this slice does not treat them as the canonical runtime.
- Workflow Loop v2 already supplies bounded-evidence loop semantics and local event replay; this slice must compose with, not duplicate or weaken, that boundary.

## Kigi source facts

1. The graph is deterministic orchestration over agentic node loops: agentic behavior lives inside nodes; edges stay deterministic Rust (`graph_tracker.rs:1-14`).
2. Planner output is parsed through a fail-closed static gate; stable topological ordering and content-derived node IDs make plans reproducible (`graph_plan.rs:1-20,158-268`).
3. A terminal whole-objective verifier node is appended by the harness, not left to planner discretion (`graph_plan.rs:308-320`).
4. Parallel nodes run in isolated git worktrees; worker and adversarial verifier form a bounded loop; merge-back is sequential and conflicts fail the node (`AGENTS.md:114-126`, `graph_workers.rs`).
5. Restart is fail-safe: Active→UserPaused and Running/Verifying→Ready; explicit resume is required (`graph_tracker.rs:16-21,272-294`).
6. Budget limits are resumable resource stops, not false failure verdicts (`AGENTS.md:127-134`).
7. Dynamic replan is append-only and bounded; optimizer edits are restricted to pending nodes and cannot mutate completed nodes (`AGENTS.md:135-165`, `graph_optimize.rs:1-12`).
8. Graph and unstructured swarm are separate primitives; shared-tree swarm is explicitly not graph-grade isolation (`AGENTS.md:167-203`).

## Gap matrix / chosen path

| Reference pattern | Hermes gap | Bounded local change | Acceptance evidence |
|---|---|---|---|
| Deterministic graph over node loops | plan and executor disconnected | Add canonical `workflow_graph_runtime.py` consuming plan+definition | exact digest match + focused tests |
| Ready-node scheduling | static waves only | dependency-driven scheduler with bounded thread pool | downstream starts before unrelated slow branch finishes |
| Deterministic edges/reduce | string I/O names only | runtime edge bindings and built-in deterministic reducers | order-independent repeat test |
| Conditional path | pattern helper only | predicate/route binding with explicit skipped state | unselected branch never runs |
| Barrier/final gate | declarative verifier links only | dependency join plus independent verifier enforcement | execute output not accepted without verifier success |
| Node runtime policy | no canonical binding | model tier/isolation/local runner metadata | unsupported modes fail closed |
| Failure continuation | no plan executor | block dependants, continue independent chain | negative concurrency test |
| Receipt/readback | loop shadow only | immutable in-memory result + caller-controlled JSON persistence | schema/readback equality test |

### Viable paths considered
- **A — flip `plan.executable=true`: rejected.** It would collapse static validation into authority and violate existing fail-closed boundaries.
- **B — reuse `TaskPipeline` directly: rejected.** It creates a second execution topology and lacks plan digest/verifier/authority semantics.
- **C — separate exact grant + canonical plan executor: chosen.** It preserves the authority-neutral plan while providing one bounded runtime path.
- **D — port Kigi graph mode wholesale: rejected.** Too broad, Rust/product-specific, and would duplicate Hermes control-plane assets.

## Pre-implementation adversarial review and resolved decisions

Independent review artifact: `/Users/charles/.hermes/runs/kigi-graph-preimplementation-review.json` (`claude-fable-5`, success, 6 turns).

The review identified three P0 holes. They are resolved as follows before implementation:

1. **Runtime-binding integrity boundary.** Runtime bindings are injected call-time and are explicitly outside `definition_digest`/`plan_digest`; no claim will say callable semantics are covered by those digests. A deterministic `binding_digest` covers only serializable binding descriptors (task id, runner key, condition key, reducer name, join policy, model tier, isolation). Callable implementation bytes remain outside the hash and are identified only by exact registry keys. The receipt reports both graph digests and binding digest. No v1 schema is changed.
2. **Conditional skip semantics.** A false condition marks a node `skipped` without calling its runner. Its verifier is also skipped. Default downstream `join_policy=require_success` propagates `skipped`; `join_policy=allow_skipped` becomes ready once all dependencies are terminal and none failed/blocked, and receives only achieved dependency values. Failed/blocked dependencies always block dependants. V1 write-collision validation is unchanged: mutually exclusive branches do not gain shared-write exemptions.
3. **Exact execution grant.** An immutable, atomically consumed process-local grant is bound to `plan_digest`, `definition_digest`, allowed capabilities, `max_side_effect<=local_write`, and max concurrency. Admission recompiles the definition, recomputes the received plan digest, checks both digests and rejects any plan task above the side-effect ceiling before invoking any runner. This slice emits an in-memory receipt only; runtime file receipt writes are not authorized or implemented. Durable cross-process replay protection remains outside this slice.

Additional decisions:
- `waves` are presentation/static-analysis output only; runtime readiness is derived from the canonical `task_bindings[].depends_on` in the received plan. This is not a second graph.
- Verifier execution is single-shot and fail-closed. Bounded retry/recovery remains owned by Workflow Loop v2 and is not duplicated here.
- Receipt has a deterministic core and a separate nondeterministic timing annex. Only the core is digest-compared.
- `model_tier` and `isolation` are policy metadata. Supported local values are admission-validated; they do not imply actual model calls or worktree execution.
- Latch/event-controlled tests, not sleeps, prove dependency-driven streaming and concurrency.

## Verification and convergence contract

Required focused evidence:
- Existing WorkflowDefinition tests remain green.
- New graph-runtime unit tests cover digest/grant mutation and replay, dependency concurrency, deterministic reduce across adversarial completion orders, conditional skip and both join policies, barrier/join, verifier fail-closed, authority ceiling, failure blocking with independent-chain continuation, model/isolation policy rejection, and receipt-core roundtrip.
- Static compilation/import succeeds.
- Git diff is limited to this SSOT plus graph runtime/test files intentionally added or edited.
- Independent current-byte review occurs after the final fix.

## Coverage rubric target

The same 14 equal-weight dimensions are retained and must be re-scored from fresh evidence:

1. Node/edge DAG — 1.0 target.
2. False-dependency removal / non-linear scheduling — 0.75 target; explicit dependency-driven execution is real, but automatic semantic false-edge discovery is not claimed.
3. Node contracts — 1.0.
4. Edge materialization and deterministic reduce — 1.0 runtime target, with the binding-digest limitation disclosed.
5. Parallel fan-out — 1.0.
6. Selective barrier/join — 1.0.
7. Diamond fan-out/reduce/synthesis — 1.0.
8. Runtime routing — 1.0.
9. Independent verifier — 1.0.
10. Isolation/single writer — 0.75; write ownership is enforced and unsupported isolation fails closed, but no worktree executor is claimed.
11. Bounded Loop — 1.0 from the separately verified Loop v2 slice.
12. Model tiering — 0.75; policy is validated, no implicit model routing is claimed.
13. Dependency streaming/backpressure — 1.0 when latch tests prove downstream release independent of unrelated branches and max-workers bounds admission.
14. Dynamic plan generation/save — 0.75 from existing validated asset/planner scaffolding; no autonomous executable LLM planner is claimed.

Target total: `13.0 / 14 = 92.9%`. No dimension may be scored from prose alone: each raised score must cite implementation and a falsification test. Global Agent OS certification remains separately `not_certified` unless independently proven.

## Implemented slice

- `workflow_kernel/workflow_graph_runtime.py`: canonical-plan admission, exact digest/grant checks, dependency-driven bounded concurrency, deterministic reducers, conditional routing, join/skip propagation, verifier acceptance gate, failure isolation and deterministic receipt core.
- `workflow_kernel/__init__.py`: additive public exports and explicit runtime capabilities.
- `tests/workflow_kernel/test_workflow_graph_runtime.py`: 33 collected positive/negative cases, including latch-controlled streaming, forced opposite completion orders, shared-verifier routing/failure propagation, verifier-prerequisite failure, immutable in-flight/copy grant replay, strict-JSON snapshot/TOCTOU isolation, complete grant-shape validation, alternating-getter subclass rejection, local-runtime authority ceiling, all-skipped failure projection and receipt roundtrip.

The executor does **not** claim that arbitrary Python runner callables are OS-sandboxed. It enforces declared plan/grant policy before invocation; a production adapter must separately attest or enforce callable/tool behavior. `isolation="worktree"` therefore fails closed rather than pretending to isolate.

### 2026-07-28 protocol-boundary amendment

- Admission now snapshots the strict-JSON plan/definition plus detached binding values, runner and condition registries before consuming authority. Caller- or runner-side mutation after admission cannot alter dependency scheduling, runner selection or receipt identity. The exact-type grant is read once into an internal candidate and that candidate alone is validated and admitted; subclasses with alternating field access are rejected. Thread-pool bounds and the receipt `grant_id` remain tied to the exact ID registered as consumed even if caller-owned dataclass internals are later tampered with.
- `LocalExecutionGrant` is fully shape-validated before hashing or consumption and is hard-capped at `local_write`. The graph runtime independently rejects plan tasks above that boundary. A `human_gate` string in a definition does not authorize external effects.
- A workflow cannot report `success` unless at least one execute node is independently accepted and every non-skipped execute node is accepted. A graph that executes and accepts nothing reports `failed`, not a false success.
- This graph executor is **not** wired to `workflow_run_event_ledger.py`. The kernel's append-only ledger capability must not be projected as graph-run durability. Unified Event Store composition, `run_id/step_id/attempt_id/effect_id`, lease/fencing, profile-bound persistence, dry-run/diff/approval/apply/compensation and structured reviewer/arbiter receipts remain adapter/protocol work, not hidden scheduler hooks.

## Fresh verification evidence

- Red TDD state: new tests initially failed collection with `ModuleNotFoundError: workflow_kernel.workflow_graph_runtime`.
- Focused graph-runtime tests: `33 passed` on the 2026-07-28 candidate.
- Graph-runtime plus definition compiler: `62 passed` on the 2026-07-28 candidate.
- Entire `tests/workflow_kernel`: `229 passed` on the 2026-07-28 candidate.
- Concurrency/replay suite repeated: `30/30 repeated graph-runtime runs passed`.
- Ruff: `All checks passed!`.
- `py_compile` and `git diff --check`: exit 0; `mypy` was not installed and was explicitly skipped.
- Workflow contract fixture/CI tests: `175 passed`.
- Existing overlay module-pool tests (`/Users/charles/.hermes/scripts`): `6 tests ... OK`.
- Existing overlay workflow-asset-decision tests (`/Users/charles/.hermes/scripts`): `6 tests ... OK`.
- Exact copy/deepcopy grant replay probe: copies retain object identity; two concurrent attempts yield one `success`, one `grant_already_consumed`, and exactly one `inspect` invocation.
- One earlier full-kernel run exposed an unrelated pre-existing multiprocessing race in `test_seed_jsonl_once_is_process_safe_and_never_overwrites_winner`; the exact failed test passed on immediate rerun. This is historical evidence only; the 2026-07-28 current-byte suite passed 229/229.
- E2E linear receipt: `/Users/charles/.hermes/runs/kigi-graph-runtime-e2e-receipt-20260727.json`, status `success`, accepted execute=true, readback digest match=true, core digest `sha256:0bd48b4a4dbf86dfc748cc948e0b4f9aaa4e3a1d8d4651a0f9f6859905b65481`.
- E2E diamond receipt: `/Users/charles/.hermes/runs/kigi-graph-runtime-diamond-receipt-20260727.json`, status `success`, max_in_flight=2, deterministic join `{left:L,right:R}`, core digest `sha256:f2f7f4d3285038d0c1eee2cd84239d54c515948edd202ed692d2b6ef320f8bdf`.

## Kigi-CLI project assessment

### What is strong

1. It gets the core abstraction right: graph orchestration is deterministic while each worker node may remain agentic.
2. It treats verifier, resume, budget pause, optimizer restrictions and worktree merge-back as runtime semantics rather than prompt advice.
3. Static planner gates, stable node IDs and topological ordering provide a stronger control boundary than a free-form multi-agent conversation.
4. Graph mode and shared-tree swarm mode are explicitly separated instead of being marketed as equivalent isolation.

### Maturity and adoption risk

- Independent pinned-source review additionally found that Kigi's verifier read-only boundary is prompt-enforced rather than capability-enforced; its terminal verifier may also repair what it certifies. Hermes must not copy that principal conflation.
- Kigi parallel budgeting is accounted after dispatch rather than reserved before dispatch, so a parallel batch can overshoot before the next scheduling boundary. Hermes should use pre-dispatch reservation for any future real model/tool adapter.
- Kigi's restart-to-ready policy is safe only for idempotent effects; Hermes must pair resume with exact receipts/idempotency checks instead of assuming replay safety.
- Kigi's sequential merge and tracker checkpoint are separate operations, leaving a crash window; any future Hermes worktree adapter needs a reconciler and candidate/commit receipt binding.
- Kigi node identity is primarily slug-derived rather than fully semantic, history snapshots are not an immutable audit ledger, and free-text marker/verdict protocols remain brittle. Hermes should retain content digests, typed receipts, and append-only event evidence.
- Git `HEAD` movement checks do not cover every shared repository state mutation; worktree execution remains outside this local slice.
- GitHub API readback on 2026-07-27: repository created 2026-07-17, 42 stars, Apache-2.0, latest release `v0.7.0` published 2026-07-25. It is fast-moving and too young to treat as a stable dependency.
- The pinned checkout contains 8 selected graph implementation/test files totaling 7,819 Rust lines. The broader tree is very large and contains generated/vendored Chromium/CDP artifacts; raw repository LOC is therefore not a useful maturity metric.
- This review inspected pinned source but did not independently build the complete Rust workspace. Build/runtime claims remain upstream claims unless backed by the cited source invariants.
- Kigi is tightly coupled to its ACP/Codex/TUI/worktree product model. Porting it wholesale would duplicate Hermes authority, loop, ledger and adapter control planes.

### Absorb vs reject

Absorbed: deterministic graph over node loops; static admission; terminal verifier semantics; ready-node scheduling; bounded concurrency; append-only/restricted replan principles; explicit graph-vs-swarm distinction.

Rejected for this slice: Rust/product port; implicit external agents; automatic worktree merge-back; terminal/TUI coupling; shared-tree swarm; any assumption that planner text grants authority.

## Converge matrix

| Dimension | Score | Implementation evidence | Falsification/runtime evidence |
|---|---:|---|---|
| 1. Node/Edge DAG | 1.00 | canonical `task_bindings[].depends_on` consumed directly | full definition + runtime suite |
| 2. Remove accidental barriers | 0.75 | ready-state scheduler ignores presentation waves | latch test proves `synth` starts while unrelated `slow` remains blocked; no semantic redundant-edge optimizer |
| 3. Node contract | 1.00 | `RuntimeTaskBinding`, `TaskContext`, strict registry admission | missing runner/policy tests fail before invocation; non-finite JSON fails closed |
| 4. Edge materialization/reduce | 1.00 | sorted dependency pairs; mapping/list/first/concat reducers; per-edge strict-JSON snapshots | opposite-completion-order and mutable-alias tests |
| 5. Parallel fan-out | 1.00 | bounded `ThreadPoolExecutor` | diamond receipt reports `max_in_flight=2`; repeated suite 30/30 |
| 6. Selective barrier/join | 1.00 | `require_success` and `allow_skipped` | conditional optional branch and verifier-prerequisite tests |
| 7. Diamond | 1.00 | fan-out → join → execute → verify | fresh diamond receipt, deterministic `{left:L,right:R}` and accepted execute |
| 8. Runtime routing | 1.00 | registered condition keys; strict bool result; verifier predicates forbidden | false branch never calls runner/verifier |
| 9. Independent verifier | 1.00 | execute remains pending until all linked verifiers achieve | rejection, shared-verifier, and blocked-prerequisite tests |
| 10. Isolation/single writer | 0.75 | compiler write ownership + runtime isolation admission | `worktree` rejected; no OS/worktree executor claimed |
| 11. Bounded loop | 1.00 | existing Loop v2 program-level runtime remains separate and bounded | existing loop focused suite included in kernel run |
| 12. Model tiering | 0.75 | tier is validated and binding-digested | unsupported tier fails admission; no actual model router claimed |
| 13. Dependency streaming/backpressure | 1.00 | downstream released on own accepted dependencies; `max_workers` bounds admission | latch test plus `max_in_flight` receipts/repetition |
| 14. Dynamic generation/save | 0.75 | existing module pool and asset decision scaffolding | their dedicated 6+6 tests pass; no autonomous authority-granting planner claimed |

Total: **13.0 / 14 = 92.9%** for the scoped local unified-runtime rubric. This matrix does not promote Agent OS global certification.

## 2026-07-28 continuation Spec Gate — durable execution protocol

### Goal and evidence boundary

Compose the approved local graph scheduler with profile-scoped append-only execution evidence. The composition must assign deterministic `run_id` / `step_id` / `attempt_id` / `effect_id` identities before a runner can perform a declared local effect, persist pre-effect intent before submission, preserve an uncertain-effect state when post-effect evidence cannot be durably recorded, and replay the ledger without invoking runners.

### Source facts and boundaries

- `workflow_run_event_v1` is a cross-language coarse run-status contract whose legal path is only `proposed → claimed → running → review → done`; it cannot represent graph failures, skipped/blocked nodes, attempts, effects, fencing, or compensation. This slice will not overload or silently change that v1 schema.
- `workflow_loop_event_v1` is an authority-neutral observed loop projection with task pass/fail and expiry semantics. It cannot represent the graph runtime's skip/join semantics or effect identity, and it intentionally does not execute. This slice will compose with its principles and shared hardened storage primitives rather than pretend its current event vocabulary is graph-complete.
- The graph runtime remains a single-shot local scheduler. Durable evidence does not grant authority; the existing exact `LocalExecutionGrant` remains mandatory and is consumed before any runner submission.
- The hot ledger stores identifiers, policy metadata, reason codes, and evidence digests only—never raw runner output, credentials, or arbitrary external payloads.

### Chosen additive path

1. Add a strict `workflow_execution_event_v1` contract and profile-scoped append-only ledger using `runtime_state` no-follow locking/fsync primitives.
2. Add an exact, snapshotted runtime execution session carrying run/session/lease/fencing identity. Deterministically derive step, attempt, and effect IDs from admitted plan/binding/session bytes.
3. Add a fail-closed scheduler event sink. Persist `run_admitted`, `task_started`, and (for declared local writes) `effect_intent` before runner submission; persist outcome and terminal evidence afterward. Replay must expose an `effect_uncertain` projection when an intent has no durable outcome.
4. Keep the contract extensible for later lease takeover/fencing enforcement and dry-run/approval/apply/compensation states; do not claim those later semantics in this first RED→GREEN slice.

### Negative cases and required evidence

- Missing or half-configured durable session/sink rejects before grant consumption and before runner invocation.
- Caller mutation/subclass getters cannot swap run/session/lease/fencing identity after admission.
- Ledger rejects mixed run, plan, binding, owner, lease, fencing token, broken links, sequence gaps, duplicate-key JSON, torn tails, symlink/hardlink targets, idempotency conflicts, and events after terminal state.
- A durable sink failure before submission invokes no affected runner; a post-effect append failure never reports success and leaves replay evidence as uncertain rather than committed or rolled back.
- Existing graph, loop, run-ledger, and cross-language v1 tests remain green. Independent current-byte review is required after the last fix.

### Explicit non-goals for this slice

No external dispatch, exactly-once-effect claim, automatic retry, lease takeover, heartbeat, compensation execution, dry-run approval, profile migration, schema replacement, push, deploy, or global Agent OS certification.

## 2026-07-28 continuation Converge — implemented execution envelope

The continuation intentionally expanded the original minimum ledger slice before closeout. The additive implementation remains local and preserves `workflow_run_event_v1` unchanged.

### Material claims and implementation evidence

| Claim | Implementation evidence | Negative/runtime evidence |
|---|---|---|
| Durable graph evidence | `workflow_execution_event_ledger.py`, `workflow-execution-event-v1.schema.json`, exact `WorkflowExecutionLedgerSink` admission | arbitrary/in-memory callbacks reject before grant consumption; event creation rejects impossible RFC3339 dates; canonical chain/replay and target tests; partial writes complete in a loop, while incomplete writes and failed outcome fsync roll back to the prior uncertain intent; rejected first event leaves no inode; orphan first success re-fsyncs its directory |
| Stable effect identity | deterministic step/attempt/effect derivation from run, plan, binding, task and side-effect class | identity drift, broken chain, and invalid effect-shape tests fail closed |
| Lease/stale-writer gate | immutable admitted session plus strict RFC3339 clock checks before admission, scheduling, result acceptance and terminal evidence | expired admission does not consume grant; post-intent expiry records `stale_observed`, rejects runner result, and preserves uncertain effect |
| Fencing evidence | fencing token is part of every content-addressed event and immutable replay identity | concurrently opened stale sink cannot append after the newer identity; ledger remains on the newer token |
| Idempotent evidence append | event ID, idempotency key, sequence and previous-event binding under exclusive file lock | concurrent sinks writing the exact same observation persist one event; conflicting same coordinate is rejected |
| Dry-run suppression | `execution_mode=dry_run`, `effect_planned`, `run_dry_run_completed`, planned node/effect projection | local-write runner and dependent verifier are never called; dry-run approval evidence is forbidden |
| Policy-bound apply | approval digest binds run, workflow, plan, bindings and policy; identity schema and replay validate the binding | incomplete or mismatched policy/approval rejects before grant consumption; evidence remains authority-neutral and a fresh exact grant is still required |
| Explicit compensation | `workflow_compensation.py` reads and canonically replays the durable source ledger internally, requires the failed terminal event to bind the receipt core digest, and prepares a separate deterministic graph package bound to source run/core/terminal/effect/task and policy | forged caller snapshots are not accepted by the API; absent or non-derived effects, behavioral receipt containers, terminal receipt mismatch, and uncertain effects reject; package grants no authority; apply requires a new grant/session/ledger/approval and verifier |
| Public API | `workflow_kernel/__init__.py` exports execution ledger, session, approval, replay and compensation entry points | import/static checks are included in the kernel verification |

### Compensation boundary

Compensation is a new run, never a mutation, truncation, or semantic rewrite of the source ledger. Preparation reads and replays the canonical durable ledger rather than accepting a caller-supplied snapshot. It requires the ledger's `run_failed` terminal event to carry the exact failed receipt core digest, matching replay identity, and a canonically derived `observed` effect whose durable task and receipt node are failed. An intent without observed outcome remains uncertain and must be reconciled by a human or a future explicit reconciler; this implementation will not guess whether the side effect happened.

The generated compensation definition and plan are authority-neutral (`authority_granted=false`, `executable=false`). Apply requires a fresh exact `LocalExecutionGrant`, separately admitted execution session, policy-bound approval evidence, a new empty ledger, a bounded runner, and an independent verification runner.

### Verification evidence at current pre-review bytes

- `uv run pytest -q tests/workflow_kernel`: `275 passed in 3.78s`.
- `uv run ruff check workflow_kernel tests/workflow_kernel`: `All checks passed!`.
- Scoped `py_compile` plus `git diff --check`: exit 0.
- Focused execution ledger/runtime/compensation suite after durable compensation source/terminal binding, complete-write looping, rollback-on-incomplete-write/fsync-failure, strict durable-sink admission, timestamp validation, mandatory policy-bound apply, and first-publication hardening: `79 passed in 1.03s`.

These results are pre-independent-review evidence. Any code change after the review digest invalidates the approval and requires a new verification/review round.

### Remaining explicit non-goals

No distributed lease service, claim acquisition, lease renewal/heartbeat, takeover coordinator, cross-ledger global fencing registry, exactly-once side-effect guarantee, automatic effect retry, automatic uncertain-effect reconciliation, external dispatch, worktree merge, push, deploy, or Agent OS global certification is claimed. The current fencing guarantee is scoped to an admitted run identity and its append-only ledger; it is not a distributed lock service.
