# Modular Workflow Kernel Phase 8 ADR — Runtime Integration Record-only

Date: 2026-06-13

## Status

Accepted as runtime integration in record-only mode. It prepares claim packets and annotates consolidated proposed task notes, but does not claim tasks, transition to running/review/done, call models, write adapters, or perform external side effects.

## Context

Phase 7 consolidated 19 imported candidates into 4 canonical `status: proposed` tasks. The next long-term-stable step is to attach a runtime integration packet to these consolidated tasks so a future claim executor can consume explicit, auditable claim packets instead of inferring runtime behavior from task text.

## Decision

Add a `Runtime Integration Record-only` layer:

```text
4 consolidated proposed tasks
  → runtime integration packet
  → claim packets prepared_not_claimed
  → readback verification
  → derived taskpool view refresh
  → STOP before claim/running/review/done or adapter write-through
```

The task notes remain `status: proposed`. This is intentional: proposed tasks are not dispatchable and cannot be accidentally picked up by cron/autorun before a separate claim authorization.

## Artifacts

- Schema: `schemas/runtime_integration_packet.schema.json`
- Script: `scripts/workflow_runtime_integration.py`
- Tests: `tests/scripts/test_workflow_runtime_integration.py`
- Runtime packet: `docs/private/workflow-kernel-runtime-integration/decision_brief_pack.run_preview_78eee6afcec1f12a.rti_2c322fc59264ae3d.runtime_integration_packet.json`
- Canonical proposed tasks annotated: 4 notes with `workflow_runtime_packet_id: rti_2c322fc59264ae3d`.

## Invariants

This phase intentionally sets:

```text
runtime_enabled=true
runtime_record_only=true
taskpool_write_enabled=true
```

But keeps execution closed:

```text
taskpool_claim_enabled=false
model_call_enabled=false
adapter_write_enabled=false
external_side_effects_enabled=false
status: proposed
workflow_claim_packet_status: prepared_not_claimed
```

Every runtime target must read back with:

```text
workflow_runtime_packet_id: rti_2c322fc59264ae3d
workflow_runtime_state: recorded_waiting_for_claim_authorization
workflow_claim_packet_status: prepared_not_claimed
runtime_record_only: true
```

## Verification

Commands used:

```bash
python3 scripts/workflow_runtime_integration.py --write --json
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_projection.py --json
python3 scripts/acceptance_compiler.py --json
python3 scripts/workflow_executor_stub.py --json
python3 scripts/workflow_taskpool_import_dry_run.py --json
python3 scripts/workflow_taskpool_import_write.py --json
python3 scripts/workflow_candidate_consolidation.py --json
python3 scripts/workflow_runtime_integration.py --json
./scripts/run_tests.sh \
  tests/scripts/test_workflow_contract_validator.py \
  tests/scripts/test_workflow_projection.py \
  tests/scripts/test_acceptance_compiler.py \
  tests/scripts/test_workflow_executor_stub.py \
  tests/scripts/test_workflow_taskpool_import_dry_run.py \
  tests/scripts/test_workflow_taskpool_import_write.py \
  tests/scripts/test_workflow_candidate_consolidation.py \
  tests/scripts/test_workflow_runtime_integration.py
```

Readback evidence:

```text
runtime_packet_id: rti_2c322fc59264ae3d
runtime_targets: 4
readback_verified: true
taskpool_claim_enabled: false
model_call_enabled: false
adapter_write_enabled: false
external_side_effects_enabled: false
```

## Boundary

Stop before:

```text
Actual task claim
pending/running/review/done lifecycle transitions
model calls
adapter write-through
external messaging or external system mutation
```

Next authorization packet:

- `authorize_claim_executor_recorded_packet`: let a narrow executor consume one prepared claim packet and move one selected proposed task into the governed lifecycle.
- `continue_runtime_record_only`: improve runtime packet validation, dashboard projection, rollback, and reviewer UX without executing claims.
