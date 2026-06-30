# Verification and Acceptance

This document defines how the autonomous runtime governance architecture is accepted at each maturity level.

## Documentation-level acceptance

- `00-index.md` is the single entrypoint.
- Existing source documents have explicit disposition in `11-adr-index.md`.
- Target architecture, domain model, control loop, authority rules, subsystem mapping, and roadmap exist.
- No runtime behavior is claimed from documentation alone.

## Projection-level acceptance

- Capability graph projection can be generated.
- Projection doctor reports no broken source refs or unknown side-effect classes.
- Projection states runtime_mutation=false unless an execution gate is explicitly being tested.

## Runtime-audit acceptance

- Read-only audit covers cron, gateway, taskpool, mission, worker jobs, provider health, and source drift.
- Every finding has divergence class, evidence refs, next safe action, and boundary.
- Audit does not perform side effects.

## Evidence-ledger acceptance

- Claims bind to EvidenceNode IDs.
- Evidence has source ref, observed_at, freshness/TTL, trust level, and verification status.
- Model-generated evidence is labeled advisory unless grounded by observed proof.
- Sensitive values are redacted or stored as secret references/hashes.

## Reconciliation acceptance

- Decisions are deterministic for fixtures.
- Output is one of: continue, retry, compensate, block, expire, human_required.
- Every decision names required evidence and authority.
- No execution happens in decision-only mode.

## Compensation acceptance

- Same idempotency_key cannot produce duplicate external receipts.
- Existing artifact is reused before rerunning expensive/model work.
- Ambiguous external delivery fails closed or requires human intervention.
- Max attempts and time window are enforced.
- Result is read back and recorded as CompensationRecord + ClosureRecord.

## Production-side-effect acceptance

Before external write, publish/deploy/push, destructive, payment, or credential-related action:

- exact target is named;
- payload/digest is fixed;
- AuthorityGrant is active and scoped;
- rollback/escape path exists;
- post-action readback command is defined;
- receipt is recorded;
- neighboring gates remain closed unless separately authorized.

## Completion claim gate

A feature or workflow is complete only when:

- requested success criteria are met;
- evidence maturity matches risk;
- projection/readback is current;
- terminal state exists;
- residual risks and closed boundaries are stated.


## Readiness-before-scale acceptance

Any proposal to raise worker/taskpool/concurrency/autonomy level must fail closed unless read-only readiness proves capacity and governance health.

Required checks:

- selected_count >= requested_agents;
- resource_ceiling >= requested_agents;
- quality_slots >= requested_agents;
- provider confidence gate passes;
- soft_unknown_count within threshold;
- stale_claim_count == 0;
- boundary_warning_count == 0;
- handoff/review/certification backlog below threshold;
- dry-run explains dispatchable_count vs selected_count;
- no config cap changed during guard implementation.

## High-risk smoke acceptance pattern

A high-risk smoke can certify only its exact scope. Examples absorbed from local brain:

- disposable destructive canary: create backup, delete exact /tmp canary, verify absent, restore, verify hash match;
- broad fanout smoke: self-only exact Telegram target, explicit third-party exclusion, message_id receipt;
- payment/credential gate: human-side gate only, no agent secret entry.

These smokes do not authorize future real targets outside the exact target/channel/payload/resource certified in the receipt.
