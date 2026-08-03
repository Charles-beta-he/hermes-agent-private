# Unified Model-Role Pipeline v1

Status: frozen target / implementation slice 1
Date: 2026-08-03
Owner: Hermes control plane

## Decision

Hermes treats every enabled main-loop input as one governed pipeline projection rather than as a direct model call. The projection separates four failure domains:

1. control plane — understands intent, decomposes work, chooses capabilities, owns authority and convergence;
2. executor — performs domain work through the selected execution engine;
3. reviewer — independently challenges executor output and cites evidence;
4. evidence — deterministic tests/readbacks/artifacts required for acceptance.

The projection is authority-negative. It does not invoke models, run tools, mutate the global model/fallback config, or mark work complete. A later execution adapter must consume the projection under a separate authority grant and emit receipts.

## Frozen default topology

| Surface | Primary executor | Independent reviewer | Typical evidence |
|---|---|---|---|
| code correctness | Claude Code CLI (`claude`, Sonnet 4.6) | Codex GPT-5.6, then DeepSeek/Grok | tests, diff readback |
| UI/visual | Gemini 3.1 Pro, then Flash/Claude | Claude, then Codex | browser readback, screenshot matrix |
| security/logic | DeepSeek, then Claude/Grok | Codex, then Claude/Grok | hostile probe, tests |
| triage | MiMo, then Codex | Codex, then DeepSeek | source readback |
| general | active Codex control model | Claude, then DeepSeek | request-dependent |

The active session model is the first control-plane target, so the current Codex identity stays in control. The frozen default fallback list is a policy projection only; it does not rewrite `fallback_model` or an agent's `_fallback_chain`.

## Invariants

- One role route cannot mutate another role's selection, except that changing the executor must reselect the reviewer when independence would otherwise be violated.
- `blocked_no_independent_reviewer` is relative to the selected executor: an executor identity change re-evaluates the reviewer chain from index zero because those candidates were ineligible, not failed.
- `no_verdict_failover_exhausted` records consumed reviewer attempts and is terminal across executor changes; executor failover must not retry or rewind failed reviewer candidates.
- Executor and reviewer cannot be the same provider/model identity. Provider independence is required by the default policy.
- Automatic model failover is permitted only for allowlisted infrastructure failures or no-verdict failures.
- Test failure, spec mismatch, permission denial, policy denial, and evidence failure return to the orchestrator or block certification; they do not consume a model fallback chain.
- Evidence failure is terminal for the acceptance claim. A model switch cannot turn failed evidence into passing evidence.
- Every route and transition is canonical-JSON SHA-256 bound.
- Route/transition receipts never grant model invocation, tool execution, global config mutation, completion certification, deployment, dispatch, payment, credential entry, or destructive authority.

## Schemas

### `hermes_model_role_policy_v1`

Defines independent chains for the control plane and each surface's executor/reviewer, plus required evidence and authority ceilings.

### `hermes_model_role_route_v1`

Per-input immutable projection containing:

- input identity and source;
- normalized surfaces;
- selected control/executor/reviewer and ordered role-local chains;
- evidence requirements/status;
- authority-negative flags;
- canonical route digest.

### `hermes_model_role_failover_receipt_v1`

Binds previous and successor route digests, failure category/code, changed roles, outcome, and explicit negative assertions for global model mutation and authority expansion.

## Failure taxonomy

Infrastructure/no-verdict allowlist:

- authentication failure;
- provider rate limit or quota exhaustion;
- connection failure or timeout;
- provider crash;
- MCP initialization failure;
- empty output;
- unparseable output.

All unclassified failures fail closed. Task/evidence failures are never infrastructure failover signals.

## Runtime integration

`agent.conversation_loop._run_conductor_intent_router` attaches one `model_role_pipeline` projection whenever the existing conductor intent router is enabled. It derives surfaces from the current input and uses the active agent provider/model as the first control-plane identity. This hook intentionally does not touch the prompt, message history, transport client, fallback index, or fallback chain.

The initial consumer boundary remains advisory-only. Actual specialist invocation is a later, separately authorized slice and must preserve:

- current prompt-cache invariants;
- role-specific credentials and fallback chains;
- independent reviewer identity;
- bounded retries;
- evidence-gated acceptance;
- exact workdir/tool/side-effect ceilings.

## Verification map

| Claim | Test/evidence |
|---|---|
| frozen default topology | `test_default_policy_freezes_*` |
| executor isolation and reviewer reselection | `test_executor_infrastructure_failover_*` |
| task failure does not fail over | `test_executor_task_failure_*` |
| reviewer isolation/self-review exclusion | `test_reviewer_timeout_*` |
| evidence cannot be repaired by model switch | `test_evidence_failure_*` |
| main-loop attachment and no global mutation | `test_enabled_conversation_router_*` |
| deterministic immutable projection | `test_route_compilation_is_deterministic_*` |

## Deferred execution adapter gate

Before activation beyond advisory projection, the next slice must provide all of:

1. a config-gated consumer using the selected execution engine;
2. role-local credential and fallback resolution;
3. exact tool/workdir/permission ceilings;
4. bounded process lifecycle and cancellation;
5. output/evidence receipt persistence;
6. negative tests proving task failures do not trigger infrastructure failover;
7. independent review of current bytes;
8. rollback/readback path.
