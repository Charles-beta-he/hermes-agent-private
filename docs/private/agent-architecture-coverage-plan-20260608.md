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
- [x] Slice 15: Scoped commit for external agent version probe if verification passes. Commit: this scoped commit.
- [ ] Boundary: stop before sending task prompts to external worker processes / task lifecycle writes / external adapter writes unless a narrower executor-specific slice authorizes that capability.

## Decisions

- Report-only first: this captures architecture coverage and gap priorities without granting new authority.
- Canonical ledger remains Hermes Control Tower / taskpool by fact domain; worker/model outputs are proposals/evidence only.
- Parallelism/fork-join, model routing, and worker adapters should be expressed as contracts before execution.
- The first approved runtime boundary is ledger-only start: it may append a durable run record and support idempotent retry, but it still must not invoke external executors, mutate lifecycle, write adapters, or persist learning.
- The second approved runtime boundary is a governed builtin read-only executor: it may append a completed run result after verifying a started run, but external agent process invocation remains disabled until a narrower executor-specific slice exists.
- The third approved runtime boundary is external process probing only: allowlisted agent binaries may be invoked with `--version` and their receipt appended, but no task prompt is sent and no external worker is allowed to act on project/task state yet.

## Files Changed

- docs/private/agent-architecture-coverage-plan-20260608.md
- hermes_cli/event_governed_agents.py
- tests/hermes_cli/test_event_governed_agents.py
