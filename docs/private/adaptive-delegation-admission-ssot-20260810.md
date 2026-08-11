# Governed Adaptive Delegation Admission — Phase 0/1 SSOT

schema: hermes_adaptive_delegation_admission_ssot_v1
id: adaptive-delegation-admission-20260810
status: proposed_compute_only_shadow
baseline_head: 94248a19e3a158b8d2c20f98fa811e77d9c783f0
phase2_successor_base: 373a52f377b618e081cabc630b7a56b938a96dba
phase2_status: candidate_contract_only

## Objective model

Goal: establish the smallest deterministic admission-policy seam needed for future task-shaped delegation depth without changing current delegation behavior, authority, configuration, model-facing schemas, prompts, or runtime outputs.

Current state:

- `delegation.max_spawn_depth` is a static operator ceiling; default and current effective value are 1.
- `role=orchestrator` is authorized only by the existing depth and kill-switch path in `tools/delegate_tool.py`.
- execution-graph model-call capacity already exists through `IterationBudget`; it is not a node/spawn budget.
- no canonical decomposition proposal, dependency DAG, resource-conflict graph, or tree-wide node budget is currently available at the `delegate_task` boundary.

Chosen path: introduce a compute-only deterministic evaluator and invoke it before child construction. Its result is deliberately discarded. Existing hard gates remain the sole authority.

Rejected paths for this slice:

- raising `max_spawn_depth`;
- adding model-facing task/decomposition fields;
- persisting or logging decisions;
- returning a decision in the tool payload;
- changing roles, toolsets, prompts, task counts, background mode, hooks, gateway/TUI state, or graph budgets;
- claiming task-dynamic admission is operational.

## Loop contract

```yaml
loop_contract:
  objective: "land and certify a compute-only, non-interfering admission seam"
  spec_ref: "docs/private/adaptive-delegation-admission-ssot-20260810.md"
  baseline_ref: "94248a19e3a158b8d2c20f98fa811e77d9c783f0"
  acceptance:
    - "pure evaluator is deterministic and data-only"
    - "shadow deny cannot block an existing allowed delegation"
    - "shadow allow cannot bypass pause, depth, concurrency, role, or kill-switch gates"
    - "evaluator failure cannot alter the legacy result"
    - "schema, prompt, config, return payload, hooks, logging and persistence remain unchanged"
  verifier: "focused tests, related regression, diff review, independent current-candidate review"
  budgets:
    max_iterations: 4
    max_repeated_failure: 2
  authority:
    max_side_effect: "local reversible files in the isolated worktree"
    prohibited: ["config mutation", "push", "deploy", "external write"]
  stop_conditions: ["slice certified", "P0/P1 blocker", "budget stop", "human/authority gate"]
```

## Phase 0/1 shadow contract

`shadow-only` means all of the following simultaneously:

```yaml
mode: shadow_only
compute: true
observe_runtime_result: false
persist: false
log: false
return_to_model: false
enforced: false
authority_effect: none
```

This phase is therefore more precisely a **compute-only shadow seam**, not an observability loop. Runtime observation and persistence require a separate versioned contract, privacy/retention policy, bounded sink, and authorization.

### Inputs

The evaluator consumes only already parsed scalar facts:

- normalized requested role;
- prospective child depth;
- current static maximum spawn depth;
- validated task count;
- current per-call maximum child count.

It must not read configuration, environment variables, clocks, UUID/randomness, files, active-process state, credentials, session/workspace identity, goal text, or context text.

### Output

```yaml
schema_version: hermes_adaptive_delegation_admission_shadow_v1
mode: shadow_only
recommendation: shadow_allow | shadow_deny | shadow_abstain
reason_codes: [stable_string]
enforced: false
authority_effect: none
```

Required semantics:

- leaf under existing scalar bounds: `shadow_allow` because no recursive authority is requested;
- task count beyond the provided cap: `shadow_deny`;
- orchestrator at the static depth floor: `shadow_deny`;
- orchestrator below the static ceiling: `shadow_abstain` with `missing_decomposition_contract`;
- child depth beyond the provided static maximum: `shadow_abstain` as a relationally invalid input;
- malformed input: deterministic `shadow_abstain`, never an exception.

The result is advisory data only and must not be used in a runtime branch.

## Existing authoritative gates

The following remain authoritative and unchanged:

1. global spawn pause;
2. parent depth versus `max_spawn_depth`;
3. task count versus `max_concurrent_children`;
4. task-shape and goal validation;
5. role normalization;
6. `_build_child_agent` depth plus `orchestrator_enabled` degradation;
7. toolset stripping/re-addition;
8. background/synchronous ownership and current graph-iteration budget.

A shadow recommendation can neither grant nor remove authority.

## Acceptance and negative evidence

Required tests:

- equal scalar inputs produce exactly equal dictionaries;
- malformed scalar inputs abstain without throwing;
- shadow deny is computed but an otherwise valid child is still built and run;
- evaluator exception is swallowed before child construction and the legacy result still returns;
- shadow allow is never evaluated on pause/depth/task-count rejection paths and cannot bypass them;
- `max_spawn_depth=1` still degrades orchestrator to leaf;
- `orchestrator_enabled=false` still degrades orchestrator to leaf;
- tool schema and returned payload contain no admission/shadow field;
- no new config key, environment variable, hook, log, persistence sink, prompt text, or toolset change.

## Rollout and rollback

Rollout for this slice is source-only in an isolated feature worktree. It does not update the live Hermes config or gateway runtime.

Rollback:

```bash
git restore tools/delegate_tool.py tests/tools/test_delegate.py
git clean -f docs/private/adaptive-delegation-admission-ssot-20260810.md
```

## Phase 2 contract reconciliation — canonical graph reuse

The target base already contains a stronger canonical decomposition boundary than this
Phase 0/1 note originally accounted for:

- `WorkflowDefinitionV1` is the authored decomposition contract;
- `compile_workflow_definition_v1` validates task identity, dependency closure and
  acyclicity, unordered write ownership, authority monotonicity, evidence requirements,
  independent verification, loop bounds, stop conditions and handoff completeness;
- `WorkflowExecutionPlanV1` is its deterministic, digest-bound, authority-neutral
  projection with `authority_granted=false` and `executable=false`.

Those assets are present in the exact `private/main` base used by this successor slice.
They remain the single source of truth for children/tasks, dependency edges,
writes/resources, owner identity, task authority, acceptance evidence and graph rank.
Adaptive delegation must compose with them; it must not introduce a second unrelated
`ExpansionProposal` DAG or copy those fields into a drifting schema.

The only missing Phase 2 object was therefore a narrow delegation-specific admission
envelope. This slice defines `DelegationExpansionProposalV1`, which references an exact
compiled plan and selects:

- `plan_digest` — exact identity of the already validated canonical plan;
- `child_task_ids` — a unique, non-empty subset of plan tasks proposed as one direct
  fan-out frontier;
- `synthesis_task_id` — one distinct plan task transitively downstream of every selected
  child.

The proposal owns no authority and no budget. `requested_child_nodes` is a derived
count of the immediate selected frontier only; it is not a total-tree budget request.
The published Draft 2020-12 projection schema encodes the same count relation with
bounded `oneOf` branches for frontier sizes 1 through 256, so raw JSON Schema
consumers cannot accept a count that contradicts `child_task_ids.length`.
Remaining tree budget, current depth, concurrency, pause state,
kill-switch state, caller capabilities and side-effect ceiling are central admission
context owned by the runtime, never caller-provided proposal fields. Per-child budget is
assigned only after central admission; descendants cannot self-declare or widen it.

A deterministic contract-only validator compiles the definition, binds the proposal
to the resulting plan digest, requires selected children to form an antichain, requires the
synthesis node to be a strict descendant of every child, and emits only a non-executable,
authority-neutral projection. It must not import into or branch the live `delegate_task`
path until tree-wide budget and cancellation gates exist.

Phase 2 non-goals:

- no second graph schema, planner, model call, prompt/schema field or public tool field;
- no runtime registration, child construction, scheduling, execution or persistence;
- no new config/environment key, telemetry, log field or gateway behavior;
- no inference that a valid proposal authorizes delegation;
- no weakening or replacement of existing depth, concurrency, role, pause or
  kill-switch gates.

## Phase 3 contract-only capacity preflight — no reservation

The next smallest non-duplicative seam is `DelegationTreeBudgetPreflightV1`. It
consumes only:

- an already validated `DelegationExpansionProjectionV1`, whose
  `requested_child_nodes` remains derived from its unique immediate child frontier and
  whose `proposal_digest` binds that normalized frontier; and
- a centrally supplied `DelegationTreeBudgetSnapshotV1` containing only an opaque
  `tree_budget_id`, positive `snapshot_revision`, and bounded non-negative
  `remaining_node_capacity`.

Snapshot numeric fields follow JSON Schema number semantics: finite integral
representations are normalized to integers before hashing, while booleans, strings,
fractional or non-finite numbers, unsafe integers, and host-only integer subclasses are
rejected. This preserves Draft 2020-12/Zod/Python parity instead of pretending JSON
Schema can distinguish `1` from `1.0`.

The pure evaluator emits `capacity_fit_at_snapshot` and exactly one corresponding reason
code; the published Draft 2020-12 schema encodes both that cardinality and the fit/reason
relation. The output also carries exact snapshot/proposal bindings. It always emits
`authority_granted=false`, `executable=false`, `reservation_committed=false`, and
`ledger_mutated=false`. It emits no residual capacity, ledger delta, admission token,
runtime handle, or command.

Detached output validators establish structural validity and internal content binding only.
They do not prove that a syntactically valid digest or normalized projection originated from
the canonical Phase 2 compiler or a trusted snapshot source. Source provenance requires a
future source-bound verification operation and must not be inferred from this contract.

This object is intentionally named a preflight rather than a reservation. Two sibling
checks can observe the same immutable snapshot and both fit. Therefore the result is
advisory at that snapshot only; a future central ledger/CAS transaction must revalidate
and atomically commit before any child construction. Phase 3 does not define that
transaction and remains unreachable from live delegation, config, gateway, TUI, hooks,
or `IterationBudget`.

Ownership remains unchanged:

- `WorkflowDefinitionV1` / `WorkflowExecutionPlanV1` own the only decomposition DAG;
- the Phase 2 projection owns immediate child demand and proposal identity;
- `IterationBudget` owns per-agent model-call iterations;
- `tools.delegate_tool` owns current depth, per-call concurrency, spawn pause,
  active-subagent registry, role gates, and targeted interrupt;
- `AIAgent.interrupt()` owns recursive cancellation propagation;
- a separately specified future central ledger owns authoritative tree capacity and
  atomic reservation.

Phase 3 non-goals:

- no runtime admission, reservation, persistence, lock, CAS, telemetry, or logging;
- no total/admitted/active-node accounting model;
- no depth, concurrency, backpressure, cancellation, authority, capability, role, or
  side-effect inheritance;
- no config/environment/public tool/prompt field or gateway/TUI change;
- no claim that tree-budget enforcement or adaptive-depth execution is enabled.

## Future phases — not authorized by this SSOT

A future `adaptive_v1` enforcement phase must separately define and certify:

- central admission context that consumes the inert `DelegationExpansionProposalV1`
  projection while retaining `WorkflowDefinitionV1` / `WorkflowExecutionPlanV1` as the
  only decomposition DAG;
- a tree-wide node/spawn budget distinct from the existing model-call `IterationBudget`;
- global active-node backpressure and structured cancellation;
- deterministic central admission, with descendants unable to widen inherited authority or budgets;
- read-only pilot telemetry with privacy, retention and bounded-cardinality rules;
- exact enforcement/config migration, rollback and runtime readback.

Until those gates exist, `max_spawn_depth` and existing role logic remain the only execution authority, and this document must not be cited as proof that dynamic depth is enabled.
