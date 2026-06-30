# Hermes Autonomous Runtime Governance Architecture

Status: canonical draft / accepted direction
Created: 2026-06-30T11:13:22+08:00
Scope: Hermes long-term automation and autonomous-governance architecture
Short name: ERG Runtime — Evidence-governed Reconciliation Runtime

## Purpose

This directory is the canonical long-term architecture packet for moving Hermes from scattered automation mechanisms into an autonomous runtime governance system.

Do not add parallel "cron self-check", "workflow kernel next phase", "Control Tower target", or "Agent OS target" documents as new top-level truths. New work should either update this packet, add an ADR referenced by `11-adr-index.md`, or append evidence to a scoped project ledger.

## Architecture thesis

Hermes is not just a chat agent with tools. The long-term system is an autonomous runtime that accepts intents, routes capabilities, gates authority, executes under leases, collects evidence, reconciles observed state against desired state, projects status to users/operators, compensates safely, and ratchets reusable learning into the right store.

Core formula:

```text
Autonomous Runtime = Intent Sources + Capability Graph + Authority Gates + Execution Leases + Evidence DAG + Reconciliation Engine + Projection Surfaces + Compensation/Ratchet
```

## Canonical reading order

1. `01-vision-and-principles.md` — why this architecture exists and the governing principles.
2. `02-current-architecture-map.md` — current Hermes components and existing architecture sources.
3. `03-target-architecture.md` — target ERG Runtime shape.
4. `04-core-domain-model.md` — canonical objects: Intent, Evidence, Gate, Closure, Compensation.
5. `05-control-loop-and-reconciliation.md` — startup/dynamic self-check as a generalized control loop.
6. `06-evidence-ledger-and-closure.md` — what counts as done.
7. `07-authority-and-side-effect-governance.md` — authority and side-effect matrix.
8. `08-runtime-surfaces.md` — Control Tower, dashboard, gateway, resume, and interruptibility surfaces.
9. `09-subsystem-mapping.md` — cron/gateway/taskpool/mission/worker/Control Tower mapping.
10. `10-migration-roadmap.md` — incremental, non-big-bang migration path.
11. `11-adr-index.md` — existing documents and their canonical disposition.
12. `12-verification-and-acceptance.md` — acceptance gates by maturity level.
13. `13-local-brain-absorption.md` — source map from older local-brain artifacts into this canonical packet.
14. `14-takeover-disposition-manifest.md` — takeover manifest for old source dispositions and JSON/profile sources.
15. `15-takeover-doctor-report.json` — latest read-only takeover doctor report (`status=pass`).
16. `16-p1-schema-status-report.json` — latest P1 canonical schema/read-only projection report (`ok=true`).
17. `17-p2-read-only-runtime-audit-report.json` — latest P2 read-only runtime audit report (`ok=true`).
18. `18-p3-evidence-node-projection-report.json` — latest P3 EvidenceNode projection report (`ok=true`).
19. `19-runtime-promotion-authorization-gate.json` — smallest next runtime mutation gate; human authorization required.
20. `20-p4-runtime-bootstrap-report.json` — initial P4 local runtime bootstrap execution report.
21. `21-p4-runtime-bootstrap-idempotency-readback.json` — P4 idempotency/readback report after rerun.
22. `22-high-risk-boundary-coverage-report.json` — high-risk boundary governance coverage report for nine requested classes.

## Anti-sprawl rules

- One canonical target architecture lives here.
- Existing phase documents are evidence/history unless explicitly marked as active subsystem specs.
- New memory structure is an overlay/canonical-index over the old local brain; it does not overwrite historical ledgers, receipts, or scoped project profiles.
- Control Tower is a projection/operator cockpit, not the global fact source.
- Agent OS is the product/control-plane vision; ERG Runtime is the runtime governance architecture.
- Workflow Kernel is the implementation kernel for contracts/ledger/policy/adapters/runtime.
- New side-effect execution must be exact-target, authority-gated, idempotent, receipt-backed, and read back.
- Model outputs are advisory/proposal unless bound to observed evidence.

## Current source artifacts

- Agent OS SSOT: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/00-model/agent-os-control-plane-ssot.md`
- Capability control plane: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/capability-control-plane-architecture.md`
- Agent OS roadmap: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/02-roadmap/phase-roadmap.md`
- Control Tower plan: `docs/private/hermes-control-tower-long-term-stable-plan-20260611.md`
- Workflow Kernel migration: `docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.md`
- Agent architecture coverage ledger: `docs/private/agent-architecture-coverage-plan-20260608.md`
- Public architecture map: `website/docs/developer-guide/architecture.md`
- Local-brain absorption map: `13-local-brain-absorption.md`
- Takeover disposition manifest: `14-takeover-disposition-manifest.md`
- Latest takeover doctor report: `15-takeover-doctor-report.json`
- Takeover doctor script: `scripts/arg_doctor.py`
- P1 schema status command: `hermes agent-os schema --json`
- Latest P1 schema report: `16-p1-schema-status-report.json`
- P2 read-only runtime audit command: `hermes agent-os audit --json`
- Latest P2 runtime audit report: `17-p2-read-only-runtime-audit-report.json`
- P3 EvidenceNode projection command: `hermes agent-os evidence --json`
- Latest P3 EvidenceNode report: `18-p3-evidence-node-projection-report.json`
- Next runtime promotion gate: `19-runtime-promotion-authorization-gate.json`
- P4 exact local runtime bootstrap command: `hermes agent-os bootstrap-runtime --authority-gate <gate> --json`
- P4 reports: `20-p4-runtime-bootstrap-report.json`, `21-p4-runtime-bootstrap-idempotency-readback.json`


High-risk boundary coverage command: `hermes agent-os boundary-coverage --destructive-canary --json`. This reports 100% governance coverage for scheduler/cron mutation, taskpool/mission lifecycle write, gateway external send, worker lease, compensation, publish/deploy/push, destructive action, payment/credential entry, and broad fan-out. It does not execute class-only real side effects; the only live destructive coverage is a disposable local canary delete/restore smoke under the project runtime directory.
