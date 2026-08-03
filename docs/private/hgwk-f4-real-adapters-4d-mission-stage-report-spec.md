# HGWK F4 4D — Canonical Mission Stage-Report Precursor Spec

Status: implemented and isolated; certification is valid only through a matching external exact-tree receipt; not integrated, live, committed, pushed, or deployed
Created: 2026-08-03T18:50:13+08:00
Parent SSOT: `docs/private/hgwk-f4-real-adapters-ssot.md`
Base commit: `f45a1d4aff849887d916372ddca3d7f4a1e8e557`
Predecessor: independently certified isolated 4C taskpool CAS precursor

## Objective model

### Goal

Add the smallest production-shaped Mission precursor needed by F4: append exactly one digest-bound, non-actionable `stage_report` to an exact mission in an isolated real `MissionStore`, with caller idempotency and optimistic expected-revision semantics, while proving that mission status/stage, flow signals, cron state, scheduler/worker state, and external systems are not advanced.

### Current evidence

- `MissionStore` is SQLite-backed and `append_event()` writes `mission_events`, updates only `missions.updated_at`, and may enqueue/dispatch flow signals for actionable event types.
- Existing `append_stage_report()` calls `append_event()` without caller idempotency or expected revision.
- `stage_report` is not currently actionable, but the F4 boundary requires an explicit `enqueue_flow=False` call rather than relying on vocabulary membership.
- The parent SSOT dependency matrix orders MissionStore stage-report work after taskpool CAS and before execution-v2 runtime integration.
- Python 3.11 baseline: `tests/test_missions.py tests/hermes_cli/test_missions.py` = 21 passed.

### Viable paths

1. Widen generic `MissionStore.append_event()` with required CAS/idempotency fields.
   - Rejected for 4D: broad compatibility and lifecycle blast radius; existing callers are not ready for mandatory revision semantics.
2. Add a dedicated F4 stage-report transaction directly in `MissionStore`, then wrap it with a narrow `workflow_kernel` adapter.
   - Selected: canonical store owns the SQLite transaction; adapter owns F4 validation and stable domain errors; existing generic callers remain unchanged.
3. Create a parallel Mission ledger/table owned by `workflow_kernel`.
   - Rejected: would create a second Mission truth source and violate canonical ownership.

### Chosen path

- Add additive nullable idempotency metadata to `mission_events`, with partial unique indexes for `(mission_id, idempotency_key)` and `(mission_id, mission_revision)`.
- Define Mission revision for this precursor as the append-only count of canonical `mission_events` for one mission immediately before the attempted append. Existing rows participate in the count; no historical row is rewritten.
- Execute `BEGIN IMMEDIATE`; resolve exact mission and existing idempotency record inside the same transaction; then compare expected revision and append once.
- Same key + same complete request digest returns the original durable event even when the supplied expected revision is now stale.
- Same key + different request digest fails as conflict with zero mutation.
- New key + stale expected revision fails with zero mutation.
- The dedicated method explicitly avoids `enqueue_flow_signal()` and `dispatch_pending_flow_signals()` and does not call the generic append path.
- A narrow `workflow_kernel.mission_stage_report` adapter validates exact input, calls the canonical store method, performs readback, and returns an immutable/digest-bound result.

## Contract

### Public adapter request

Required exact fields:

- `mission_store_path`: exact pre-existing local SQLite file supplied by the composition root; never derived from serialized command data; a missing path rejects without creating a directory or database.
- `mission_id`: non-empty exact canonical Mission identifier.
- `expected_revision`: integer in `0..9223372036854775806`, booleans rejected; the upper bound reserves one SQLite signed-integer revision increment.
- `idempotency_key`: non-empty bounded string.
- `summary`: non-empty bounded string.
- `evidence`: list/tuple of unique non-empty bounded canonical strings.
- `risks`: list/tuple of unique non-empty bounded canonical strings.
- `next_step`: bounded string.
- `needs_user`: exact boolean.

All nine fields are runtime-required; omissions and unknown fields fail with `request_invalid` rather than Python argument-binding exceptions.

The complete canonical request body excluding `mission_store_path` is hashed as `request_digest`. The body is domain-separated by `schema == "mission_stage_report_request_v1"` and `operation == "append_stage_report"`.

### Durable event

- `event_type == "stage_report"`.
- Payload preserves canonical `evidence`, `risks`, `next_step`, and `needs_user`.
- Durable row records `idempotency_key`, `request_digest`, and `mission_revision = expected_revision + 1`.
- Durable envelope requires a non-empty bounded UTF-8 `event_id`, a non-negative finite numeric `created_at`, and an exact non-boolean integer `mission_revision`.
- Adapter result includes exact mission/event/revision/request digest and a canonical `result_digest`; digest failures from malformed durable envelope data fail as `readback_mismatch`.
- Result is an immutable mapping; nested event/payload values are immutable.

### Stable adapter error codes

- `request_invalid`
- `mission_not_found`
- `revision_conflict`
- `idempotency_conflict`
- `durability_unknown`
- `readback_mismatch`

No raw SQLite/platform exception may cross the adapter boundary during request normalization, store initialization, transaction execution, readback, or cleanup.

## Invariants

1. MissionStore remains the sole Mission truth owner.
2. Existing Mission/event rows are not rewritten during schema migration; opening a legacy store may durably add the three nullable metadata columns and indexes before an application request is evaluated. Initialization acquires `BEGIN IMMEDIATE` before schema inspection/migration so concurrent legacy openers converge.
3. The two named partial unique indexes are verified for uniqueness, exact ordered columns, and exact non-null predicates; malformed same-name indexes fail closed. Replay cardinality other than exactly one fails as `readback_mismatch`.
4. Historical generic Mission APIs preserve signatures and behavior.
5. One accepted logical request produces exactly one stage-report row.
6. Idempotent replay returns the original event and revision without mutation.
7. Conflicting key reuse and stale revision reject before any stage-report or Mission-row mutation; the one-time legacy schema upgrade described by invariant 2 is the only allowed precursor write.
8. Accepted or rejected requests never change mission `status` or `stage`.
9. No flow signal, cron job, tick, scheduler, worker, taskpool, gateway, network, or external side effect is created.
10. The adapter always passes/implements the equivalent of `enqueue_flow=False`; safety does not depend on `stage_report` remaining absent from an actionable-type set.
11. SQLite busy/commit ambiguity is never reported as applied without exact durable readback.
12. This slice grants no runtime execution-v2, live Mission, commit, push, deploy, or external-send authority.

## Negative and adversarial cases

- missing/unknown request fields and missing/unknown mission;
- missing store path creates no parent directory or SQLite file;
- bool/negative/stale expected revision;
- empty/oversized or wrong-type identity/text fields;
- hostile non-list evidence/risks, non-string entries, duplicate/object values;
- same key/same digest replay after intervening unrelated event;
- same key/different digest;
- two gate-synchronized real processes racing same mission/revision with distinct keys: exactly one applies;
- two gate-synchronized real processes racing same key/same request: both return one durable event;
- an independently held `BEGIN IMMEDIATE` prevents a signaled contender, whose child-local schema initializer is bypassed, from reaching/completing the dedicated append transaction until release, after which the contender applies;
- every spawned-process harness has bounded waits plus `finally` release and join/terminate/kill fallback;
- migration over a legacy database containing Mission and event rows, including concurrent spawned openers;
- malformed same-name unique indexes, duplicate replay rows, and openable schemas/rows missing canonical event columns fail with stable adapter errors;
- injected begin/select/insert/update/commit/readback failures, including a real `commit()` that durably applies and then raises before the adapter can observe success;
- backend SQLite/OS/runtime/malformed-state exception types and messages are normalized to a stable code/message; neither direct strings, exception `__context__`/`__cause__`, nor formatted tracebacks expose backend details across the adapter boundary;
- monkeypatch `stage_report` into actionable vocabulary and prove the dedicated adapter still creates zero flow signals/dispatch calls;
- pre/post snapshots prove status/stage unchanged and no cron/script/output files appear in isolated `HERMES_HOME`;
- direct SQL readback proves exact row metadata, request digest, and type-exact payload;
- malformed durable envelope values (BLOB `event_id`, non-finite `created_at`, non-integer revision) fail with stable `readback_mismatch`;
- post-return mutation attempts fail and do not alter durable bytes/readback.

## TDD and evidence plan

1. RED: desired adapter import/API absent.
2. RED: first accepted append contract.
3. RED: idempotent replay and conflicting reuse.
4. RED: stale-revision and multiprocess race.
5. RED: legacy-schema migration and zero side effects.
6. RED: fault classification/readback and immutable result.
7. GREEN each focused node, then existing Mission suites.
8. Run taskpool CAS and workflow-kernel regression suites in cache-neutral Python 3.11.
9. Freeze complete tracked-plus-untracked identity after all writable verification.
10. Independent read-only spec review, then quality/security review; any edit invalidates the review and verification identity.

## Parallelism and authority

- Parallel read-only research/review is allowed.
- Implementation, schema migration, and tests touch the same Mission state machine and are serialized under the main integration owner.
- All tests use isolated temporary stores and homes.
- Commit/push/deploy/live Mission/taskpool/scheduler/worker/external actions remain closed.

## Converge requirements

- Every material invariant maps to a focused test and direct SQLite/filesystem readback.
- Existing Mission suites remain green under Python 3.11.
- No unexplained repository-wide regression is attributed to 4D.
- Current exact-tree independent review has no P0/P1.
- SSOT status distinguishes isolated precursor certification from integration/live authority.

## Producer evidence lineage (not independent certification)

- Latest focused remediation suite: 45 passed under Python 3.11 with cache disabled. It includes required/unknown-field normalization, canonical duplicate rejection, missing-path no-creation, gate-synchronized same/distinct-key races, SQL-traced mutation-sensitive `BEGIN IMMEDIATE` holder/contender serialization, failure-safe spawned-child cleanup, concurrent legacy migration, canonical-index verification, duplicate replay cardinality, malformed-openable-schema normalization, direct begin/select/insert/update/commit/readback fault injection, and malformed durable-envelope rejection.
- The first exact-tree review matched identity and found no P0/P1 but rejected two blocking P2 acceptance violations: unnormalized request-preparation failures and incomplete direct fault/readback evidence. Those findings were reproduced RED and independently confirmed resolved on its successor.
- The second exact-tree review matched its successor identity, independently confirmed the first review findings and the `false`-versus-`0` type-confusion remediation, but rejected one new blocking P2: malformed durable `event_id`/`created_at` values escaped during result digest. Those failures were reproduced RED and fixed through envelope validation and stable digest-failure mapping.
- A third exact-tree review matched opening/closing identity and returned APPROVE, but its receipt was later revoked: delayed predecessor-identity reviewers reported additional findings that remained reproducible on those approved bytes. Fresh current-byte evidence observed 10 RED failures spanning required/unknown fields, canonical duplicates, missing-store filesystem creation, concurrent legacy migration, malformed named indexes, and duplicate replay cardinality; those failures were remediated.
- The first replacement APPROVE was also revoked after a later full predecessor report disclosed a malformed-openable-schema replay that still escaped as raw `IndexError` on the replacement bytes. That exact case was reproduced RED and now maps to `durability_unknown` under the adapter storage boundary.
- The next exact-tree reviewer independently confirmed every prior defect closed but rejected one blocking P2 evidence gap: same/distinct-key process tests could pass under sequential scheduling. The successor releases both workers from a shared gate and separately contests `BEGIN IMMEDIATE`.
- Its successor review confirmed the shared-gate races but rejected two further P2 evidence defects: the holder could block the contender in schema initialization rather than the dedicated append, and child cleanup was not guaranteed on failure. The current harness bypasses only child-local schema initialization for the dedicated-transaction probe and uses bounded `finally` cleanup with join/terminate/kill fallback in every process harness.
- The following exact-tree review confirmed initialization bypass and ordinary cleanup but rejected two residual P2s: the test could survive replacement of the early immediate transaction with deferred locking, and a partial process-start failure could escape cleanup. The current test traces contender `SELECT` statements and proves none execute while the holder owns the writer reservation; replacing the dedicated `BEGIN IMMEDIATE` with `BEGIN` produces `DID NOT RAISE` and fails the test. Process starts now occur inside protected regions and only successfully started children are stopped in `finally`.
- The resulting replacement certification was revoked after reconciling another delayed predecessor-identity security audit: its legacy-migration and malformed-schema P1s were already closed, but raw backend exception messages remained reproducible on the certified bytes and `commit()`-applies-then-raises lacked direct causal coverage. Its first successor fixed outer messages and added durable-commit ambiguity evidence, but exact-tree review rejected an unsuppressed exception-chain disclosure. The current successor raises normalized failures only after leaving backend exception handlers and tests outer strings, `__context__`, `__cause__`, and formatted tracebacks.
- Full Mission/workflow-kernel, Ruff, ty, py_compile, and `git diff --check` evidence must be recorded externally after the final repository edit and before identity freeze. Any later edit makes that evidence a predecessor result.
- Repository-wide `tests` is not a green baseline: optional-dependency collection first failed, and a second all-tests attempt with ACP/messaging extras timed out amid pre-existing logging/thread/file-descriptor failures. It is not used as 4D approval evidence.
- No exact-tree approval is embedded here. Certification exists only when fresh frozen-candidate producer evidence and an external read-only reviewer bind the same complete tracked-plus-untracked identity.
