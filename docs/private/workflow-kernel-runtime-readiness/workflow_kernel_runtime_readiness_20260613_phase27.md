# Workflow Kernel Runtime Readiness — Phase 27

Status: **RUNTIME_SWITCH_AUTO_ENABLED**
Switch mode: `compatibility_shim`
Runtime switch enabled by default: `True`
Explicit enable required: `False`

## Compatibility CLIs
- `scripts/workflow_architecture_migration_evaluation.py`
- `scripts/cron_provider_health_gate.py`
- `workflow_kernel/cron_wrappers.py`

## Required Tests
- `tests/workflow_kernel/test_kernel_scaffold.py`
- `tests/workflow_kernel/test_runtime_switch_readiness.py`
- `tests/scripts/test_workflow_architecture_migration_evaluation.py`
- `tests/scripts/test_cron_provider_health_gate.py`

## Blocked Capabilities
- cron_mutation
- external_dispatch_expansion
- autorun_enablement
- config_migration

## Next Closed Boundary
runtime switch is auto-enabled; future expansion still requires scoped rollout gates for new external dispatch or broad scheduler changes
