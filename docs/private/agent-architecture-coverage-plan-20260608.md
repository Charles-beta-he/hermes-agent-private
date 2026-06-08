# Agent Architecture Coverage Plan — 2026-06-08

Goal: turn the prior Claude Code / Hermes agent-architecture coverage discussion into a Hermes-owned, evidence-grounded control-plane artifact and land the first safe implementation slices until the next boundary requires runtime authority or human approval.
Status: in_progress
SSOT: this file

## Scope

- Codify the architecture coverage matrix as Hermes report-only control-plane contract data.
- Keep Hermes as canonical ledger / policy / evidence / certification control plane.
- Treat Claude Code / Codex / OpenCode / delegate workers as bounded execution adapters, not new fact sources.
- Land low-risk deterministic/report-only slices first; do not enable broad worker execution, lifecycle mutation, external writes, or automatic done.

## Slices

- [x] Slice 0: Establish SSOT and boundary file — docs/private/agent-architecture-coverage-plan-20260608.md
- [x] Slice 1: Add report-only agent architecture coverage contract to hermes_cli.event_governed_agents with CLI JSON access and tests.
- [x] Slice 1b: Add worker adapter result contract so Claude Code/Codex/OpenCode outputs are evidence/proposal only and cannot smuggle lifecycle/external-write authority.
- [x] Slice 2: Run focused tests and py_compile for the touched control-plane module.
- [x] Slice 3: Scoped commit if verification passes. Commit: 06fbbe238 Add agent architecture coverage contract.
- [x] Slice 4: Add fork/join graph contract validation as ledger projection only; runtime parallel dispatch remains disabled.
- [x] Slice 5: Add multi-model certification packet validation; model consensus remains non-fact and cannot mark done.
- [x] Slice 6: Scoped commit for graph/certification contracts if verification passes. Commit: 58f086b5f Add agent workflow graph certification contracts.
- [x] Slice 7: Add artifact pointer contract so evidence remains summary/hash/pointer only in the hot ledger.
- [x] Slice 8: Add workflow metadata contract for lane/workflow_state/checkpoint substate without lifecycle authority.
- [x] Slice 9: Scoped commit for artifact/workflow metadata contracts if verification passes. Commit: 268e63de0 Add agent artifact workflow metadata contracts.
- [x] Slice 10: Add first real started runtime boundary: ledger-only bounded run start with durable JSONL record, idempotent retry, no external executor invocation, no lifecycle mutation, no adapter write-through.
- [x] Slice 11: Scoped commit for ledger-only runtime start if verification passes. Commit: 9a5440557 Start bounded agent runtime ledger runs.
- [x] Slice 12: Add first governed executor invocation: allowlisted Hermes builtin read-only executor completes a started run and appends result evidence to ledger; unknown/external executors remain rejected.
- [x] Slice 13: Scoped commit for builtin read-only executor if verification passes. Commit: 747f419d9 Execute bounded builtin agent runtime runs.
- [x] Slice 14: Add first external process boundary: allowlisted Claude/Codex/OpenCode-style agent version probe appends a receipt to ledger; no task prompt/tool execution/lifecycle/adapter write.
- [x] Slice 15: Scoped commit for external agent version probe if verification passes. Commit: df9fbc6bb Probe external agent runtime executors.
- [x] Slice 16: Add first external task-prompt boundary: allowlisted Claude/Codex/OpenCode-style agent receives a no-tool/no-write dry-run prompt, full output is captured as an artifact, and only artifact pointer/hash/summary enters the ledger.
- [x] Slice 17: Scoped commit for external prompt dry-run if verification passes. Commit: 9f2bda9b5 Dry run external agent prompt execution.
- [x] Slice 18: Add external readonly context packet: allowlisted agent receives a no-tool/no-write prompt that references a file artifact by URI/hash; inline context is rejected and ledger stores only context/output artifact pointers.
- [x] Slice 19: Scoped commit for external readonly context packet if verification passes. Commit: 5da09a457 Attach readonly context to external agent runs.
- [x] Slice 20: Add external proposal artifact intake: worker proposals are accepted only as review_proposed artifacts with evidence-backed claims and failure classification; done/lifecycle authority remains rejected.
- [x] Slice 21: Scoped commit for proposal artifact intake if verification passes. Commit: 95c20b13b Record external agent proposal artifacts.
- [x] Slice 22: Add multi-model certification promotion packet: certification artifacts must bind to a recorded proposal, require quorum/truth gate/reviewer evidence/claim evidence, and may only target certification_passed_for_review.
- [x] Slice 23: Scoped commit for certification promotion packet if verification passes. Commit: 1146f643a Record multi-model certification promotions.
- [x] Slice 24: Add human/elevated done authorization projection: require certification_recorded, elevated actor, approval decision, target done, TTL, and certification artifact hash binding while keeping fact mutation/write-through disabled.
- [x] Slice 25: Scoped commit for done authorization projection if verification passes. Commit: this scoped commit.
- [ ] Boundary: stop before ledger-local fact mutation, external agent tools/file edits/shell commands, task lifecycle writes to external adapters, or external adapter writes unless a narrower executor-specific slice authorizes that capability.

## Decisions

- Report-only first: this captures architecture coverage and gap priorities without granting new authority.
- Canonical ledger remains Hermes Control Tower / taskpool by fact domain; worker/model outputs are proposals/evidence only.
- Parallelism/fork-join, model routing, and worker adapters should be expressed as contracts before execution.
- The first approved runtime boundary is ledger-only start: it may append a durable run record and support idempotent retry, but it still must not invoke external executors, mutate lifecycle, write adapters, or persist learning.
- The second approved runtime boundary is a governed builtin read-only executor: it may append a completed run result after verifying a started run, but external agent process invocation remains disabled until a narrower executor-specific slice exists.
- The third approved runtime boundary is external process probing only: allowlisted agent binaries may be invoked with `--version` and their receipt appended, but no task prompt is sent and no external worker is allowed to act on project/task state yet.
- The fourth approved runtime boundary is external prompt dry-run only: an allowlisted agent may receive a prompt with tools disabled and permission mode constrained; stdout/stderr are stored as an artifact while the ledger stores only pointer/hash/summary. The agent still cannot edit files, run shell tools, mutate lifecycle, or write adapters.
- The fifth approved runtime boundary is external readonly context packet: an allowlisted agent may receive a prompt containing only a context artifact URI/hash, while inline context and write/tool/lifecycle authority remain rejected. Ledger stores context pointer/hash and output artifact pointer/hash/summary only.
- The sixth approved runtime boundary is external proposal artifact intake: an external worker proposal may be recorded only as a review_proposed artifact with evidence-backed claims and failure classification. It is still not a fact, cannot mark done, and requires later certification/human gate.
- The seventh approved runtime boundary is multi-model certification promotion: a certification artifact may be recorded only if it binds to the recorded proposal, has at least two reviewers, quorum >= 2, truth gate enabled, reviewer/claim evidence, and lifecycle_target=certification_passed_for_review. It still cannot mark done or mutate lifecycle.
- The eighth approved runtime boundary is human/elevated done authorization projection: an elevated human/admin may approve target_status=done only against the exact certification artifact hash and unexpired TTL, but this projection still keeps fact mutation and write-through disabled.

## Files Changed

- docs/private/agent-architecture-coverage-plan-20260608.md
- hermes_cli/event_governed_agents.py
- tests/hermes_cli/test_event_governed_agents.py
