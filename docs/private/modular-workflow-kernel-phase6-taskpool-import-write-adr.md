# Modular Workflow Kernel Phase 6 ADR — Taskpool Import Write

Date: 2026-06-13

## Status

Accepted as a narrow canonical taskpool write boundary. Not authorized for task claiming, runtime executor integration, model calls, adapter write-through, external messages, or external side effects.

## Context

Phase 5 generated taskpool import candidates as a local dry-run artifact. The next authorized step is to represent those candidates inside the canonical taskpool SSOT so they can be reviewed, grouped, rejected, or later promoted through normal taskpool governance.

## Decision

Add a `Taskpool Import Write` layer:

```text
taskpool_import_dry_run
  → canonical proposed brain task notes
  → readback verification
  → derived taskpool view refresh
  → STOP before claim/runtime integration
```

Imported candidates are written as `status: proposed`, not `pending`, to prevent dispatch before a separate runtime executor authorization.

## Artifacts

- Schema: `schemas/taskpool_import_write_result.schema.json`
- Script: `scripts/workflow_taskpool_import_write.py`
- Tests: `tests/scripts/test_workflow_taskpool_import_write.py`
- Write output: `docs/private/workflow-kernel-taskpool-import-writes/decision_brief_pack.run_preview_78eee6afcec1f12a.tpwrite_f62e8067efc48f89.taskpool_import_write.json`
- Canonical task notes: 19 notes under `/Users/charles/Documents/brain/05-实践与项目/tasks/` with `workflow_import_write_id: tpwrite_f62e8067efc48f89`.

## Invariants

The write result permits only this mutation:

```text
taskpool_write_enabled=true
```

The following remain false:

```text
taskpool_claim_enabled=false
runtime_enabled=false
model_call_enabled=false
adapter_write_enabled=false
external_side_effects_enabled=false
```

Every created note must include:

```text
status: proposed
workflow_taskpool_import_id: tpdry_a5678ba9e856a763
workflow_import_write_id: tpwrite_f62e8067efc48f89
workflow_import_candidate_id: <candidate_id>
taskpool_import_write_boundary: no_claim_no_runtime_no_model_no_adapter
taskpool_claim_enabled: false
runtime_enabled: false
model_call_enabled: false
adapter_write_enabled: false
external_side_effects_enabled: false
```

The write script is idempotent by `workflow_import_candidate_id`: rerunning does not create duplicate notes for already-imported candidates.

## Verification

Commands used for this phase:

```bash
python3 scripts/workflow_taskpool_import_write.py --write --json
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_projection.py --json
python3 scripts/acceptance_compiler.py --json
python3 scripts/workflow_executor_stub.py --json
python3 scripts/workflow_taskpool_import_dry_run.py --json
python3 scripts/workflow_taskpool_import_write.py --json
./scripts/run_tests.sh \
  tests/scripts/test_workflow_contract_validator.py \
  tests/scripts/test_workflow_projection.py \
  tests/scripts/test_acceptance_compiler.py \
  tests/scripts/test_workflow_executor_stub.py \
  tests/scripts/test_workflow_taskpool_import_dry_run.py \
  tests/scripts/test_workflow_taskpool_import_write.py
```

Readback evidence:

```text
workflow_import_write_id: tpwrite_f62e8067efc48f89
created task notes: 19
status distribution: proposed=19
readback_verified: true
```

## Boundary

Stop before:

```text
Runtime Executor Integration
Task claim / running / review / done transitions
Adapter Write-through
External messaging or external system mutation
```

Next authorization packet:

- `continue_proposed_candidate_review_only`: keep work in review/grouping/rejection mode.
- `authorize_runtime_executor_integration`: allow a narrow runtime executor integration against selected proposed candidates.
