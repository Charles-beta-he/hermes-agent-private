# Modular Workflow Kernel Phase 54 ADR: Unattended Dispatch Policy Gate

## Status

Accepted — Phase54 defines a fail-closed unattended dispatch policy gate after one verified guarded Telegram send.

## Decision

Add:

- `scripts/workflow_unattended_dispatch_policy_gate.py`
- `tests/scripts/test_workflow_unattended_dispatch_policy_gate.py`
- `docs/private/workflow-kernel-unattended-dispatch-policy/`

The policy defines:

- target allowlist;
- purpose allowlist;
- rate limits;
- idempotency key fields;
- duplicate policy;
- dry-run preview requirement;
- receipt ledger path;
- correction path;
- monitoring and kill switches.

## Evidence

```text
status=UNATTENDED_DISPATCH_POLICY_GATE_READY
default_mode=dry_run_preview
unattended_dispatch_enabled=false
external_side_effects_executed=false
```

## Boundary

This phase does not enable unattended sends. It compiles policy and a dry-run instruction only.

Next larger divergence:

1. Phase55 unattended dry-run scheduler — schedule dry-run previews only.
2. Phase56 Control Tower policy editor / approval UI — expose policy changes to operator review.
