# Migration Roadmap

Migration strategy: incremental strangler, not big-bang rewrite.

## P0 — Canonical documentation and source disposition

- Create this autonomous-runtime-governance packet.
- Link existing Agent OS, Control Tower, Workflow Kernel, and Agent Architecture Coverage documents as sources or history.
- Do not change runtime behavior.

Acceptance:
- canonical index exists;
- existing source artifacts have roles;
- no new runtime side effects.

## P1 — Canonical schema and projection

- Stabilize IntentRecord, EvidenceNode, AuthorityGrant, GateDecision, CompensationRecord, ClosureRecord.
- Generate projection/doctor over existing capability graph.
- Keep existing domain stores intact.

Acceptance:
- schema validates;
- projection doctor passes;
- no domain store mutation.

P1 implementation note (2026-06-30): the first executable schema gate is landed as a read-only Agent OS CLI surface: `hermes agent-os schema --json`. It validates canonical domain-model sections, S0-S7 side-effect coverage, S4-S7 default-off semantics, and projection runtime_mutation=false. Latest report: `16-p1-schema-status-report.json` (`ok=true`, `findings=[]`). This is not domain-store migration.


## P2 — Read-only runtime audit

- Build read-only audit over cron, gateway, taskpool, mission, worker jobs, provider health, source drift.
- Output divergence classes and next safe action.
- No compensation execution yet.

Acceptance:
- audit report generated;
- known fixtures classify correctly;
- no side effect beyond local report write.

P2 implementation note (2026-06-30): the first read-only runtime audit is landed as `hermes agent-os audit --json`. It classifies canonical schema, capability projection, cron jobs, gateway delivery outbox, shadow router, and scoped project profile into pass/warn/fail divergence records with evidence refs and next safe actions. Latest report: `17-p2-read-only-runtime-audit-report.json` (`ok=true`, summary `pass=6 warn=0 fail=0`). This is audit/report only: no compensation, store migration, or external side effect.


## P3 — Evidence ledger consolidation projection

- Map existing outbox, cron output, taskpool proof, mission stage report, worker artifact into EvidenceNode projections.
- Do not migrate canonical stores yet.

Acceptance:
- evidence projection covers key subsystems;
- stale/missing/conflicting evidence can be represented.

## P4 — Reconciliation Engine v0

- Produce decisions only: continue, retry, compensate, block, expire, human_required.
- No execution.

Acceptance:
- deterministic decision tests;
- exact reason and required evidence emitted.

## P5 — Allowlisted local compensation

- Enable only local/reversible actions: mark-only, local projection repair, dry-run, artifact copy.
- Require idempotency and readback.

Acceptance:
- duplicate suppression test;
- rollback/readback proof.

## P6 — Allowlisted external compensation

- Enable exact-target external compensation for narrow cases, e.g. resend existing Telegram artifact once.
- Require AuthorityGrant, idempotency, receipt, readback.

Acceptance:
- message_id recorded;
- ambiguous timeout fails closed;
- second execution suppressed.

## P7 — Control Tower integration

- Control Tower reads unified evidence/reconciliation projections.
- It shows progressability, blockers, evidence debt, human packets, and next safe action.

Acceptance:
- no UI-as-fact source;
- write actions remain digest-bound and gated.

## P8 — Autonomous governance loop

- Repeated blockers create governed improvement candidates.
- Ratchet reusable patterns into skill/test/SSOT, not stale memory.
- High-risk actions stay exact-authority gated.

Acceptance:
- improvement candidates are deduped, evidence-backed, and not auto-closed without proof.


## Cross-cutting track — Readiness-before-scale

This track comes from the taskpool concurrency certification plan and applies to any autonomous scale-up, not only taskpool.

Principle: do not raise automation/concurrency caps until read-only readiness verdicts pass.

Required readiness inputs:

- selected_count vs requested_agents;
- resource ceiling;
- quality slots;
- provider confidence and soft unknowns;
- stale claim count;
- boundary warning count;
- review/certification backlog;
- handoff backlog;
- dispatchable vs actually selectable tasks;
- blocking boundary groups.

Acceptance before any persistent cap raise:

- readiness is PASS for requested cap;
- no stale claims or boundary warnings;
- provider readiness is not hard_blocked or unknown beyond threshold;
- review/certification backlog is below threshold;
- multi-model certification is grounded for the guard implementation;
- config cap remains unchanged until the certification packet explicitly approves the raise.


## P0.5 — Local brain overlay, not overwrite

Before changing old local-brain structure:

- create disposition banners for old source docs;
- keep receipts/certification ledgers immutable as evidence;
- update project profile pointers to canonical packet;
- build doc-packet doctor to detect missing canonical landings;
- only archive or move old entrypoints after readback proves no active consumer depends on them as truth source.

This avoids replacing a working but messy evidence store with an unverified new taxonomy.
