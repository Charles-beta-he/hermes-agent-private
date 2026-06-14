# Modular Workflow Kernel Phase 73 ADR: Exact-target Apply Dry-run Compiler

## Status

Accepted — Phase73 compiles a dry-run-only preview for any future exact-target publication apply.

## Decision

Add:

```text
scripts/workflow_exact_target_apply_dry_run_compiler.py
tests/scripts/test_workflow_exact_target_apply_dry_run_compiler.py
```

The compiler consumes Phase72 gate and Phase68 archive evidence, but does not select a target/payload and does not publish.

## Evidence

```text
status=EXACT_TARGET_APPLY_DRY_RUN_COMPILER_READY
dry_run_only=true
would_publish=false
publication_apply_enabled=false
external_side_effects_executed=false
```

## Boundary

No publication, external send, cron mutation, unattended dispatch, or credential access is performed.
