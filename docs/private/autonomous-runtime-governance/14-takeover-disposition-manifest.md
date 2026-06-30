# Takeover Disposition Manifest

Status: active takeover manifest
Purpose: prove old local-brain/architecture sources are now governed by the autonomous-runtime-governance canonical packet without deleting historical evidence.

## Markdown sources with in-file disposition banners

| Source | Role | Canonical landing | Banner |
|---|---|---|---|
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/00-model/agent-os-control-plane-ssot.md` | source-active | `01-vision-and-principles.md / 03-target-architecture.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/capability-control-plane-architecture.md` | source-active-subsystem | `04-core-domain-model.md / 09-subsystem-mapping.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/02-roadmap/phase-roadmap.md` | source-project-roadmap | `10-migration-roadmap.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/03-governance/default-off-gates.md` | source-policy | `07-authority-and-side-effect-governance.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/06-projection/control-surface-projection.md` | source-projection | `08-runtime-surfaces.md / 09-subsystem-mapping.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/07-shadow/resume-layer-contract.md` | source-policy-shadow | `08-runtime-surfaces.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/07-shadow/interruptibility-policy-shadow.md` | source-policy-shadow | `08-runtime-surfaces.md / 07-authority-and-side-effect-governance.md` | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/taskpool-concurrency-certification-plan-20260627.md` | source-readiness-plan | `10-migration-roadmap.md / 12-verification-and-acceptance.md` | yes |
| `/Users/charles/.hermes/hermes-agent/docs/private/hermes-control-tower-long-term-stable-plan-20260611.md` | source-operator-surface | `08-runtime-surfaces.md` | yes |
| `/Users/charles/.hermes/hermes-agent/docs/private/workflow-kernel-architecture-migration/workflow_kernel_architecture_migration_20260613_phase25.md` | source-kernel-migration | `10-migration-roadmap.md` | yes |
| `/Users/charles/.hermes/hermes-agent/docs/private/modular-workflow-kernel-phase25-architecture-migration-adr.md` | source-adr | `11-adr-index.md / 10-migration-roadmap.md` | yes |
| `/Users/charles/.hermes/hermes-agent/docs/private/workflow-kernel-architecture-selfcheck/arch_selfcheck_20260613_phase21.md` | source-selfcheck | `12-verification-and-acceptance.md` | yes |
| `/Users/charles/.hermes/hermes-agent/docs/private/agent-architecture-coverage-plan-20260608.md` | historical-authority-ledger | `07-authority-and-side-effect-governance.md / 12-verification-and-acceptance.md` | yes |

## JSON/profile sources governed by this manifest

| Source | Canonical landing | Exists |
|---|---|---|
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/01-architecture/agent-os-schemas.json` | schema source -> 04-core-domain-model.md / 07-authority-and-side-effect-governance.md | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/03-governance/remaining-gates-index.json` | gate status source -> 07-authority-and-side-effect-governance.md / 12-verification-and-acceptance.md | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/yodaos-hermes-analysis-evidence.json` | provenance source -> 01-vision-and-principles.md / 02-current-architecture-map.md | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/11-high-risk/exact-execution-requirements-20260627.json` | high-risk exact requirements -> 07-authority-and-side-effect-governance.md | yes |
| `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/11-high-risk/real-execution-authority-and-destructive-smoke-20260627.json` | exact-scope smoke evidence -> 12-verification-and-acceptance.md | yes |
| `/Users/charles/.hermes/project-profiles/hermes-agent-os-control-plane.json` | scoped project profile -> 02-current-architecture-map.md / 13-local-brain-absorption.md | yes |

## Takeover rule

The new packet controls future architecture interpretation and write targets. Historical ledgers, receipts, project profiles, and JSON evidence files remain source artifacts; they are not physically overwritten or deleted by this takeover.
