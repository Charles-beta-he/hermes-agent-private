# DataFlow-Harness → Hermes capability absorption SSOT

Status: implemented / verification in progress
Date: 2026-07-25
Target branch: `codex/workflow-governed-runtime`
Target baseline: `57cb785a9bc41fd4117bd369f60b456e07d687ce`

## 1. Objective model

### Goal

Absorb the reusable control-plane shape from DataFlow-Harness into Hermes as a native, local-only governed workflow artifact path:

```text
current versioned artifact
  → typed incremental mutation
  → schema + graph + authority + stale-state validation
  → append-only atomic commit
  → durable receipt
  → synchronized read-back
  → replayed projection
  → governed runtime input
```

### Success criteria

1. A `WorkflowDefinitionV1` can be initialized as a versioned `WorkflowArtifactV1` without changing the existing definition/runtime contracts.
2. A mutation names the exact artifact, base revision, base digest, idempotency key, and typed operation list.
3. Validation is read-only and fail-closed; it rejects malformed graphs, stale bases, unknown tasks, authority widening, duplicate mutation identities, and schema drift.
4. Apply holds the existing private-ledger lock, replays the current state under the lock, validates the candidate, appends one complete event, `fsync`s it, reads the ledger back, and checks the committed revision/digest.
5. The append-only ledger is the canonical artifact history; status/history are replayed projections, not mutable side stores.
6. The current artifact definition can be consumed by the existing governed runtime compile/run path without a JSON export step.
7. The CLI is default-off for writes: initialize/apply require an explicit local-write acknowledgement. No external action, scheduler action, taskpool lifecycle write, publish, deploy, credential handling, or destructive operation is opened.
8. Fresh tests prove positive flow and negative boundaries: schema, graph, authority, stale revision/digest, idempotency conflict, concurrent writers, torn tail, partial-write rollback, path/symlink safety, receipt/read-back, replay, and runtime consumption.

### Constraints and non-goals

- Preserve existing `WorkflowDefinitionV1`, runtime manifests, execution ledger, CLI behavior, and public APIs.
- Do not import or vendor DataFlow runtime code, WebUI, Skills, or dependencies.
- Do not use full JSON overwrite as the canonical persistence mechanism.
- Do not expose an unauthenticated MCP mutation surface.
- Do not claim the paper's 93.3% as Hermes evidence.
- Do not build a visual editor in this slice. CLI status/history are the first projection; future UIs must consume the same read model and emit typed mutations.
- Do not add automatic natural-language planning. Existing Hermes agent/Skill reasoning produces the typed proposal; the kernel validates and commits it.
- V1 keeps workflow-level authority immutable after initialization. Capability or side-effect widening requires a future separately authorized contract/version.

### Current state and landing seam

The target branch already contains:

- `workflow_kernel/workflow_definition.py`: strict schema and semantic compilation for `WorkflowDefinitionV1`, including DAG, authority, evidence, write-conflict, verification and loop invariants.
- `workflow_kernel/runtime_state.py`: private-root containment, secure file opens, and ledger writer locking.
- `workflow_kernel/workflow_execution_ledger.py`: append-only, replay-validated execution state with idempotency, hash binding, torn-tail detection, short-write handling, rollback and synchronized read-only paths.
- `workflow_kernel/governed_runtime.py`: immutable compile/run identity and runtime adapter admission.
- `hermes_cli/subcommands/workflow_runtime.py`: existing local governed runtime CLI.

The narrow seam is therefore a separate artifact event ledger that reuses those primitives and feeds a replayed current `WorkflowDefinitionV1` into `governed_runtime`; it must not overload the execution ledger or turn immutable runtime manifests into mutable state.

## 2. External source identity and evidence boundary

Reference snapshot root:

`/Users/charles/.hermes/runs/dataflow-harness-reference-review-20260725`

Pinned identities:

- Paper: arXiv `2607.16617v1`, downloaded PDF SHA-256 `3bfceee817b86f79ad48fdcdf0c63f0ee61f9c439fcfc795fc58c524c2e66e4b`.
- `OpenDCAI/DataFlow`: `f62aa1349e0ff14cb737a4cbda1945d04fde85bb`.
- `OpenDCAI/DataFlow-WebUI`: `b64f2b9d4de5792cb9fb3faedef07c0377b9c878`.
- `OpenDCAI/DataFlow-Skills`: `b5c23c1374129cd0121861a669a51cb0ae9fa95d`.

Paper-backed claims:

- The paper defines the NL2Pipeline gap as the difference between generated scripts and persistent, editable, platform-native artifacts.
- It proposes typed incremental mutations, live MCP grounding, Skills for procedural knowledge, validation, atomic commitment, and synchronized conversational/visual projections.
- It reports an observed 93.3% end-to-end pass rate on 12 custom tasks; this is benchmark-local evidence, not a universal rate and not Hermes evidence.

Code-backed limitations at the pinned WebUI commit:

- `backend/app/schemas/pipelines.py` models operator order with `operators: List[...]`; this is not proof of an arbitrary persisted DAG.
- `backend/app/mcp_server.py` exposes create/update/execute/register operations in the MCP operation allowlist; Hermes will not copy this authority shape.
- `backend/app/services/pipeline_registry.py::_write` overwrites the registry JSON with `json.dump`; Hermes will not copy this persistence shape.
- `backend/app/services/pipeline_compile_check.py` returns `ok: true, skipped: true` on unexpected validation errors; Hermes side-effect admission remains fail-closed.

## 3. Accepted, adapted, and rejected capabilities

### Accepted directly

- Procedural Skill knowledge is separate from live registry/state.
- Agent changes a persistent host-native artifact, not only a transient script.
- Every authoring round rereads current state.
- Mutations are typed and incremental.
- Validation precedes commitment.
- Human/agent/CLI/UI projections share one canonical representation.
- Evaluation distinguishes tasks that need procedural knowledge from simple routing and non-synthesis failures.

### Adapted for Hermes

- Data pipeline becomes governed workflow definition/capability graph.
- Backend SSOT becomes a private append-only artifact event ledger plus replayed projection.
- WebUI synchronization becomes projection-first CLI status/history; future UI is non-authoritative.
- MCP mutation becomes a kernel API/CLI with explicit local-write acknowledgement and immutable authority.
- Pipeline validation becomes existing strict `WorkflowDefinitionV1` compilation plus stale-state and mutation semantics.
- Downstream utility becomes completion-evidence correctness: a syntactically valid artifact is not a completed task.

### Rejected

- Global installation of DataFlow Skills.
- Direct dependency on DataFlow-WebUI or its backend.
- Whole-document overwrite as canonical history.
- Unversioned mutation or last-writer-wins conflict handling.
- Validation fail-open on unexpected errors.
- Tool discovery as execution authority.
- Visual projection as SSOT.
- Transferring the 93.3% metric to Hermes.

## 4. V1 contract

### Artifact projection

```json
{
  "schema_version": "workflow_artifact_v1",
  "artifact_id": "wf.demo",
  "revision": 3,
  "artifact_digest": "sha256:...",
  "definition": {"schema_version": "workflow_definition_v1"},
  "event_count": 3,
  "last_event_id": "wae_..."
}
```

`artifact_digest` binds `{artifact_id, revision, definition}` using canonical JSON.

### Mutation request

```json
{
  "schema_version": "workflow_mutation_v1",
  "artifact_id": "wf.demo",
  "base_revision": 2,
  "base_artifact_digest": "sha256:...",
  "idempotency_key": "request-specific-key",
  "operations": [
    {"op": "add_task", "task": {}},
    {"op": "replace_task", "task_id": "verify", "expected_task_digest": "sha256:...", "task": {}},
    {"op": "remove_task", "task_id": "obsolete", "expected_task_digest": "sha256:..."},
    {"op": "set_metadata", "title": "...", "goal": "...", "non_goals": [], "constraints": []},
    {"op": "set_loop_contract", "loop_policy": {}, "stop_conditions": [], "handoff": {}}
  ]
}
```

V1 intentionally has no `set_authority` operation. A task replacement may narrow but may not widen its side-effect level or capability set relative to the replaced task. V1 rejects any newly added task with a non-`none` side effect or non-empty capability set; introducing new authority-bearing work requires a future separately authorized contract/version.

### Event / receipt

- `artifact_initialized`: embeds the normalized initial definition.
- `mutation_committed`: embeds the normalized mutation, resulting definition, prior/new revision and prior/new digest.
- Event IDs are derived from the canonical event body.
- The apply receipt is the committed event identity plus prior/new artifact digests and a successful synchronized read-back marker.

## 5. Validation and transaction algorithm

Read-only validation:

1. Acquire synchronized read-only access.
2. Decode and replay every complete event.
3. Require artifact ID, revision and digest equality.
4. Apply operations to an isolated candidate.
5. Reject workflow-level authority changes and task authority widening.
6. Set definition version to `base_revision + 1` and compile with the existing strict definition compiler.
7. Return candidate digest/report; do not touch the ledger or lock file.

Commit:

1. Acquire the existing exclusive private-ledger writer lock.
2. Read and replay under that same lock.
3. Re-run all validation against the locked tip.
4. Validate the complete candidate before first-ledger creation; create a missing ledger with descriptor-relative `O_EXCL` only after semantic validation succeeds.
5. Build one canonical event and issue exactly one record write. A short write is never continued with a second append.
6. `fsync` the ledger and, for first/empty-ledger publication, its containing directory.
7. If first-ledger preparation (`fchmod`/append seek) or a partial append fails, truncate/remove the uncommitted first ledger and sync its directory. If rollback fails, report `durability_unknown`.
8. On a file or directory `fsync` failure after a complete write, report `durability_unknown`; an exact idempotent retry must re-sync both the ledger and directory before returning the original receipt.
9. Read from the same anchored ledger descriptor and replay.
10. Require revision/digest/event ID equality with the candidate.
11. Release the lock and return the receipt.

### Persistence and threat-model boundary

- The private root is a trusted, pre-provisioned local POSIX filesystem boundary. Advisory locks govern cooperating Hermes processes.
- Existing runtime-state helpers reject path escape, symlink and hard-link redirection and use descriptor-relative no-follow opens. This slice does not claim protection from a hostile same-principal process that can rename or replace the trusted private directory while a transaction is in flight.
- Normal reads and writes never repair malformed JSONL. A missing final LF, malformed complete row, broken event chain or digest mismatch fails closed. V1 intentionally has no automatic torn-tail recovery API; operator restoration must replace the ledger from separately verified evidence.
- File plus containing-directory `fsync` is claimed for successful first publication and exact retry after durability uncertainty. Fresh-tree crash durability for newly created ancestor directories is not claimed; provision the private root before relying on this layer.

## 6. Evidence and closure matrix

| Requirement | Required evidence |
|---|---|
| Persistent native artifact | schema + replayed status CLI output |
| Typed incremental mutation | contract tests for every operation and unknown-op rejection |
| Current-state grounding | stale revision/digest tests and concurrent writer test |
| Graph/schema semantics | existing compiler invoked; cycle/unknown dependency/verification negative tests |
| Authority boundary | immutable workflow authority and task-widening negative tests |
| Atomic commitment | short-write, partial-write rollback, torn-tail and read-back tests |
| Receipt/read-back/replay | digest/event assertions and fresh-process CLI readback |
| Runtime integration | compile/run accepts artifact ledger and binds resulting definition digest |
| No silent expansion | CLI acknowledgement negative test; no network/external/taskpool/scheduler adapters |
| Benchmark-local evaluation | regression matrix tagged procedural/simple/non-synthesis; no imported 93.3% claim |

## 7. Fresh implementation evidence

Candidate verification completed before immutable freeze:

- Python related regression:
  `scripts/run_tests.sh tests/workflow_kernel tests/hermes_cli/test_workflow_runtime.py tests/contracts`
  → 21 files, 546 tests passed, 0 failed.
- Python static checks:
  targeted `uv run ty check ...` and `uv run ruff check ...`
  → `All checks passed!` for both.
- TypeScript contract checks:
  `pnpm --filter @hermes/workflow-contracts typecheck`, `test`, and `schema:check`
  → typecheck passed; 15 files / 269 tests passed; generated schemas match.
- Artifact durability/adversarial slice:
  21 tests passed, including one-write short-write rollback, first-create preparation cleanup,
  file/directory `fsync` uncertainty retry, tamper rejection, and spawned exact-init contention.
- Real CLI and wheel evidence root:
  `/Users/charles/.hermes/runs/dataflow-harness-absorption-final-evidence-20260725-precommit-04`.
  Thirteen actual CLI invocations prove default-off init, init/apply exact retry,
  read-only validation, apply/read-back, history, artifact-backed compile,
  stale-base rejection, authority-widening rejection, and tamper rejection.
- CLI evidence final ledger SHA-256:
  `1e12981f50dc3340857f7309063d55b41c8bdd71931cf4c365cbb92f6325a63c`.
- Built wheel SHA-256:
  `f8744ed593c6d77cc81c25a19675d3afb93441a9f0d093a03adae3c8a1429a12`.
  An isolated `--no-deps` install resolved and decoded all three packaged artifact schemas.
- Three earlier evidence-harness runs are retained as `failed-01` through `failed-03`.
  They failed on verifier-script assumptions (exact idempotent replay was incorrectly expected
  to be stale; one authority fixture was schema-invalid; one expected error-code label was stale),
  not on a product command unexpectedly violating its contract. The corrected non-overwriting run
  is `precommit-04`.

### Certification gate

The bounded local-only V1 implementation and local verification matrix are complete. Closure remains
`implemented / locally verified / uncertified` until an independent verifier binds a PASS vote to an
immutable candidate identity. Independent review may block or narrow the path; it cannot expand authority.
