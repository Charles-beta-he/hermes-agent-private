# Governed Adaptive Delegation Admission — Phase 0/1 SSOT

schema: hermes_adaptive_delegation_admission_ssot_v1
id: adaptive-delegation-admission-20260810
status: proposed_compute_only_shadow
baseline_head: 94248a19e3a158b8d2c20f98fa811e77d9c783f0

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

## Future phases — not authorized by this SSOT

A future `adaptive_v1` enforcement phase must separately define and certify:

- a versioned `ExpansionProposal` with decomposition rank, children, dependency edges, write/resource ownership, synthesis owner, evidence contract and budget split;
- a tree-wide node/spawn budget distinct from the existing model-call `IterationBudget`;
- global active-node backpressure and structured cancellation;
- deterministic central admission, with descendants unable to widen inherited authority or budgets;
- read-only pilot telemetry with privacy, retention and bounded-cardinality rules;
- exact enforcement/config migration, rollback and runtime readback.

Until those gates exist, `max_spawn_depth` and existing role logic remain the only execution authority, and this document must not be cited as proof that dynamic depth is enabled.
