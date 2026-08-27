# HTDA-G2 exact-tree requirement / negative / evidence matrix

Status semantics: `covered` means the listed contract is exercised by the fresh local test command recorded below. `partial` means a material negative or external/runtime boundary remains open. No row authorizes activation.

## Fresh local evidence checkpoints

| E1 | `pytest -q tests/agent/test_task_runtime.py tests/agent/test_task_runtime_worker.py tests/agent/test_task_runtime_process_supervisor.py tests/agent/test_task_runtime_workspace.py` | `76 passed in 6.77s` |
| E2 | `pytest -q tests/agent/test_task_runtime_ingress.py tests/agent/test_task_runtime_process_e2e.py tests/tools/test_task_runtime_tool.py tests/gateway/test_fast_command.py tests/test_dispatch_session_id.py` | `23 passed in 1.04s` |

- E3: temporary-home real CLI startup: `HOME=<tmp>/home HERMES_HOME=<tmp>/hermes uv run --frozen hermes --version` → exit 0, Hermes Agent v0.19.0; `cron/jobs.json` and `gateway.json` remained absent.

- E4: `python -m py_compile agent/task_runtime.py agent/task_runtime_worker.py agent/task_runtime_process.py agent/task_runtime_workspace.py` → exit 0; `git diff --check` → exit 0.
- E5: `pytest -q tests/agent --maxfail=20` → `20 failed, 3491 passed in 182.98s`; broad all-green is false. The first 20 failures are confined to Copilot ACP, execution-graph governance, file safety, image routing and model metadata; none touches the HTDA-G2 changed-file set. Log: `/tmp/htda-g2-agent-suite-successor.log`.
- E6: historical base/current follow-up attempts did not yield a reliable exact-node differential result because several extracted historical node IDs failed collection. Baseline equivalence and order-dependence are therefore not claimed for this successor; the broad failures remain explicit cross-domain evidence debt.
- E7: fake Telegram delivery rail targeted rerun → `2 passed in 0.39s`; exact ingress persisted, bounded task context reached the fake Agent, the in-memory adapter returned `fake-delivery-1`, real-network count remained zero, and isolated `config.yaml`, `cron/jobs.json`, and `gateway.json` remained absent.
- E8: `pytest -q tests/tools/test_task_runtime_tool.py` → `5 passed in 0.14s`; runtime-bound reviewer/compiler identity and `review_spec → freeze_spec` tool flow are covered.

## Business-rule coverage

| Requirement IDs | Status | Code / evidence |
|---|---|---|
| BR-01, BR-02, BR-19, BR-21 | covered | profile-owned append-only digest-chained ledger, CAS, owner derivation and cross-profile negatives in `test_task_runtime.py`; E1 |
| BR-03, BR-17 | covered | exact entrypoint binding and replay/conflict tests in runtime ingress/tool suites; E1/E2/E3 |
| BR-04, BR-05, BR-13, BR-14, BR-15, BR-25 | covered | revision/digest checks, correction invalidation, stale packet/result/evidence/closure tests; E1 |
| BR-06, BR-07, BR-20, BR-23, BR-24 | covered | closed-world WorkerResult, claims/evidence/verification/write-set/authority gates and frozen-plan closure tests; E1 |
| BR-08, BR-22, BR-27, BR-28 | covered | bounded pool, heartbeat, natural expiry quarantine, controller-issued process cleanup, detached descendant and residual-readback tests; E1 |
| BR-09, BR-10, BR-11, BR-16 | covered | bounded ingress projection, artifact references and no-send gateway/CLI continuity tests; E2/E3 |
| BR-12 | partial | local closure/candidate-bound gates covered by E1; external activation/readback intentionally closed |
| BR-18 | covered | managed linked worktree base/HEAD/write-set/symlink/executable/deletion tests; E1 |
| BR-26 | covered | runtime-supplied integrator/verifier identity and tool-schema tests; E1/E2 |
| BR-29 | covered | post-accept freeze, deterministic archive, persisted exact replay, controller cleanup and workspace reap tests; E1 |
| BR-30 | covered | isolated-index tree construction, deterministic commit/ref replay, diff-tree readback, exact source-HEAD/base gate and canonical HEAD non-mutation assertions; E1 |
| BR-31 | covered | accepted-result candidate-finalize fault injection makes result non-integrable, quarantines packet/slice through an append-only event and preserves workspace/process readback; E1 |
| BR-32, BR-33 | covered | governed SpecBundle negative matrix covers task class, project/prototype/competitor/open-source preflight, bidirectional source/claim edges, Oracle/closure coverage and blocking unknowns; E1 |
| BR-34, BR-35 | covered | append-only digest-bound independent review, state-neutral exact replay/conflict rejection, compiler separation, plan readmission and runtime-bound tool flow; E1/E2/E8 |
| BR-36, BR-37 | covered | non-draft source correction invalidates dependent projections and old review; summary/Oracle/source contracts are structurally required by the governed spec gate; E1 |
| BR-38 | covered | pre-SpecReview schema reconstruction preserves the existing projection-bound event ledger without fabricating a migration event; E1 |


## Negative-case coverage

| Negative IDs | Status | Code / evidence |
|---|---|---|
| N-01, N-02, N-09, N-10, N-11, N-16 | covered | temporary-home process continuity, exact binding, bounded projection and fake/no-send gateway rail; E2/E3 |
| N-03, N-04, N-05, N-06, N-08, N-12, N-15, N-23, N-27, N-28 | covered | stale revision/fence, replay neutrality, invalidation, closure and plan negatives; E1 |
| N-07, N-21, N-26, N-29 | covered | authority-smuggling, unknown field, incomplete result and actor-impersonation negatives; E1/E2 |
| N-13, N-14, N-19, N-20, N-25 | covered | owner/profile isolation, ledger tamper, UPDATE/DELETE and cross-profile replay negatives; E1 |
| N-17, N-18 | covered | workspace containment/marker/base/dirty/write-set/worker-commit negatives; E1 |
| N-22, N-24, N-30, N-31 | covered | timeout/expiry/runner-start quarantine and exact release-proof negatives; E1 |
| N-32 | covered | duplicate/unexpected/hardlink/path escape/hash/content checks, malformed receipt, serialized concurrent freeze, no-follow archive read and resource-size ceiling; E1 |
| N-33 | covered | source/base binding, exact source-HEAD mismatch, CAS candidate ref, deterministic replay, explicit conflicting-ref and wrong-base rejection, diff readback, canonical HEAD non-mutation, and injected post-ref/pre-DB-commit failure followed by exact roll-forward replay; E1 |
| N-34 | covered | injected post-accept finalization failure is fail-closed and idempotently quarantined while preserving the only recoverable managed workspace; E1 |
| N-35, N-36 | covered | missing governance, malformed task class/claims/closure/source coverage and required discovery-lane completion fail closed; E1 |
| N-37 | covered | self-review, unreviewed mutation, stale source digest and append-only review identity conflict negatives; E1 |
| N-38 | covered | source change clears plan/slices/candidate/evidence/closure and makes stale plan unclaimable until fresh review/spec/plan; E1 |
| N-39 | covered | reviewer/compiler derive from runtime session and are absent as public actor-override properties; E2/E8 |
| N-40 | covered | empty-table schema migration preserves the prior projection digest and exact state-event history; E1 |


## Converge result

The current uncommitted tree is focused-green, but E5 remains non-green and E6 intentionally does not infer baseline equivalence. Candidate certification remains blocked until the final documentation/test delta is rerun, frozen to an exact local commit/tree and independently reviewed with no unresolved P0/P1. Separately, g7 runtime activation/readback remains closed pending exact human authority.
