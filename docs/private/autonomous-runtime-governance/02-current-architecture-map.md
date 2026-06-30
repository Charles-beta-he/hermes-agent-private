# Current Architecture Map

Status: source-grounded map from existing docs, not a runtime completeness claim.

## Public code architecture

`website/docs/developer-guide/architecture.md` currently maps Hermes as:

```text
Entry Points
  CLI / Gateway / ACP / Batch Runner / API Server / Python Library
    -> AIAgent
       -> Prompt Builder
       -> Provider Resolution
       -> Tool Dispatch
    -> Session Storage
    -> Tool Backends
```

It also lists major subsystems: Agent Loop, Prompt System, Provider Resolution, Tool System, Session Persistence, Gateway, Cron, ACP, skills, plugins, website, tests.

## Existing governance/control-plane sources

1. Agent OS SSOT
   - Path: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/00-model/agent-os-control-plane-ssot.md`
   - Role: active vision/source.
   - Key model: Ingress -> Context Graph -> CapabilityIntent -> CapabilityGraph Router -> Policy + Authority Gate -> Tool/Skill/Worker Lease -> Evidence DAG -> ClosureRecord -> Control Surface.

2. Capability Control Plane Architecture
   - Path: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/capability-control-plane-architecture.md`
   - Role: active schema source.
   - Existing objects: CapabilityNode, CapabilityIntent, AgentWrapper, EvidenceNode, ClosureRecord.

3. Agent OS Phase Roadmap
   - Path: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/02-roadmap/phase-roadmap.md`
   - Role: project-local roadmap/ledger.
   - P0/P1 projection and shadow routing validated; P2/P3/P4 remain staged.

4. Control Tower Long-Term Stable Plan
   - Path: `docs/private/hermes-control-tower-long-term-stable-plan-20260611.md`
   - Role: active source for operator/control-surface design.
   - Important correction: Control Tower should be treated as cockpit/projection over ERG Runtime, not a competing global fact source.

5. Workflow Kernel Architecture Migration
   - Path: `docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.md`
   - Role: active source for implementation migration.
   - Accepted path: incremental strangler migration; target modules: contracts, ledger, policy, adapters, runtime, cron_gate.

6. Agent Architecture Coverage Plan
   - Path: `docs/private/agent-architecture-coverage-plan-20260608.md`
   - Role: historical boundary-opening ledger and authority precedent.
   - Not suitable as future entrypoint because it mixes many completed slices, authority rungs, commits, and historical receipts.

7. Project profile / local brain profile
   - Path: `/Users/charles/.hermes/project-profiles/hermes-agent-os-control-plane.json`
   - Role: scoped project profile and local-brain index.
   - Absorbed facts: project role, promotion policy, relations to portfolio/control-console/model-router/persona-wake projects, current component state, boundaries, certifications, and canonical doc pointers.
   - Canonical update: now points back to this `autonomous-runtime-governance/` packet as the long-term architecture trunk.

## Current gaps

- Target-model facts are spread across Agent OS, Control Tower, Workflow Kernel, and architecture-coverage documents.
- Runtime concepts are duplicated under different names: fire/run/outbox/proof/stage-report/closure/receipt.
- Cron, gateway, taskpool, mission, and workers each have partial evidence/receipt semantics, but not one shared Intent/Evidence/Gate/Closure model.
- Control Tower and Agent OS can appear to define overlapping control planes unless projected under ERG Runtime.
- Startup self-check and scheduled-job compensation are currently too low-level; they should be subsumed by reconciliation/runtime assurance.
- Historical evidence documents are too numerous to be the everyday architecture entrypoint.
