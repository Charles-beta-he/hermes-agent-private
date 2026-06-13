# Workflow Kernel Scaffold — Phase 26

Status: **KERNEL_SCAFFOLD_COMPAT_ONLY_RUNTIME_UNCHANGED**

## Scope
create workflow_kernel package scaffold and move shared JSON artifact IO behind imported modules while keeping scripts as compatibility CLIs

## New Modules
- `workflow_kernel/__init__.py`
- `workflow_kernel/io.py`

## Compatibility Shims
- `scripts/workflow_architecture_migration_evaluation.py`
- `scripts/cron_provider_health_gate.py`

## Safety Flags
- runtime_switch_enabled: `False`
- external_side_effects_enabled: `False`
- cron_mutation_enabled: `False`
- autorun_enabled_by_this_phase: `False`

## TDD Evidence
- RED: tests/workflow_kernel/test_kernel_scaffold.py failed first with ModuleNotFoundError and missing workflow_kernel.io imports
- GREEN: 12 tests passed across kernel scaffold, architecture migration evaluation, and cron provider health gate

## Boundaries
- no cron wrapper rollout
- no external dispatch expansion
- no autorun enablement
- no evidence directory consolidation/deletion
- no taskpool lifecycle mutation beyond evidence fields

## Next Phase
Phase 27 should extract contracts/ledger helpers behind workflow_kernel modules with parity tests for Phase17-24 behavior.
