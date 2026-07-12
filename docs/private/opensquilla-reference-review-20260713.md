# OpenSquilla Reference Review for Hermes Self-Optimization

```yaml
schema: hermes_reference_review_v1
id: opensquilla-20260713
source_media: https://weixin.qq.com/sph/A3AfqQQVGX
source_repo: https://github.com/opensquilla/opensquilla
source_commit: aa223cad912c447db3537646bb945fe105ab6197
local_checkout: /tmp/opensquilla-analysis
review_scope: static source, docs, public preview metadata, and GitHub API
runtime_authority: none
```

## Spec brief

Goal: absorb verified, transferable OpenSquilla patterns into Hermes without installing OpenSquilla, migrating `~/.hermes`, copying third-party code, or opening runtime/external side-effect gates.

Non-goals:

- no OpenSquilla installation or execution;
- no `opensquilla migrate hermes` invocation;
- no model-router replacement or global prompt change;
- no unverified DRACO/Fable 5 or 60–80% savings claim;
- no external dispatch, deploy, push, destructive operation, credential entry, or task lifecycle mutation.

Invariants:

- Hermes prompt-cache prefix remains byte-stable within a conversation;
- capability stays at the edge unless a concrete runtime consumer and E2E proof justify core wiring;
- raw tool evidence remains durably recoverable when a provider-visible projection is shortened;
- routing observability must not store prompt/free text by default;
- estimated savings must never be reported as realized savings;
- migration stays dry-run-first, redacted, transactional, and scheduler-safe.

## Source boundary

The public WeChat Channels preview exposed the caption, author, timestamp, cover text, and engagement metadata, but full playback was blocked by the QR/App gate. The preview is a lead only. Repository facts below were read from the pinned commit and GitHub API.

At review time GitHub API reported 5,808 stars, 414 forks, 72 open issues, Apache-2.0, Python, and repository creation on 2026-05-06. Those counts are time-scoped and are not architectural evidence.

## Claim/evidence map

### C-001 — OpenSquilla is a complete agent runtime, not a token-only plugin

Evidence:

- `README.zh-Hans.md:4-46`
- excerpt SHA-256: `3b26f1504761e89969fb8b21e6d7a9d0d4fc610ce615bb1654473e841d556e0e`

Supported facts: microkernel positioning, local model routing, persistent memory, layered sandbox, unified turn loop, multiple providers, current preview status.

Hermes decision: treat OpenSquilla as a peer/reference implementation. Do not integrate it as a dependency.

### C-002 — Per-turn routing decisions are durable, privacy-bounded observability records

Evidence:

- `migrations/V017__router_decisions.py:1-60`
- excerpt SHA-256: `c7ff778ff03765776d7450ca6e49de9895db56517d464857a5250ba569a6478b`
- `src/opensquilla/persistence/router_decision_writer.py:1-24`
- excerpt SHA-256: `08c37406984a707f815768a785b0b2d0235478fb0581359877fac5a27d3366f5`

Supported facts:

- one decision row per routed turn;
- proposed/final tier, confidence, provider/model, source, baseline model, savings percentage, fallback hops, and route trail are persisted;
- prompt/free text is excluded;
- enum/numeric sanitization, bounded retention, best-effort fail-open writes, and WAL/locking are explicit.

Hermes transfer:

Adopt the contract shape, not the implementation. A future Hermes routing ledger should separate:

1. prediction: proposed tier/model, confidence, baseline, estimated cost/savings;
2. execution: final provider/model, fallback hops, retries, cache-continuity decision;
3. outcome: realized tokens/cost/latency, completion state, human correction, evidence quality;
4. promotion: candidate router version, offline comparison, regression veto, rollback receipt.

No runtime ledger is added by this review because Hermes currently lacks a single concrete consumer and the repository constitution rejects speculative core surfaces.

### C-003 — Tool-result compression separates durable/runtime evidence from provider-visible projection

Evidence:

- `docs/features/tool-compression.md:1-59`
- excerpt SHA-256: `da7e14f9d4d6bbec9746b5f3548bc37dd99a3eac971873c4f4009587a7bad32d`

Supported facts:

- raw/durable results and bounded provider views are separate;
- projections may be truncation, semantic summary, or structured reducers;
- handles preserve access to out-of-band results;
- semantic summarization adds a model call and should be opt-in.

Hermes transfer:

Hermes already persists oversized tool results through `tools/tool_result_storage.py` and exposes large-result handling in `run_agent.py`. The net-new rule is to evaluate projections by shape and preserve a verifiable handle/readback path. A compact projection must never become the only copy or claim more than the underlying evidence.

### C-004 — Self-migration is a stable report contract and transactional workflow

Evidence:

- `docs/self-migration-report-contract.md:1-47`
- excerpt SHA-256: `7c027de0b9a7c5263cdc6e97407fb183d829b149b35717dbabab4f0cbd6dd2ef`
- `CHANGELOG.md:17-23`
- excerpt SHA-256: `370b23103e158a8f4b68290b3c011a0e67b3721621231184e07e611997eb12c4`

Supported facts:

- dry-run writes nothing;
- report schema is a pinned wire contract;
- secret values are excluded;
- preflight includes process/schema/disk checks;
- imported jobs are paused;
- apply uses staging, WAL-safe database handling, and transactional publication.

Hermes transfer:

Future Hermes profile/home migrations should expose a stable additive report with source/target, apply flag, item statuses, transforms, redacted secret relocation names, paused jobs, preflight, recovery metadata, and readback. Migration apply must not be inferred from a successful preview.

### C-005 — Router self-learning is explicitly opt-in and rollback-aware

Evidence:

- `docs/releases/0.5.0rc3.md:42-65`
- excerpt SHA-256: `8d1b6cfe58ca0332847271e488a3ab6e33e78875a58312cfe036dab1a70ecafe`

Supported facts: privacy-preserving feature collection, explicit feedback, offline candidate evaluation, rollback on regression, and default-off operation.

Hermes transfer:

Route-learning may produce proposal/candidate artifacts only until it passes replayable offline evaluation, cost/quality counterfactual comparison, cache-continuity checks, provider-drift checks, and a separate promotion authority gate.

### C-006 — OpenSquilla explicitly targets Hermes state migration

Evidence:

- `MIGRATION.md:227-296`
- excerpt SHA-256: `d6837664b757baa5654d993162a9d6663a9e9ccc1ec727976bcc93ef14d74434`

Supported facts: OpenSquilla can import selected Hermes persona, memory, skills, provider/search configuration, and archive unsupported runtime artifacts; live runtime/session/gateway state is not imported and the migrator documents coverage limits.

Hermes decision: this increases dual-controller/state-fork risk. Never run both systems against the same state, cron, channels, or side-effect adapters without an isolated migration plan and exact authority.

## Rejected or unverified claims

- `DRACO` superiority over `Fable 5`: not found in the pinned public repository/docs reviewed here.
- universal 60–80% token savings: no reproducible workload/result bundle was found; treat as promotional/best-case until realized-cost evidence exists.
- production readiness: repository metadata says Alpha and the analyzed source version is `0.5.0rc3`; this is not stable-production proof.
- team-background claims from the video: not independently verified from the pinned repository.

## Hermes overlap and gap analysis

Already covered:

- complexity router: `agent/task_router.py`;
- oversized tool-result persistence: `tools/tool_result_storage.py` plus `run_agent.py`;
- dry-run migration behavior: OpenClaw migration surfaces;
- provider fallback, session persistence, skills, MCP, gateway, scheduler, and sandbox controls;
- Agent OS route/readback and governance evidence surfaces.

Net-new gaps worth bounded implementation:

1. estimated-versus-realized routing economics contract;
2. fallback/retry/cache-switch penalty accounting;
3. privacy-bounded per-turn route decision ledger with retention;
4. offline candidate-router replay and regression veto;
5. shape-aware tool-result projection quality metrics tied to durable handles;
6. stable additive migration report contract shared across migration sources.

## Decisions

### Immediate landing

- this canonical reference-review SSOT;
- one reusable Hermes skill reference translating the patterns into gates;
- one proposal-only ExperienceAtom for the routing-economics contract.

### Deferred implementation candidates

P1 — routing economics observation packet, local and read-only first:

- define versioned schema outside the hot path;
- feed only sanitized enum/numeric values;
- produce estimates and realized outcomes separately;
- add deterministic tests for no free text, bounded retention, malformed input, and no routing mutation;
- require a real consumer before runtime wiring.

P1 — candidate-router replay harness:

- replay pinned historical feature/outcome records;
- compare quality, cost, latency, fallback, and cache-switch penalties;
- fail promotion on quality regression even when estimated cost improves;
- remain proposal-only/default-off.

P2 — structured tool projection evaluators:

- reducers for logs, diffs, JSON, and test output;
- raw handle/path retained;
- projection carries omission metadata;
- no semantic summarizer unless explicitly configured.

P2 — migration report convergence:

- compare existing Hermes migration reports against the additive contract;
- land report-shape tests before changing apply behavior;
- preserve dry-run/no-write and paused-job invariants.

## Do not do

- do not copy OpenSquilla router models, bundled skills, or tokenjuice code;
- do not install or run OpenSquilla from this review;
- do not write prompts or user text into a routing observability table;
- do not optimize cost alone; quality regression and fallback cost veto promotion;
- do not make route learning default-on;
- do not alter current Hermes routing from media/repository claims alone;
- do not create cron polling, external delivery, or task lifecycle mutations from this review.

## Acceptance and verification

| Claim | Evidence | Status |
|---|---|---|
| Source pinned | repo commit `aa223cad...` read back | pass |
| Preview boundary preserved | full playback labeled QR/App-gated | pass |
| Verified claims have file/line/hash | C-001 through C-006 | pass |
| Promotional claims not promoted | DRACO/Fable/80% explicitly rejected | pass |
| No install/migration/runtime mutation | review scope and commands | pass |
| Reusable procedure landed | `hermes-agent/references/external-agent-runtime-routing-evidence-absorption.md` read back | pass |
| Proposal-only learning captured | atom `hermes-self-optimization-d55c863ba62e`; store validation `ok=true` | pass |
| Runtime routing improvement delivered | no runtime change in this slice | not claimed |

## Verification record

- repository clone: `/tmp/opensquilla-analysis`
- source commit: `aa223cad912c447db3537646bb945fe105ab6197`
- GitHub API and public preview metadata read on 2026-07-13 local session
- model certification: `not_run`; low-risk docs/skill/proposal landing only
- Agent OS route preflight: shadow-only, no capability execution

## Continuation loop — 2026-07-13

### Objective/path correction

A proposed disconnected routing-economics schema/replay module was sent through Agent OS route preflight and independent multi-model review. Agent OS classified the intent as critical/S5 and kept execution blocked. The model round reached only two planners, reported `degraded_not_certified`, `no_action`, and no concrete patch authorization. Therefore the speculative route-ledger/replay surface was not implemented.

The loop instead selected an existing real consumer: oversized tool-result persistence in `tools/tool_result_storage.py`. This path already replaces provider-visible content with a preview plus durable file handle, but the projection did not identify the exact raw bytes or quantify omission.

### Implemented bounded slice

The persisted-output projection now carries:

- projection mode (`head-preview` or `full-preview`);
- preview character count;
- omitted character count;
- SHA-256 of the exact UTF-8 raw result;
- the existing durable sandbox file handle/readback instruction.

The hash is emitted only after a successful durable write. Inline truncation after missing environment/write failure does not claim a recoverable exact artifact.

### TDD evidence

- RED: `uv run --python 3.12 pytest tests/tools/test_tool_result_storage.py::TestMaybePersistToolResult::test_persisted_projection_identifies_exact_raw_evidence -q` failed because `Projection: head-preview` was absent.
- GREEN: the same test passed.
- focused regression: `uv run --python 3.12 pytest tests/tools/test_tool_result_storage.py -q` → `53 passed`.
- existing consumer integration: `uv run --python 3.12 pytest tests/run_agent/test_run_agent.py -q -k 'result_truncation_over_100k'` → `1 passed, 430 deselected`.
- targeted lint: `uv run --python 3.12 ruff check tools/tool_result_storage.py tests/tools/test_tool_result_storage.py` → all checks passed.
- broader `tests/tools` run was attempted but exceeded 300 seconds after surfacing pre-existing-looking failures outside the changed file; no broad-suite green claim is made.
- system `python3` was Python <3.10 and could not parse `int | float`; verification uses the project-compatible Python 3.12 runtime.

### Self-audit gap matrix

| Gap | Current result | Residual boundary |
|---|---|---|
| Raw tool evidence recoverability | durable path already existed; exact raw SHA-256 and omission metadata now added | no automatic readback/hash verifier yet |
| Shape-aware projection | preview is still generic head truncation | reducers for JSON/log/diff/test output need separate acceptance fixtures |
| Routing predicted vs realized economics | contract documented only | no concrete ledger consumer; implementation vetoed as speculative |
| Fallback/retry penalty | visible in design contract only | provider attempt chain is not normalized into one observation |
| Cache continuity penalty | documented as required | provider cache-read/write usage is heterogeneous and not normalized |
| Quality outcome | promotion veto documented | no stable privacy-bounded outcome signal exists |
| Offline router replay | deferred | requires sanitized historical observations and an explicit consumer |
| Migration report convergence | inventoried, not converged: `hermes_cli/migrate.py` uses CLI-only xAI dry-run/apply output; `hermes_cli/codex_runtime_plugin_migration.py` has a source-specific `MigrationReport`; `scripts/docker_config_migrate.py` has backup/restore/post-version verification | a shared additive schema would change several consumers and needs source-by-source contract fixtures first |
| Cross-platform sandbox matrix | partially covered by existing tests | Windows ACL/native-runtime cases remain source-specific |
| Multi-model certification | degraded, not certified | Claude/DeepSeek planners were unhealthy; 2-model fan-out was below minimum quorum 3 |

### Negative-case readback

- No OpenSquilla install or migration occurred.
- No route selection, model/provider, scheduler, task lifecycle, external dispatch, deploy, push, destructive action, or credential path changed.
- No prompt/free text is added to a routing ledger; no routing ledger was created.
- Existing unrelated scheduler evidence-file modifications remain unstaged and outside this slice.
