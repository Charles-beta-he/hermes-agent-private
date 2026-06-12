# Modular Workflow Kernel Phase 5 ADR — Taskpool Import Dry-run

Date: 2026-06-13

## Status

Accepted as a local dry-run boundary. Not authorized for canonical taskpool write, task claim, runtime execution, model calls, adapter write-through, or external side effects.

## Context

Phase 4 established a local reversible executor stub that converts a frozen acceptance bundle into preview steps while keeping all runtime and side-effect flags false. The next safe layer is to preview how those executor steps would become canonical taskpool tasks without mutating the taskpool SSOT.

## Decision

Add a `Taskpool Import Dry-run` layer:

```text
executor_dry_run_result
  → taskpool_import_dry_run candidates
  → STOP before canonical brain/kanban write
```

The dry-run artifact contains candidate task previews only:

- frontmatter preview
- body preview
- evidence preview
- human gate packet
- rollback plan
- explicit side-effect flags

It does not write brain markdown notes, kanban rows, taskpool claims, runtime lifecycle transitions, adapter state, messages, or external systems.

## Artifacts

- Schema: `schemas/taskpool_import_dry_run.schema.json`
- Script: `scripts/workflow_taskpool_import_dry_run.py`
- Tests: `tests/scripts/test_workflow_taskpool_import_dry_run.py`
- Dry-run output: `docs/private/workflow-kernel-taskpool-import-dry-runs/decision_brief_pack.run_preview_78eee6afcec1f12a.tpdry_a5678ba9e856a763.taskpool_import_dry_run.json`

## Invariants

The following must remain false:

```text
taskpool_write_enabled=false
taskpool_claim_enabled=false
runtime_enabled=false
model_call_enabled=false
adapter_write_enabled=false
external_side_effects_enabled=false
```

Each generated candidate must include:

```text
write_enabled=false
dry_run_candidate_only=true
workflow_id
workflow_run_id
workflow_executor_result_id
workflow_import_dry_run_id
source_criterion
```

Safety-flag criteria remain blocked candidates and require human gate before write/import.

## Consequences

Positive:

- The workflow kernel now has a deterministic bridge from executor preview to taskpool import candidates.
- Candidate shape can be inspected and tested before touching canonical taskpool state.
- The taskpool remains the future SSOT without creating a competing pool.

Tradeoffs:

- This still does not create real taskpool work.
- Candidate granularity is criterion-based; later write/import may consolidate candidates before canonical insertion.
- Human authorization is still required for taskpool import write.

## Verification

Commands used for this phase:

```bash
python3 scripts/workflow_executor_stub.py --write --json
python3 scripts/workflow_taskpool_import_dry_run.py --write --json
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_projection.py --json
python3 scripts/acceptance_compiler.py --json
python3 scripts/workflow_executor_stub.py --json
python3 scripts/workflow_taskpool_import_dry_run.py --json
./scripts/run_tests.sh \
  tests/scripts/test_workflow_contract_validator.py \
  tests/scripts/test_workflow_projection.py \
  tests/scripts/test_acceptance_compiler.py \
  tests/scripts/test_workflow_executor_stub.py \
  tests/scripts/test_workflow_taskpool_import_dry_run.py
```

## Boundary

Stop before:

```text
Taskpool Import Write
Runtime Executor Integration
Adapter Write-through
```

Next authorization packet:

- `authorize_taskpool_import_write`: create canonical taskpool entries from selected candidates.
- Safer alternative: `continue_taskpool_import_dry_run_only`: improve candidate grouping, schema validation, dashboard projection, and reviewer UX without writing SSOT.
