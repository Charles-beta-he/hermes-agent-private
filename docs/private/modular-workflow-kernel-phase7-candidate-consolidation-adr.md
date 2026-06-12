# Modular Workflow Kernel Phase 7 ADR — Candidate Consolidation Write

Date: 2026-06-13

## Status

Accepted as a narrow canonical taskpool consolidation write. Not authorized for task claim, runtime executor integration, model calls, adapter write-through, archiving/deleting source candidate notes, or external side effects.

## Context

Phase 6 imported 19 workflow-kernel candidates as canonical `status: proposed` task notes. Keeping all 19 as future runtime-entry tasks would over-fragment the workflow kernel and increase scheduling/review overhead. The next safe layer is to consolidate them into a smaller set of reviewable proposed tasks while preserving the original candidate notes as evidence.

## Decision

Add a `Candidate Consolidation Write` layer:

```text
19 imported proposed candidate notes
  → 4 grouped proposed consolidation tasks
  → readback verification
  → derived taskpool view refresh
  → STOP before runtime executor integration
```

The source candidate notes are not archived, deleted, claimed, or executed. The consolidated tasks carry source candidate ids, source task ids, source criteria, and explicit no-runtime flags.

## Consolidation groups

```text
contract_acceptance_governance: 6 source candidates
module_contracts: 5 source candidates
model_routes: 5 source candidates
safety_flags: 3 source candidates, requires_human_gate=true
```

## Artifacts

- Schema: `schemas/taskpool_candidate_consolidation_result.schema.json`
- Script: `scripts/workflow_candidate_consolidation.py`
- Tests: `tests/scripts/test_workflow_candidate_consolidation.py`
- Result artifact: `docs/private/workflow-kernel-candidate-consolidations/decision_brief_pack.run_preview_78eee6afcec1f12a.tpcon_4421e19ba058d222.candidate_consolidation.json`
- Canonical consolidated proposed notes: 4 notes under `/Users/charles/Documents/brain/05-实践与项目/tasks/` with `workflow_consolidation_id: tpcon_4421e19ba058d222`.

## Invariants

Allowed mutation:

```text
taskpool_write_enabled=true
```

Still false:

```text
taskpool_claim_enabled=false
runtime_enabled=false
model_call_enabled=false
adapter_write_enabled=false
external_side_effects_enabled=false
```

Every consolidated task must read back as:

```text
status: proposed
workflow_source_import_write_id: tpwrite_f62e8067efc48f89
workflow_consolidation_id: tpcon_4421e19ba058d222
workflow_consolidation_group_id: <group_id>
taskpool_candidate_consolidation_boundary: no_claim_no_runtime_no_model_no_adapter
```

## Verification

Commands used:

```bash
python3 scripts/workflow_candidate_consolidation.py --write --json
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_projection.py --json
python3 scripts/acceptance_compiler.py --json
python3 scripts/workflow_executor_stub.py --json
python3 scripts/workflow_taskpool_import_dry_run.py --json
python3 scripts/workflow_taskpool_import_write.py --json
python3 scripts/workflow_candidate_consolidation.py --json
./scripts/run_tests.sh \
  tests/scripts/test_workflow_contract_validator.py \
  tests/scripts/test_workflow_projection.py \
  tests/scripts/test_acceptance_compiler.py \
  tests/scripts/test_workflow_executor_stub.py \
  tests/scripts/test_workflow_taskpool_import_dry_run.py \
  tests/scripts/test_workflow_taskpool_import_write.py \
  tests/scripts/test_workflow_candidate_consolidation.py
```

Readback evidence:

```text
source candidate notes: 19, status=proposed
consolidated notes: 4, status=proposed
taskpool_claim_enabled=false
runtime_enabled=false
model_call_enabled=false
adapter_write_enabled=false
external_side_effects_enabled=false
```

## Boundary

Stop before:

```text
Runtime Executor Integration
Task claim / running / review / done transitions
Adapter Write-through
External messaging or external system mutation
Source candidate archiving/deletion
```

Next authorization packet:

- `continue_proposed_candidate_review_only`: review/group/reject while proposed-only.
- `authorize_runtime_executor_integration`: open a narrow runtime executor integration against selected consolidated proposed tasks.
