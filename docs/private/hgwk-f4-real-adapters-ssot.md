# HGWK F4 Real Adapter Successor — Objective/Spec SSOT

Status: architecture pre-review AMEND resolved in spec; 4A and 4B certified; 4C taskpool CAS precursor implemented and isolated with certification valid only through a matching external exact-tree receipt; 4D Mission stage-report precursor implemented and isolated with certification valid only through a matching external exact-tree receipt; not integrated, live, committed, pushed, or deployed
Created: 2026-08-02T00:16:12+08:00
Amended: 2026-08-02 after independent Codex pre-review
Base commit: `f45a1d4aff849887d916372ddca3d7f4a1e8e557`
Base tree: `2e2365b39e17fdf1a3d80d948c8df5fbba35b549`
Branch: `grok/hgwk-real-adapters-20260802`

## Goal

Advance frozen HGWK v1 from its standalone experimental local-effect harness to a governed, production-shaped path using `workflow_kernel.governed_runtime`, canonical taskpool semantics, canonical `MissionStore`, and an exact-target external adapter boundary without weakening frozen replay, authority, or state-ownership invariants.

## Source facts and boundaries

- `workflow_execution_ledger.py` is the sole workflow-run lifecycle SSOT.
- `workflow_execution_event_v1` has a closed vocabulary and one terminal packet per leaf; its bytes and semantics remain unchanged.
- `GoalManager.workflow_execute_local()` is a local projection path that marks nodes complete without a worker/model; `workflow_apply_adapters()` only writes SessionDB projections. Neither is the production runtime or canonical adapter path.
- `local_effect_harness.py` is an experimental standalone journal, not canonical `EffectReceiptV1` or F4 execution truth.
- `workflow_taskpool_import_write.py` bypasses command-level singleflight and does not provide production fencing; it is not the production taskpool adapter.
- Current `MissionStore.append_event()` lacks caller idempotency/expected revision and actionable events can enqueue/schedule; first integration must be non-actionable and `enqueue_flow=False`.
- The user authorized real external/runtime/taskpool/mission integration. This does not identify a live external platform/recipient/payload and does not authorize push, deploy, publication, broad fan-out, credential entry, payment, destructive action, or implicit scheduler/worker activation.

## Canonical ownership and versions

1. `workflow_execution_ledger` remains the sole owner of workflow-run lifecycle.
2. Preserve `workflow_execution_event_v1` byte-for-byte and semantically; historical v1 readers, fixtures, hashes, and replay remain unchanged.
3. F4 runs that can enter durable `WAITING_RECONCILE` use separately authored `workflow_execution_event_v2`; never mix v1 and v2 records in one run ledger.
4. Add exactly one subordinate `workflow_effect_ledger_v1` that owns cross-run effect claim, fence, dispatch intent, observation, reconciliation, terminal receipt, and compensation facts. It explicitly does not own workflow status.
5. Execution-v2 records reference effect IDs and receipt digests. SessionDB and GoalManager views are projections only.
6. Cross-ledger publication is a recoverable saga, never described as synchronous atomic dual-write.
7. Contract sources are authored in `apps/workflow-contracts/src` and generated into root/package schema copies according to the frozen SSOT.

## Selected runtime and adapter path

- Runtime base: `workflow_kernel.governed_runtime`, extended only through a versioned F4 path. GoalManager is rejected as the production runtime.
- Effect protocol: immutable `EffectCommandV1`, non-serializable/opaque `AuthorityGrant`, `EffectObservationV1`, terminal `EffectReceiptV1`, and one durable effect ledger.
- Taskpool: add a formal versioned API with exact target ID, complete request digest, `expected_revision`, owner, monotonic fence, and atomic CAS. Do not wrap the current import writer as production.
- Mission first slice: exact mission ID, idempotent non-actionable `stage_report` append, `enqueue_flow=False`, no mission `stage` or status mutation, and no cron/tick. Status/stage updates require a later transactional API with expected revision and idempotency.
- External: generic service-gated adapter with exact allowlist/kill switch. Local HTTP receiver certification is allowed; live send remains closed pending an exact platform/recipient/payload/compensation/readback authorization.
- Configuration: successor gates belong in canonical `config.yaml`; do not promote experimental harness environment flags.

## Authority and dispatch sequence

For every real effect, including canonical taskpool and mission smoke:

1. Validate exact immutable command and non-serializable/opaque grant.
2. Validate issuer, policy/config revision, exact adapter, operation, target, payload digest, expiration, and one-shot scope.
3. Perform normalized preflight readback.
4. Under target-scoped lock, atomically allocate monotonic fence and persist `claimed`.
5. Recheck kill switch, allowlist, config revision, grant, and drift under the claim lock.
6. Consume the one-shot grant.
7. Persist and fsync dispatch intent.
8. Dispatch with idempotency key and fence.
9. Persist classified observation and post-readback.
10. Emit terminal receipt only for `applied` or `not_applied`; persist `unknown` as nonterminal history.
11. Project only digest-bound effect facts into execution-v2.

Crash classification:

- before durable dispatch intent: `not_dispatched`;
- after durable dispatch intent without terminal observation: `unknown`;
- `unknown` enters `WAITING_RECONCILE`, never re-enters dispatch, never blind retries, and blocks workflow completion.

## Success criteria

1. Historical v1 compatibility and replay remain exact.
2. Execution-v2 and effect-ledger schemas and Python/TypeScript contracts have parity.
3. Durable claim-before-dispatch, monotonic fencing, one-shot authority, idempotency conflict detection, readback, unknown/reconcile, and compensation are proven.
4. Formal taskpool CAS/fence precursor exists and passes real temporary-vault multiprocess tests before runtime integration.
5. Mission first-slice adapter appends exactly one non-actionable stage report idempotently and proves no status/stage change or cron creation.
6. Runtime execution-v2 blocks on unknown and resumes only after a digest-bound reconciliation result.
7. External local-receiver tests prove network/timeout/duplicate/drift/reconcile behavior; they do not claim live-platform certification.
8. Any canonical/live smoke uses an exact named target, one-shot authority, idempotency key, receipt, readback, and explicit compensation boundary.
9. Focused and full Python/TypeScript/schema suites pass and independent exact-tree review returns no P0/P1.

## Explicit state mappings

| Source fact | Permitted mapping |
|---|---|
| HGWK `accepted` / `running` | no automatic taskpool, mission, or external mutation |
| Effect `applied` | terminal `APPLIED`; leaf advances only after post-readback/evidence verification |
| Effect `not_applied` | no target status advance; node blocks/fails by explicit operation policy |
| Effect `unknown` | `WAITING_RECONCILE`; no terminal workflow, retry, or downstream mapping |
| Taskpool create | `proposed` or `pending` only |
| Taskpool claim | `pending/retry -> claimed` through CAS with owner and fence |
| Runtime dispatch start | owned `claimed -> running` with same fence |
| Worker candidate success | `running -> review`, never directly `done` |
| Verified completion | `review/certified -> done` only through native evidence gates |
| Failure | explicit `retry` or `blocked`, never inferred from mission/HGWK text |
| Mission stage report | append event only; mission status and `stage` unchanged |
| Mission human decision | native decision API: `blocked_on_human`, then `active` after decisions resolve |
| HGWK `run_completed` | never automatically means taskpool/mission `done` or external success |
| External receipt | remains `applied/not_applied/unknown`; internal mapping is a separate effect |

`goal_satisfied` is independent of `applied/not_applied/unknown`; `not_applied` is never silently success.

## Negative cases that fail closed

- missing, expired, replayed, widened, or self-minted grant;
- issuer, policy/config revision, adapter, operation, target, payload digest mismatch;
- unallowlisted target, adapter kill switch, or kill switch closing while waiting;
- stale owner/fence/expected revision and conflicting idempotency reuse;
- taskpool/mission identity or state mismatch;
- preflight/post-readback drift;
- crash before intent, crash after intent, torn write, fsync ambiguity;
- external timeout/ambiguous result -> `unknown` and reconcile only;
- blind retry of `unknown`;
- compensation without terminal original effect or wrong receipt digest;
- mission event causing cron/tick in first slice;
- symlink, hardlink, path replacement, partial publication;
- any target mutation after rejected admission.

## Dependency-ordered RED matrix

0. Historical v1 byte/replay fixtures; v1 rejects effect-v2 records; existing TS/Python/schema parity unchanged.
1. Effect-ledger torn write, fsync ambiguity, duplicate-key conflict, symlink/hardlink/path replacement, multiprocess claim race.
2. Grant negative matrix: missing/expired/replayed/widened/self-minted and command/config mismatches.
3. Claim-before-dispatch: crash before/after intent, stale fence, kill switch race, no redispatch from unknown.
4. Real temporary taskpool create/claim/start/review readback; identical/conflicting concurrency; stale-owner rejection.
5. Real temporary MissionStore idempotent stage-report append; revision conflict; duplicate replay; no cron/status/stage mutation.
6. Execution-v2 unknown/reconcile/resume and terminal receipt projection; no v1 behavior change.
7. Local HTTP receiver timeout-before/after-apply, duplicate provider key, drift, reconcile, compensation.
8. Full isolated `HERMES_HOME` crash/replay/duplicate/negative matrix, then independent exact-tree review.

## Constraints and non-goals

- No in-place v1 event or payload mutation.
- No second workflow lifecycle SSOT.
- No synchronous dual-write atomicity claim.
- No automatic taskpool/mission `done` mapping.
- No canonical taskpool or mission write without an exact target/grant/readback, despite broad integration authorization.
- No live external send until exact platform, target, payload, credentials held by the existing runtime, rollback/compensation semantics, and post-send readback are supplied and preflighted.
- No worker/scheduler broad activation, push, deploy, publication, broad fan-out, credential entry, payment, or destructive cleanup.
- No isolated smoke evidence promoted to deployment/live certification.

## Consequences and risks

- Execution-v2 and effect ledger add a recoverable saga boundary and explicit reconciliation work.
- Canonical taskpool uses note/vault SSOT while MissionStore uses SQLite; adapters must preserve separate state machines.
- Crash after apply but before observation is necessarily `unknown`; only provider/target readback may resolve it.
- Local HTTP proves adapter network behavior, not a live platform.
- Canonical taskpool/mission smoke can pollute live state; isolated real stores are the default and canonical smoke is separately gated.

## Evidence requirements

- exact base/HEAD/tree/status before/after;
- architecture pre-review packet and closure mapping for every AMEND finding;
- RED then GREEN output for every invariant;
- focused/full suite outputs and exit codes;
- isolated real-I/O receipts and store readbacks;
- exact-target receipt/readback only if that gate is separately opened;
- negative scans proving no push/deploy/broad fan-out/credential path;
- Codex exact-tree APPROVE and deterministic Converge matrix.

## Append-only invariant — 4A.5B.12 canonical corpus manifest pin

Governing immutable pin (independent of generator constants):

- canonical corpus manifest path: `tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json`
- canonical corpus manifest raw SHA-256: `c0f935c09ae6bbf16ec8eb066bdad1a5b1426cc8be6a6be5da8eed6f658e40fd`
- baseline manifest path: `tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json`
- baseline manifest raw SHA-256: `fa4566baeb9fe32a8e8146a9201f1142fdc8040d0a5ed450b47814f2a9f0b960`

Ordinary generator `--check`/`--write` must verify the manifest file raw SHA against this pin and against an independent test/module pin that does not import the generator. Coherent corpus+manifest mutation without updating both independent authorities is rejected. Ordinary `--write` remains validation-only zero-write; migrations closed.

## 4C exact-tree certification boundary

- The 4C taskpool CAS/fence precursor is an isolated local primitive governed by `docs/private/hgwk-f4-real-adapters-4c-taskpool-cas-spec.md`.
- It grants no live taskpool, worker, scheduler, mission, gateway, adapter, network, publish, deploy, or external-send authority.
- Certification is not embedded as a self-referential digest in this SSOT. A 4C approval is valid only when an external certification receipt identifies a complete tracked-plus-untracked dirty-tree manifest, an independent reviewer recomputes that identity, and the reviewer returns no P0/P1 findings for those exact bytes.
- Any repository-byte or dirty-status change invalidates that receipt until producer verification, manifest freeze, and independent review are repeated.

## 4D exact-tree certification boundary

- The 4D Mission `stage_report` precursor is an isolated local primitive governed by `docs/private/hgwk-f4-real-adapters-4d-mission-stage-report-spec.md`.
- It may append only a non-actionable report to an explicitly supplied local `MissionStore` path in isolated verification. It grants no live Mission, taskpool, scheduler, worker, cron, tick, gateway, network, publish, deploy, push, or external-send authority.
- Mission `status` and `stage` remain unchanged; the dedicated transaction never reaches flow-signal enqueue or dispatch paths. Any lifecycle transition requires a later separately specified and authorized API.
- 4D certification is not embedded as a self-referential digest. Approval is valid only when fresh producer verification and an external read-only review identify the same complete tracked-plus-untracked dirty-tree identity and the reviewer returns no blocking finding for those exact bytes.
- Any subsequent repository-byte or dirty-status change makes a 4D review a predecessor verdict; reproducible findings must be checked against the successor, but predecessor approval cannot certify it.
