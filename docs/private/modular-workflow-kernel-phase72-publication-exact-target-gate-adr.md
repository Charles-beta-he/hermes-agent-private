# Modular Workflow Kernel Phase 72 ADR: Publication Exact-target Gate

## Status

Accepted — Phase72 defines the exact target/payload gate for any future archive publication apply.

## Decision

Add:

```text
scripts/workflow_publication_exact_target_gate.py
tests/scripts/test_workflow_publication_exact_target_gate.py
```

The gate remains disabled and requires future `target_adapter`, `target_id`, `payload`, `operator_reason`, and `rollback_plan` before any apply phase can be opened.

## Evidence

```text
status=PUBLICATION_EXACT_TARGET_GATE_READY
gate_only=true
selected_target=null
publication_apply_enabled=false
external_publication_enabled=false
external_side_effects_executed=false
```

## Boundary

This phase does not publish, send, mutate cron, or access credentials.
