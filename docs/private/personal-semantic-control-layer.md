# Personal Semantic Control Layer — Long-Term Stable Architecture

Status: implemented candidate; projection-only and default-off
Contract: `SemanticControlLayer.v1`
CLI: `hermes agent-os semantic-schema` and `hermes agent-os semantic-project --input <SemanticControlEnvelope.v1.json> [--evaluation-time <ISO-8601>]`

## Objective model

The system must connect facts, evidence, goals, capabilities, actions, authority, receipts, and revisions into one deterministic semantic projection while preserving every native source of truth. It must answer:

1. What is known, from which source, with what epistemic status?
2. What goal gap exists under fresh trusted observation?
3. Which capability/action could address it, and why was it selected?
4. Which gate blocks it, or why is it only eligible for a separate executor?
5. What exact receipt and post-action observation prove an outcome?
6. What can a source explicitly not prove?

The layer is successful only when identical input plus identical evaluation time and out-of-band trust bindings produce byte-equivalent semantic JSON, every projected node retains its native reference, only bound rows retain a native owner, unbound owner declarations remain explicit projection-owned claims, completion is evidence-bound, and every side-effect/authority flag remains false.

## Non-goals

- It is not a graph database or a second source of truth.
- It does not copy native lifecycle state into an authoritative semantic store.
- It does not infer or grant authority.
- It does not execute capabilities, dispatch messages, arm schedulers, mutate memory, or close tasks.
- It does not treat model confidence, candidate ranking, a process exit, or an execution receipt without readback as proof of goal convergence.
- It does not require RDF, OWL, a vendor ontology platform, or a long-running graph service.

## Stable ownership boundary

| Native owner | Authoritative objects | Semantic layer behavior |
|---|---|---|
| user/profile memory | explicit durable preferences and user facts | projects source-bound assertions only |
| project profile | project facts and conventions | projects scoped assertions only |
| skills/workflows | reusable capability and action contracts | projects capability/action/term nodes |
| mission/taskpool | goals and lifecycle state | projects goals; never writes lifecycle |
| runtime probes | current observations | accepts only exact out-of-band trusted row bindings |
| evidence store | provenance and execution receipts | projects evidence/receipt nodes |
| authority ledger | grants/revocations/budgets | projects gate state; never grants authority |
| semantic projection | gaps, candidates, intentions, evaluations, unbound declarations | rebuildable derived objects only |

Native objects remain authoritative. A row with the exact out-of-band binding may retain its native owner and carries `ownership_status=bound`. An unbound packet or envelope declaration uses `native_owner=semantic_projection`, carries `ownership_status=unverified_claim`, and preserves the declared owner only as `claimed_native_owner`. A trusted row binds only that row: referenced `Source`, `Evidence`, capability, goal, and absent revision-target identities remain projection-owned unless they have their own explicit trust transport. Action contracts have no native-row trust input in v1, so their capability/goal links remain `claims_requires_capability` and `claims_serves`. Derived nodes carry `ownership_status=projection_derived`. None flow back automatically.

## Three connected planes

### Knowledge and evidence

Objects: `SemanticAssertion`, `Source`, `Evidence`, `Observation`, `ClaimType`, `PreferredTerm`, `TermAlias`.

Material relations:

- `Source asserts SemanticAssertion`
- `Source produces Observation`
- `Evidence supports Observation|ExecutionReceipt`
- `SemanticAssertion revises SemanticAssertion`
- `Source claims_cannot_prove ClaimType`
- `Source claims_defines_term PreferredTerm|TermAlias`
- `TermAlias claims_alias_of PreferredTerm`

Proof-boundary and terminology declarations have no trust-binding transport in v1. They remain first-class projection claims, never native negative evidence or canonical terminology. Their query results retain `status=claimed_*`, source provenance, `ownership_status=unverified_claim`, and `claimed_native_owner`.

Packet-local rows remain queryable but do not mint facts or native ownership. Without the exact out-of-band row binding, the projection uses projection-owned nodes plus explicit `claims_*` relations (`claims_assertion`, `claims_revision_of`, `claims_production`, `claims_support`, `claims_observation_of`, `claims_authority_for`, `claims_execution_of`, and `claims_readback`) instead of assertive relations. Only trusted rows may emit `asserts`, `revises`, `observes`, `projects_authority_for`, `reports_execution_of`, or `read_back_by`.

### Capability and action

Objects: `Capability`, `ActionContract`, `ActionCandidate`, `Intention`, `AuthorityGrant`, `Gate`.

Material relations:

- `ActionContract requires_capability Capability`
- `ActionContract serves Goal`
- `ActionCandidate realizes ActionContract`
- `Intention selects ActionCandidate`
- `Intention blocked_by Gate`
- `AuthorityGrant projects_authority_for Capability`

Every candidate and intention has `may_execute=false`. `eligible_for_executor` means only that a separately governed executor may revalidate the exact intention, grant, target, budget, expiry, and action digest.

### Goal and evaluation

Objects: `Goal`, `GoalGap`, `ExecutionReceipt`, `Evaluation`.

Material relations:

- `Observation observes Goal`
- `Goal has_gap GoalGap`
- `ExecutionReceipt reports_execution_of Intention`
- `ExecutionReceipt read_back_by Observation`
- `Evaluation evaluates Goal`

Goal truth and execution truth are distinct:

- A fresh trusted observation may verify the goal without claiming an execution.
- A receipt may verify that an execution occurred but cannot prove convergence without a fresh, exact, trusted post-action observation.
- The reconciler exposes `evaluated_receipt_ref` and `evaluated_post_action_observation_refs`, binding downstream evidence attribution to the exact receipt and only the trusted, fresh, subject/predicate/source-matching readbacks it admitted.

## Contracts

### SemanticControlEnvelope.v1

Exact top-level fields:

- `schema`
- `reconcile_input`: `PersonalSemanticReconcileInput.v1`
- `proof_boundaries[]`: exact `{source_ref, source_owner, cannot_prove[]}` objects
- `term_mappings[]`: exact `{preferred, aliases[], scope, source_ref, source_owner}` objects

`source_owner` must be one of the declared native owners, but envelope declarations are not trust bindings: proof-boundary and term nodes remain projection-owned and preserve that value only as `claimed_native_owner`. Ambiguous aliases fail closed within a scope; the same alias may map differently across scopes. Duplicate proof boundaries fail closed. The nested reconciliation packet retains its existing exact validation, trusted-clock, scope, budget, freshness, authority, and receipt rules. Every out-of-band trust-binding container must be an exact built-in `frozenset`; each member must be an exact built-in string in canonical `trusted-row:<24 lowercase hex>` form. Subclasses and equality-spoofing members fail closed.

### SemanticControlProjection.v1

- `reconciliation`: non-authoritative, read-only reconciliation projection
- `nodes[]`: deterministic graph nodes with native reference, bound or projection ownership, ownership status, and plane
- `edges[]`: deterministic typed relations
- `queries.claim_verdicts`: explicit `claimed_not_proven` declarations with claimed source ownership; never native negative-proof verdicts
- `queries.action_readiness`: selected action/gate projection with `may_execute=false`
- `queries.completion`: separate goal and execution truth, evidence refs, and blockers
- `queries.terminology`: scope-keyed claimed mappings retaining source, claimed owner, and unverified ownership status; never an unconditional canonicalization table
- `projection`: non-authoritative, rebuildable declaration with canonical input/trust-context SHA-256 provenance and evaluation-time binding; native owners are retained only for bound rows
- `boundary`: all authority, execution, external side-effect, writeback, and owner-replacement flags false

## Completion truth table

| Evidence state | Goal status | Execution status |
|---|---|---|
| no fresh trusted observation | not verified | not claimed |
| fresh trusted observation satisfies goal, no receipt | verified | not claimed |
| trusted completed receipt, no exact fresh readback | not verified | uncertain without readback |
| trusted receipt + exact fresh readback still shows gap | not verified | verified not converged |
| trusted receipt + exact fresh converged readback | verified | verified |
| failed/blocked/uncertain exact receipt | not verified | matching terminal status |

When one receipt references multiple admissible post-action readbacks, only the latest `observed_at` wins; `observation_id` is the deterministic tie-breaker. Only that decision-driving readback is exposed in `evaluated_post_action_observation_refs` and execution evidence. The projection cannot emit a completion claim without the evidence path shown above.

## Threat and negative-case model

The implementation must fail closed against:

- packet-local rows claiming their own trust;
- inferred/model assertions claiming to be user facts;
- semantic assertions attempting `authority_effect != none`;
- stale or future observations;
- grant scope/target/side-effect/budget mismatch;
- receipt/intention/action mismatch;
- same-ID row substitution after trust binding;
- malformed trust-binding containers, non-canonical members, or equality-spoofing string subclasses;
- receipt completion without exact post-action observation;
- unrelated or older converged observations masking a failed or later-gapped execution;
- ambiguous term aliases;
- duplicate graph identities with conflicting semantics;
- Python-only, cyclic, over-deep, non-finite, or hostile typed-JSON values in the reconciliation packet.

## Runtime placement

The implementation is a narrow CLI/Python projection surface:

- `hermes_cli/personal_cognitive_reconciler.py`: trusted deterministic goal/action/authority/receipt reconciliation
- `hermes_cli/semantic_control_layer.py`: three-plane graph and fixed query projection
- `hermes_cli/subcommands/agent_os.py`: read-only schema/projection CLI
- `hermes_cli/main.py`: pre-bootstrap `semantic-project` dispatch that bypasses logging, state, cache, and startup-receipt initialization so the public process preserves its no-durable-write contract even under an empty `HERMES_HOME`

The CLI has no authority-neutral way to receive Python `frozenset` trust bindings from JSON, so `semantic-project` intentionally runs in `none_claim_only` mode: all packet rows and envelope declarations remain unbound claims. Trusted projection is available only to an embedding Python caller that supplies exact out-of-band bindings. The CLI never upgrades packet content into trust. By default it evaluates against the current UTC time; `--evaluation-time <timezone-aware ISO-8601>` replays an exact retained input at the same time boundary and therefore reproduces byte-equivalent output.

`semantic-schema` returns a versioned contract descriptor, not JSON Schema. Machine validation is the exact, fail-closed `project_semantic_control` runtime validator exercised by `semantic-project`; the descriptor explicitly reports `json_schema=false` and `machine_validation=project_semantic_control_runtime_validator`.

It adds no model tool, daemon, background worker, provider call, scheduler, database, or prompt mutation. This preserves Hermes prompt caching and the narrow-waist architecture.

## Verification requirements

A candidate is acceptable only with fresh evidence for:

1. focused semantic-layer tests;
2. existing cognitive reconciler regression tests;
3. Agent OS CLI parser/process behavior;
4. deterministic repeated output and unique node/edge identity;
5. exact receipt-reference binding;
6. all boundary flags remaining false;
7. lint/static scan and independent hostile review of the exact candidate;
8. final branch, diff, and test readback after the last source mutation.

Local source/test success does not authorize activation, deployment, push, merge, external dispatch, or any runtime side effect.
