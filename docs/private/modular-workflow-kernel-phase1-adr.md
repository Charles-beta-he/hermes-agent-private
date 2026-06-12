# Modular Workflow Kernel Phase 1 ADR

Date: 2026-06-12
Status: accepted for Phase 1 seed implementation
Scope: Hermes local self-advancing workflow / Agent Builder control-plane contracts

## Decision

Phase 1 lands a **Workflow Contract Seed Pack** instead of directly changing Hermes runtime behavior.

The first implementation layer is:

- `schemas/workflow_contract.schema.json` — canonical contract shape for a self-advancing workflow pack.
- `config/workflow_registry.json` — local registry containing the first disabled-by-default workflow pack (`decision_brief_pack`).
- `scripts/workflow_contract_validator.py` — read-only validator for schema/registry/taskpool seed invariants.
- `docs/private/taskpool-seeds/modular-workflow-kernel-seed.md` — taskpool seed for future governed implementation.
- focused tests for the validator and fail-closed phase-1 constraints.

## Why not direct runtime mutation in Phase 1

The target architecture touches lifecycle, routing, model choice, evidence, acceptance, dashboard projection, and future external adapter boundaries. Direct runtime integration before contract validation would risk creating a second fact source or allowing a module/model/router to change behavior without replayable evidence.

Phase 1 therefore keeps all execution authority disabled:

- `runtime_enabled: false`
- `write_through_enabled: false`
- `external_side_effects_enabled: false`
- production run mode remains `pinned`
- dynamic routing is shadow-only / recommendation-only
- no push, deploy, destructive delete, credential write, external send, or production write authority is opened

## Fact-source boundaries

- **Mission ledger** owns long-lived objective, budget, stage reports, and human decisions.
- **Taskpool** owns lifecycle state, dispatchability, claim/lease, review/done/retry/blocked transitions.
- **Evidence ledger** owns completion proof: command output, artifacts, files, source refs, verification results, and human decisions.
- **Project / Agent / Workflow profiles** own scoped business rules, model policies, acceptance expectations, and module contracts.
- **Dashboard / Control Tower** are projections and operator surfaces; they are not canonical truth.
- **Models and workers** may propose, execute bounded slices, or produce evidence; they do not directly own `done` authority.

## Phase 1 acceptance

A Phase 1 workflow registry is valid only if:

1. Every workflow has a stable id/version/objective.
2. Runtime/write-through/external side-effect flags are all false.
3. Every module declares input/output schema, model policy, evidence requirements, risk, side-effect level, and blocked actions.
4. Acceptance checks are compiled and frozen before execution.
5. Human gate packet fields are explicit.
6. Evidence policy accepts only structured prefixes.
7. The validator can run read-only and return JSON.
8. Tests cover valid and fail-closed invalid registries.

## Runtime entry criteria for a later phase

Runtime integration may start only after this contract layer passes and a later ADR opens one narrow boundary at a time:

1. read-only projection
2. dry-run command proposal
3. human approval lifecycle
4. disabled-by-default executor plan
5. local reversible executor
6. adapter-specific write-through with readback and rollback
7. external delivery authorization

## Answer to product boundary question

The base is **not limited to deliverable agent products**. It can also produce:

- Workflow Packs
- Agent Packs
- Evidence / compliance ledgers
- model-routing governance layers
- acceptance compilers
- self-advancing operations workflows
- enterprise delivery packages
- internal personal-assistant workflows

Agent products are one packaging layer over the same kernel, not the only output.
