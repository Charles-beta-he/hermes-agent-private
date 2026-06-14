# Modular Workflow Kernel Phase 76 ADR: Publication Apply Implementation Choice

## Status

Accepted — Phase76 records safe default `A_KEEP_PRIVATE_NO_APPLY` because no exact target/payload apply scheme is open.

## Decision

Add:

```text
scripts/workflow_publication_apply_implementation_choice.py
tests/scripts/test_workflow_publication_apply_implementation_choice.py
```

The choice is record-only and keeps publication apply disabled.

## Evidence

```text
status=PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_RECORDED
choice=A_KEEP_PRIVATE_NO_APPLY
record_only=true
exact_target_present=false
publication_apply_enabled=false
external_publication_enabled=false
```

## Boundary

This is not an apply phase and does not represent a large divergence. A future exact target/payload scheme would be the next real single implementation-point choice.
