> Autonomous Runtime Governance disposition:
> - Current role: source-kernel-migration
> - Canonical architecture entrypoint: `/Users/charles/.hermes/hermes-agent/docs/private/autonomous-runtime-governance/00-index.md`
> - Canonical landing: `10-migration-roadmap.md`
> - Do not extend this file with new target architecture. Keep it as source/evidence/history; land new target-model changes in `docs/private/autonomous-runtime-governance/` and register them in `11-adr-index.md` or `13-local-brain-absorption.md`.

# Workflow Kernel Architecture Migration Evaluation — Phase 25

Status: **MIGRATION_RECOMMENDED_INCREMENTAL_STRANGLER**

## Objective
Evaluate migration from scattered workflow-kernel scripts and ledgers into an event-governed Modular Workflow Kernel without changing runtime behavior yet.

## Evidence Summary
```json
{
  "missing_inputs": [],
  "phase21_score": 6.8,
  "phase21_status": "DEGRADED_NOT_BLOCKED",
  "phase22_class_counts": {
    "provider_generation": 7,
    "script_network_failure": 1,
    "telegram_delivery": 1
  },
  "phase22_failed_count": 9,
  "phase23_classification_counts": {
    "reachable": 5,
    "tls_error": 1
  },
  "phase23_degraded_endpoints": [
    "xiaomi"
  ],
  "phase23_status": "DEGRADED_PARTIAL",
  "phase24_decision_counts": {
    "retry_allowed_by_provider_health_manual_only": 1,
    "skip_provider_health_degraded": 1
  },
  "phase24_degraded_endpoints": [
    "xiaomi"
  ],
  "phase24_failed_job_count": 2,
  "phase24_status": "GATE_COMPILED_NO_MUTATION"
}
```

## Current Architecture
Type: layered local-control-plane with growing modular workflow kernel

### Components
- **conversation/runtime tools** — interactive execution and explicit user-authorized side effects
- **mission ledger** — long-running objective, tick budget, stage reports, human gates
- **taskpool control plane** — canonical task state, proof/evidence, lifecycle transitions
- **workflow kernel scripts** — local validators, policy compilers, dry-run executors, evidence generators
- **cron scheduler** — scheduled automation, currently provider/network health-gated by read-only artifacts
- **external dispatch adapters** — Telegram smoke tested; unattended dispatch remains disabled

### Known Debt
- kernel behavior is distributed across scripts rather than a single typed runtime package
- some evidence directories from earlier iterations have naming drift and should be consolidated later
- cron health gate is not yet wired into job execution wrappers
- taskpool/mission/cron schemas are connected by conventions, not a unified event schema

## Target Architecture
Name: **Event-governed Modular Workflow Kernel**

### Principles
- single event/evidence ledger projection for taskpool, mission, cron, and external dispatch
- policy-first execution: plan/dry-run/health-gate before any side effect
- adapter registry with explicit authority, readback, rollback, idempotency, and blast radius
- workers execute proposals; lifecycle and external side effects remain governed by gates
- migration by strangler facade, not big-bang replacement

### Core Modules
- `kernel.contracts: typed schemas for packets, receipts, rollback, health decisions`
- `kernel.ledger: append-only local artifacts plus projections into taskpool/mission`
- `kernel.policy: allowlists, idempotency, rate limits, provider health decisions`
- `kernel.adapters: telegram/taskpool_outbox/github/lark adapters behind explicit capabilities`
- `kernel.runtime: deterministic compile/apply/readback orchestration with fail-closed defaults`
- `kernel.cron_gate: optional wrapper entrypoints that consult health decisions before work`

## Candidate Paths
- **A incremental strangler migration** score=9/10 risk=low/medium — Wrap existing scripts behind a typed kernel package one phase at a time; keep old scripts as CLI shims until parity tests pass. Rollback: disable wrapper or call existing script directly
- **B big-bang kernel rewrite** score=3/10 risk=high — Replace scripts/mission/taskpool/cron integration in one architecture pass. Rollback: large revert; high chance of losing evidence semantics
- **C status quo plus docs** score=5/10 risk=medium operational drift — Keep producing policy scripts and ADRs without runtime consolidation. Rollback: none needed but debt grows

## Selected Path
Selected: **A**
Best risk-adjusted path: preserves verified Phase17-24 behavior, allows parity tests, and avoids big-bang loss of taskpool/mission evidence semantics.

## Migration Lanes
- **Phase 25 migration evaluation and SSOT** — this report; gate: read-only architecture decision; no runtime switch
- **Phase 26 kernel package scaffold** — proposed; gate: package imports compile; existing scripts still canonical
- **Phase 27 contracts/ledger extraction** — proposed; gate: existing Phase17-24 tests pass through both old script and new module paths
- **Phase 28 cron health wrapper pilot** — proposed; gate: one Xiaomi/MiMo smoke job uses skip evidence without model call when degraded
- **Phase 29 external dispatch manual runner** — proposed; gate: consumes dry-run packet and explicit authorization; unattended remains disabled
- **Phase 30 projection consolidation** — proposed; gate: taskpool/mission/cron views derive from one typed event projection

## Hard Boundaries
- no global autorun enablement
- no cron mutation in migration evaluation
- no external dispatch expansion
- no config migration without backup/diff
- no deletion/renaming of historical evidence before a manifest-backed consolidation phase

## Acceptance Before Runtime Switch
- all existing Phase17-24 tests pass through new module wrappers
- old script CLIs remain backward compatible
- taskpool selfcheck and proof-check pass
- mission stage-report can cite new kernel artifact paths
- rollback path is a one-line CLI shim revert or wrapper disable

## Next Phase
Phase 26: create kernel package scaffold and move shared read/write helpers behind imported modules while keeping scripts as compatibility shims.
