# Local Brain Absorption Notes

Status: absorbed into canonical architecture packet
Created: 2026-06-30T11:13:22+08:00
Updated: 2026-06-30T11:20:00+08:00
Source: `/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/`

## Purpose

This document records which older local-brain artifacts were absorbed into the ERG Runtime architecture packet, what transferable pattern they contributed, and where the pattern now lives.

It is not a new target architecture. It is an evidence/source map to prevent re-reading old project ledgers as parallel current truth.

## Absorbed artifacts and patterns

| Local brain artifact | Pattern absorbed | Canonical landing |
|---|---|---|
| `03-governance/default-off-gates.md` | Default-off and shadow-first promotion ladder | `07-authority-and-side-effect-governance.md`, `10-migration-roadmap.md` |
| `01-architecture/agent-os-schemas.json` | Side-effect taxonomy, required CapabilityNode/Evidence/Closure fields, default-off S4-S7, shadow-first classes | `04-core-domain-model.md`, `07-authority-and-side-effect-governance.md` |
| `06-projection/control-surface-projection.md` | Control surface distribution, high-gate capability list, projection-only next actions | `08-runtime-surfaces.md`, `09-subsystem-mapping.md`, `12-verification-and-acceptance.md` |
| `07-shadow/resume-layer-contract.md` | Resume candidate sources and privacy/authority guard | `05-control-loop-and-reconciliation.md`, `08-runtime-surfaces.md` |
| `07-shadow/interruptibility-policy-shadow.md` | Severity ladder and no silent proactive sends | `08-runtime-surfaces.md`, `07-authority-and-side-effect-governance.md` |
| `03-governance/remaining-gates-index.json` | Actual gate status: local/advisory/runtime/external/high-risk rungs, exact scopes and readbacks | `07-authority-and-side-effect-governance.md`, `12-verification-and-acceptance.md` |
| `11-high-risk/exact-execution-requirements-20260627.json` | Exact target requirements for destructive/payment/fanout/publish actions | `07-authority-and-side-effect-governance.md`, `12-verification-and-acceptance.md` |
| `11-high-risk/real-execution-authority-and-destructive-smoke-20260627.json` | Safe exact-scope high-risk smoke pattern: disposable destructive canary, self-only fanout, human-only credential gate | `07-authority-and-side-effect-governance.md`, `12-verification-and-acceptance.md` |
| `taskpool-concurrency-certification-plan-20260627.md` | Readiness-before-scale pattern; fail-closed concurrency raising with provider/boundary/review gates | `10-migration-roadmap.md`, `12-verification-and-acceptance.md` |
| `04-evidence/yodaos-hermes-analysis-evidence.json` | Source provenance for YodaOS-inspired product layering and P0 concepts | `01-vision-and-principles.md`, `02-current-architecture-map.md` |
| `/Users/charles/.hermes/project-profiles/hermes-agent-os-control-plane.json` | Scoped project profile: role, promotion policy, relations, current component states, boundaries, certification refs, canonical docs | `02-current-architecture-map.md`, `11-adr-index.md`, project profile updated in place |

## Transferable patterns extracted

### 1. Default-off + shadow-first promotion

Capabilities that may affect memory, external sends, scheduler mutation, worker lifecycle, publish/deploy/push, destructive operations, payment, physical actions, or always-on sensing must start in projection/shadow mode. Promotion requires declared capability node, side-effect class, authority scope, evidence requirements, tests/dry-run proof, rollback/kill switch, user-visible receipt, cost budget, and stale/failure behavior.

### 2. Resume is routing, not authority

`继续 / 接着做 / 刚才那个` should resolve candidates from conversation tail, project profile/ledger, mission/taskpool gates, cron/background receipts, session_search, and explicit artifact links. The result is advisory until an AuthorityGrant exists for any side effect. Group chat and cross-profile candidates require redaction/selection safeguards.

### 3. Interruptibility is a policy gate

Proactive notifications need severity, timing, channel, evidence, budget, and user-control gates. Severity levels range from silent ledger record to urgent high-risk incident notice. Shadow classification may recommend delivery, but no silent send is allowed.

### 4. Readiness-before-scale

Before raising taskpool/worker concurrency, the system must expose read-only readiness verdicts: selected_count, resource ceiling, quality slots, provider confidence, soft unknowns, stale claims, boundary warnings, review backlog, and handoff backlog. Raising caps is forbidden while readiness is FAIL_CLOSED.

### 5. Exact high-risk execution

High-risk execution requires exact targets and readbacks, not category-level permission. Destructive actions need exact resource + backup + restore + blast radius + readback. Fanout needs exact channel set + payload + idempotency + per-target receipt. Payment/credential entry remains human-side; the agent must not type secrets.

### 6. Project profile is scoped local brain, not global memory

Durable project facts such as current component states, canonical doc paths, relations, boundaries, and certification refs belong in the scoped project profile. They should not be promoted into global memory or copied into unrelated project profiles. The canonical architecture packet may reference them as source evidence while keeping runtime authority boundaries explicit.

### 7. Projection-only is a valid terminal slice

A slice can be complete as projection-only when its boundary is explicit, runtime mutation is false, and the next authority rung is named. Do not relabel projection completion as runtime execution.

## Open follow-up

- Add disposition banners to older local-brain source docs so future edits land in this canonical packet instead of re-splitting target architecture.
- Add a lightweight doctor for this documentation packet: required file presence, source disposition coverage, and no missing canonical sections.


## Replacement strategy: do not overwrite the old local brain

Decision: the new architecture packet must not physically overwrite the existing local-brain structure.

Use an overlay / canonical-index / strangler strategy:

1. Keep old local-brain artifacts as source evidence and historical ledgers.
2. Mark the new `autonomous-runtime-governance/` packet as the canonical target architecture and current interpretation layer.
3. Add disposition banners to old source docs so future target-architecture edits land in the canonical packet.
4. Gradually migrate active schemas, gates, and projections into ERG Runtime docs or implementation when they pass verification.
5. Keep project-specific facts in the scoped project profile; do not promote them to global memory.
6. Deprecate old entrypoints only after a doctor can prove every source has a canonical landing and no active consumer depends on the old path as truth source.

Rationale:

- The old local brain contains receipts, certification paths, authority history, and historical decision context that should not be lost.
- A hard overwrite would erase provenance and may make future safety reviews unverifiable.
- The new structure is a semantic canonical layer, not a storage replacement.
- Gradual strangler migration matches the existing Workflow Kernel migration principle and lowers blast radius.

Allowed mutations:

- add disposition banners;
- add backlinks from old docs to canonical docs;
- add doctor/projection checks;
- update project profile pointers;
- archive superseded documents after readback and dependency check.

Disallowed mutations without separate explicit authorization:

- deleting old local-brain ledgers;
- rewriting historical receipts;
- moving files in ways that break existing tools;
- collapsing project profiles into global memory;
- treating canonical documentation as runtime authority.
