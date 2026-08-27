# HTDA-G2 exact-tree requirement / negative / evidence matrix

Status semantics: `covered` means the listed contract is exercised by the fresh local test command recorded below. `partial` means a material negative or external/runtime boundary remains open. No row authorizes activation.

## Fresh local evidence checkpoints

| E1 | `pytest -q tests/agent/test_task_runtime.py tests/agent/test_task_runtime_worker.py tests/agent/test_task_runtime_process_supervisor.py tests/agent/test_task_runtime_workspace.py` | `69 passed in 6.55s` |
| E2 | `pytest -q tests/agent/test_task_runtime_ingress.py tests/agent/test_task_runtime_process_e2e.py tests/tools/test_task_runtime_tool.py tests/gateway/test_fast_command.py tests/test_dispatch_session_id.py` | `23 passed in 0.99s` |

- E3: temporary-home real CLI startup: `HOME=<tmp>/home HERMES_HOME=<tmp>/hermes uv run --frozen hermes --version` → exit 0, Hermes Agent v0.19.0; `cron/jobs.json` and `gateway.json` remained absent.

- E4: `python -m py_compile agent/task_runtime.py agent/task_runtime_worker.py agent/task_runtime_process.py agent/task_runtime_workspace.py` → exit 0; `git diff --check` → exit 0.
- E5: `pytest -q tests/agent --maxfail=20` → `20 failed, 3491 passed in 165.70s`; broad all-green is false. The 20 node IDs are confined to Copilot ACP, execution-graph governance, file safety, image routing and model metadata; none touches the HTDA-G2 changed-file set.
- E6: rerunning those exact 20 node IDs on both base `5e966d06067be042326fb564e7f731ae9e66ccec` and the current tree produced the same result: `1 failed, 19 passed` (`test_run_prompt_preserves_real_home_when_profile_home_available`). The other 19 failures are broad-suite order/pollution dependent, and the remaining Copilot failure is baseline-present. This is differential non-regression evidence, not a claim that the repository broad suite is green.

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
| N-33 | covered | source/base binding, exact source-HEAD mismatch, CAS candidate ref, deterministic replay, explicit conflicting-ref and wrong-base rejection, diff readback and canonical HEAD non-mutation; E1 |
| N-34 | covered | injected post-accept finalization failure is fail-closed and idempotently quarantined while preserving the only recoverable managed workspace; E1 |

## Converge result

The current uncommitted tree is focused-green and its broad-suite failures are differentially baseline-present/order-dependent per E5/E6, but this does not claim repository-wide green. Candidate certification remains blocked until an exact local commit/tree is independently reviewed with no unresolved P0/P1. Separately, g7 runtime activation/readback remains closed pending exact human authority.
