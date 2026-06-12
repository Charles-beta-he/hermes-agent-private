# Modular Workflow Kernel Phase 4 Local Reversible Executor Stub ADR

Date: 2026-06-12
Status: accepted for executor-stub dry-run implementation
Scope: local reversible executor preview for a frozen acceptance bundle.

## Decision

Add a local reversible executor stub that consumes the frozen acceptance bundle and emits an executor dry-run result. This is still not a runtime executor. It is a preview artifact that shows which acceptance criteria would become executable steps later, while keeping every side-effect flag disabled.

Implemented artifacts:

- `schemas/executor_dry_run_result.schema.json`
- `scripts/workflow_executor_stub.py`
- `tests/scripts/test_workflow_executor_stub.py`
- generated result:
  - `docs/private/workflow-kernel-executor-dry-runs/decision_brief_pack.run_preview_78eee6afcec1f12a.execdry_412861ba7acffd38.executor_dry_run.json`

## Boundary

The executor stub has these hard-coded false authority flags:

```text
runtime_enabled=false
taskpool_claim_enabled=false
model_call_enabled=false
adapter_write_enabled=false
external_side_effects_enabled=false
```

It writes only a local JSON artifact under `docs/private/workflow-kernel-executor-dry-runs/`.

It does not:

- claim or mutate taskpool tasks;
- call any model provider;
- invoke tool adapters;
- write to external systems;
- send notifications;
- update runtime state;
- mark any workflow done.

## Why stop here

This phase reaches the next方案边界: the system can now preview how a frozen acceptance bundle would become execution steps, but it still has no authority to execute those steps against taskpool/runtime/adapters.

The next meaningful phase is `taskpool import / runtime integration`. That would introduce canonical lifecycle effects and must be separately authorized with an adapter/taskpool-specific gate.

## Verification evidence

- `python3 scripts/workflow_executor_stub.py --json` returns `dry_run_only=true`, `local_reversible_only=true`, 19 steps, and all side-effect/claim/model/adapter flags false.
- `python3 scripts/workflow_executor_stub.py --write --json` writes the local dry-run result artifact.
- `./scripts/run_tests.sh tests/scripts/test_workflow_contract_validator.py tests/scripts/test_workflow_projection.py tests/scripts/test_acceptance_compiler.py tests/scripts/test_workflow_executor_stub.py` passes 19 tests.
- `python3 scripts/workflow_contract_validator.py --json` returns PASS and recognizes `executor_dry_run_schema`.

## Next human gate

Choose one:

1. `continue_local_dry_run_only` — add more evaluator/projection artifacts, no canonical taskpool mutation.
2. `authorize_taskpool_import_dry_run` — generate taskpool import candidates, but do not write them.
3. `authorize_taskpool_import_write` — create canonical taskpool tasks from workflow seed/import candidates. This crosses into taskpool SSOT mutation and requires explicit approval.
4. `authorize_runtime_executor_integration` — connect executor to taskpool lifecycle. This is higher risk and should not be the next default.

Recommended default: `authorize_taskpool_import_dry_run` before any write.
