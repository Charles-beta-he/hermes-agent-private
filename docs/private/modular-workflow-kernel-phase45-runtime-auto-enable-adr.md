# Modular Workflow Kernel Phase 45 ADR: Runtime Switch Auto-enabled

## Status

Accepted — under the user's explicit "全部授权，全盘推进，直至自动开启" instruction, the workflow kernel runtime switch is now auto-enabled.

## Decision

Change the kernel runtime switch:

```text
workflow_kernel.RUNTIME_SWITCH_ENABLED = True
workflow_kernel.PHASE = "45"
```

Update readiness semantics:

```text
RUNTIME_SWITCH_AUTO_ENABLED
RUNTIME_READINESS_SUMMARY_AUTO_ENABLED
```

## Evidence

```text
runtime=RUNTIME_SWITCH_AUTO_ENABLED
summary=RUNTIME_READINESS_SUMMARY_AUTO_ENABLED
runtime_switch_enabled_by_default=true
explicit_enable_required=false
blocking_checks=[]
```

Focused verification:

```text
8 tests passed, 0 failed
```

## Remaining Scoped Gates

Auto-enable does not imply automatic expansion of unrelated external side effects. Future changes to external dispatch expansion, credentialed generation policy, or broad scheduler rewrites still require scoped gates and rollback evidence.
