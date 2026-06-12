---
title: Modular Workflow Kernel Phase 1 Seed
lane: evolution
priority: P1
project: hermes
architecture_target: workflow-kernel
autorun_allowed: false
requires_multimodel: true
source: user-approved-full-landing
status: seed
---

# Modular Workflow Kernel Phase 1 Seed

## Objective

Land the first safe, reversible contract layer for a modular self-advancing workflow base that can later produce Workflow Packs, Agent Packs, evidence-ledger products, model-routing governance, and enterprise delivery packages.

## Scope

Phase 1 is schema/registry/validator only. It must not change live runtime behavior.

## Required artifacts

- `schemas/workflow_contract.schema.json`
- `config/workflow_registry.json`
- `scripts/workflow_contract_validator.py`
- `docs/private/modular-workflow-kernel-phase1-adr.md`
- `tests/scripts/test_workflow_contract_validator.py`

## Acceptance criteria

- `command:python3 scripts/workflow_contract_validator.py --json` returns `status=PASS`.
- Focused tests for validator pass.
- Registry contains at least one workflow pack with runtime/write-through/external side effects disabled.
- Every module has a model policy, evidence requirements, blocked actions, side-effect level, and risk level.
- Acceptance compiler policy requires compile-before-execution and freeze-before-execution.
- Human gate packet fields include decision, facts, options, default, blast radius, rollback, and verification.
- Evidence policy is structured-prefix only.
- No runtime loop, gateway, taskpool transition, external adapter, push/deploy/send/delete/secret boundary is opened.

## Red lines

- Do not mark implementation `done` from advisory-only model output.
- Do not enable write-through adapters.
- Do not allow external side effects.
- Do not mutate production systems.
- Do not rewrite acceptance after a failed run.
- Do not let model/router/module output become canonical lifecycle state.

## Suggested follow-up tasks

1. [x] Add read-only projection for workflow registry health.
2. [x] Add route trace schema and model registry seed.
3. [x] Add acceptance compiler dry-run for a concrete `decision_brief_pack` run.
4. [ ] Add taskpool import command for workflow seed tasks, still dry-run by default.
5. [x] Add a pinned run lockfile schema before any runtime execution integration.
6. [ ] Add local reversible executor stub only after acceptance dry-run is validated and a new human gate approves runtime transition.

## Phase 2 evidence

- `schemas/route_trace.schema.json`
- `schemas/run_lockfile.schema.json`
- `scripts/workflow_projection.py`
- `docs/private/modular-workflow-kernel-phase2-adr.md`
- `docs/private/workflow-kernel-projections/decision_brief_pack.run_preview_78eee6afcec1f12a.run_lockfile.json`
- `docs/private/workflow-kernel-projections/decision_brief_pack.run_preview_78eee6afcec1f12a.route_traces.json`
- `docs/private/workflow-kernel-projections/decision_brief_pack.run_preview_78eee6afcec1f12a.summary.json`
- `command:./scripts/run_tests.sh tests/scripts/test_workflow_contract_validator.py tests/scripts/test_workflow_projection.py => 14 passed`
- `schemas/acceptance_bundle.schema.json`
- `scripts/acceptance_compiler.py`
- `docs/private/modular-workflow-kernel-phase3-acceptance-adr.md`
- `docs/private/workflow-kernel-acceptance/decision_brief_pack.run_preview_78eee6afcec1f12a.acc_3c811427ac76ce88.acceptance.json`
- `command:./scripts/run_tests.sh tests/scripts/test_workflow_contract_validator.py tests/scripts/test_workflow_projection.py tests/scripts/test_acceptance_compiler.py => 16 passed`
