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
- [ ] Slice 9: Scoped commit for artifact/workflow metadata contracts if verification passes.
- [ ] Boundary: stop before real runtime worker dispatch / task lifecycle writes / external adapter writes unless a separate explicit human gate authorizes that slice.

## Decisions

- Report-only first: this captures architecture coverage and gap priorities without granting new authority.
- Canonical ledger remains Hermes Control Tower / taskpool by fact domain; worker/model outputs are proposals/evidence only.
- Parallelism/fork-join, model routing, and worker adapters should be expressed as contracts before execution.

## Files Changed

- docs/private/agent-architecture-coverage-plan-20260608.md
