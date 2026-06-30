# ADR / Source Disposition Index

| Source | Current role | Canonical landing | Disposition |
|---|---|---|---|
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/00-model/agent-os-control-plane-ssot.md` | active vision/source | `01-vision-and-principles.md`, `03-target-architecture.md` | keep; do not fork target model elsewhere |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/capability-control-plane-architecture.md` | active schema source | `04-core-domain-model.md` | keep; future schema should converge here |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/02-roadmap/phase-roadmap.md` | project-local progress roadmap | `10-migration-roadmap.md` | keep as project ledger; long-term roadmap lives here |
| `docs/private/hermes-control-tower-long-term-stable-plan-20260611.md` | active Control Tower/operator source | `08-runtime-surfaces.md` (future), `09-subsystem-mapping.md` | keep; Control Tower is projection/cockpit, not competing global fact source |
| `docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.md` | active implementation migration source | `10-migration-roadmap.md` | keep incremental strangler principle |
| `docs/private/modular-workflow-kernel-phase25-architecture-migration-adr.md` | ADR for Workflow Kernel migration | `10-migration-roadmap.md` | accepted source; no runtime switch by itself |
| `docs/private/workflow-kernel-architecture-selfcheck/arch_selfcheck_20260613_phase21.md` | architecture health evidence | `02-current-architecture-map.md`, `12-verification-and-acceptance.md` (future) | historical evidence |
| `docs/private/agent-architecture-coverage-plan-20260608.md` | historical authority-boundary ledger | `07-authority-and-side-effect-governance.md`, `12-verification-and-acceptance.md` (future) | do not use as everyday architecture entrypoint |
| `website/docs/developer-guide/architecture.md` | public code architecture | public-safe derivative section later | keep public; avoid private gate details |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/03-governance/default-off-gates.md` | active promotion policy source | `07-authority-and-side-effect-governance.md` | absorbed; keep as local-brain source |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/07-shadow/resume-layer-contract.md` | active resume policy source | `08-runtime-surfaces.md` | absorbed; resume is routing not authority |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/07-shadow/interruptibility-policy-shadow.md` | active proactive notification policy source | `08-runtime-surfaces.md` | absorbed; delivery remains default-off |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/taskpool-concurrency-certification-plan-20260627.md` | active readiness-before-scale plan | `10-migration-roadmap.md`, `12-verification-and-acceptance.md` | absorbed as cross-cutting scale gate |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/11-high-risk/exact-execution-requirements-20260627.json` | high-risk exact-target requirement packet | `07-authority-and-side-effect-governance.md` | absorbed as exact high-risk execution requirements |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/11-high-risk/real-execution-authority-and-destructive-smoke-20260627.json` | exact-scope high-risk smoke evidence | `12-verification-and-acceptance.md` | absorbed as exact-scope smoke pattern |
| `13-local-brain-absorption.md` | source absorption index | this packet | current local-brain source map |
| `/Users/charles/.hermes/project-profiles/hermes-agent-os-control-plane.json` | scoped local-brain project profile | `02-current-architecture-map.md`, `13-local-brain-absorption.md` | updated to point to the autonomous runtime governance packet |


## Rule for new ADRs

New ADRs must state:

- target domain;
- whether they supersede an existing source;
- side-effect class affected;
- evidence/verification required;
- rollback/readback path;
- canonical document section updated.
