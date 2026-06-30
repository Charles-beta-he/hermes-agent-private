> Autonomous Runtime Governance disposition:
> - Current role: source-adr
> - Canonical architecture entrypoint: `/Users/charles/.hermes/hermes-agent/docs/private/autonomous-runtime-governance/00-index.md`
> - Canonical landing: `11-adr-index.md / 10-migration-roadmap.md`
> - Do not extend this file with new target architecture. Keep it as source/evidence/history; land new target-model changes in `docs/private/autonomous-runtime-governance/` and register them in `11-adr-index.md` or `13-local-brain-absorption.md`.

# Modular Workflow Kernel Phase 25 ADR: Architecture Migration Evaluation

## Status

Accepted — migration to a new event-governed Modular Workflow Kernel architecture is recommended, but only by incremental strangler migration. No runtime switch is made in this phase.

## Context

Phases 17-24 created a safe ladder from external-apply contracts through local outbox, Telegram smoke test, governed notification policy, architecture self-check, cron/provider triage, provider probe ledger, and cron provider health gate.

Current evidence:

```text
Phase 21: DEGRADED_NOT_BLOCKED
Phase 22: cron failures classified into provider_generation/script_network_failure/telegram_delivery
Phase 23: provider probe DEGRADED_PARTIAL; xiaomi tls_error
Phase 24: GATE_COMPILED_NO_MUTATION; failed_job_count=2
```

The architecture now has enough verified policy artifacts to consider consolidation. However, it is not safe to replace the runtime in one pass because taskpool, mission, cron, and external dispatch semantics are evidence-sensitive.

## Decision

Select path A: **incremental strangler migration**.

Reject:

- big-bang kernel rewrite;
- status quo plus more ad-hoc scripts only.

Target architecture:

```text
Event-governed Modular Workflow Kernel
```

Core modules should eventually become:

```text
kernel.contracts
kernel.ledger
kernel.policy
kernel.adapters
kernel.runtime
kernel.cron_gate
```

Existing scripts stay as compatibility CLIs until parity tests pass.

## Migration Lanes

```text
Phase 25: migration evaluation and SSOT — this phase
Phase 26: kernel package scaffold
Phase 27: contracts/ledger extraction
Phase 28: cron health wrapper pilot
Phase 29: external dispatch manual runner
Phase 30: projection consolidation
```

## Hard Boundaries

Do not use this ADR to authorize:

- global autorun enablement;
- cron mutation;
- external dispatch expansion;
- config migration without backup/diff;
- deleting or renaming historical evidence without a manifest-backed consolidation phase.

## Acceptance Before Runtime Switch

Before any runtime switch, require:

1. Existing Phase 17-24 tests pass through new module wrappers.
2. Existing script CLIs remain backward compatible.
3. Taskpool selfcheck and proof-check pass.
4. Mission stage-report can cite new kernel artifact paths.
5. Rollback is a one-line CLI shim revert or wrapper disable.

## Evidence

Generated report:

```text
docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.json
docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.md
```

Compiler:

```text
scripts/workflow_architecture_migration_evaluation.py
```

Tests:

```text
tests/scripts/test_workflow_architecture_migration_evaluation.py
```

## Consequences

Benefits:

- Makes the new architecture explicit before migration.
- Converts current script sprawl into a typed target module map.
- Preserves verified behavior and rollback.
- Prevents premature runtime switch.

Tradeoffs:

- Adds one more planning/evidence artifact before code consolidation.
- Does not yet reduce script duplication or naming drift.

## Next Phase

Phase 26 should create the kernel package scaffold and move shared helpers behind imported modules while preserving existing script CLI behavior.
