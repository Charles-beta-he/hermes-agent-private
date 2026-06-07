# Control Tower Phase 10 implementation review

Date: 2026-06-07
Scope reviewed: `hermes_cli/control_tower.py`, `plugins/control-tower/dashboard/plugin_api.py`, `plugins/control-tower/dashboard/dist/index.js`, focused Control Tower tests, and `docs/private/hermes-control-tower-implementation-packet-20260607.md`.

## Current implementation boundary

The current packet and code are at Phase 9:

- Control Tower Ledger is the canonical workflow fact source at `~/.hermes/control_tower.db`.
- Read projections, command proposals, human approvals, executor dry-run plans, read-only executor runs, evidence gates, closeout authorization, and ledger-only closeout are implemented.
- Phase 9 prepares blocked `external_adapter_write_intents` with stable mutation digests.
- External taskpool/Kanban write-through remains disabled, with `writes_enabled = false`, `external_adapter_write_through_enabled = false`, `rollback_required = true`, and `rollback_available = false`.

## Recommended small TDD Phase 10 slice

Implement a ledger-only external adapter write intent readiness review.

This should answer: "Is this prepared external adapter write intent structurally ready for a future write-through contract?" It should not execute the intent, mutate external adapters, change taskpool/Kanban state, or mark the intent write-enabled.

### Proposed behavior

Add `control_tower.review_external_adapter_write_intent(...)` that records one idempotent review for an existing Phase 9 intent.

Suggested durable table:

- `external_adapter_write_reviews`
  - `external_write_review_id TEXT PRIMARY KEY`
  - `external_write_intent_id TEXT NOT NULL UNIQUE`
  - `closeout_execution_id TEXT NOT NULL`
  - `task_id TEXT NOT NULL`
  - `adapter_name TEXT NOT NULL`
  - `external_ref TEXT NOT NULL`
  - `mutation_digest TEXT NOT NULL`
  - `review_status TEXT NOT NULL`
  - `contract_status TEXT NOT NULL`
  - `rollback_status TEXT NOT NULL`
  - `writes_enabled INTEGER NOT NULL DEFAULT 0`
  - `external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 0`
  - `required_decisions_json TEXT NOT NULL DEFAULT '[]'`
  - `blocked_reason TEXT NOT NULL`
  - `reviewed_by TEXT NOT NULL`
  - `reviewed_role TEXT NOT NULL`
  - `reviewed_at TEXT NOT NULL`

Suggested successful review result for the current boundary:

- `review_status = blocked`
- `contract_status = adapter_contract_missing`
- `rollback_status = rollback_reopen_contract_missing`
- `writes_enabled = false`
- `external_adapter_write_through_enabled = false`
- `blocked_reason = external_adapter_contract_and_rollback_required`
- `required_decisions = ["adapter_mutation_contract", "rollback_reopen_contract", "approval_digest_semantics"]`

Fail closed when:

- intent does not exist;
- intent status is not `blocked_pending_adapter_contract`;
- `writes_enabled` or `external_adapter_write_through_enabled` is unexpectedly true;
- stored mutation digest no longer matches the canonical mutation payload;
- actor role is below `owner`.

Emit one audit event on first review:

- `ExternalAdapterWriteIntentReviewed`

Snapshot addition:

- `external_adapter_write_reviews`
  - `mode = adapter_write_readiness_projection_only`
  - `write_through_enabled = false`
  - `review_count`
  - `blocked_count`
  - `recent_reviews`
  - `required_decisions`

Dashboard API addition:

- `POST /api/plugins/control-tower/external-write-intents/{external_write_intent_id}/review`

Static UI addition:

- One button: `Review external intent`
- Summary counts for external intent reviews and blocked reviews.

## Expected tests

### `tests/hermes_cli/test_control_tower_snapshot.py`

1. `test_init_ledger_creates_single_fact_source_schema`
   - Add `external_adapter_write_reviews` to expected table set.

2. `test_review_external_adapter_write_intent_records_blocked_readiness_without_write_through`
   - Use existing closeout/intent helper path.
   - Prepare a Phase 9 intent.
   - Review it as `actor_role="owner"`.
   - Assert review fields above.
   - Assert repeated review is idempotent and emits no duplicate event.
   - Assert `external_adapter_write_intents` row remains blocked with writes disabled.
   - Assert adapter observation count is unchanged.
   - Assert task remains closed only in Control Tower Ledger.
   - Assert latest event is `ExternalAdapterWriteIntentReviewed`.

3. `test_review_external_adapter_write_intent_fails_closed_on_digest_mismatch`
   - Prepare an intent.
   - Tamper only ledger `mutation_payload_json` or `mutation_digest` in test setup.
   - Assert review raises/fails closed with `external_adapter_intent_digest_mismatch`.
   - Assert no review row and no extra external adapter mutation.

4. `test_snapshot_includes_external_adapter_write_reviews_projection`
   - Review an intent.
   - Assert snapshot projection reports write-through disabled, count 1, blocked count 1, and required decisions.

### `tests/plugins/test_control_tower_dashboard_plugin.py`

5. `test_control_tower_dashboard_plugin_reviews_external_write_intent_without_write_through`
   - Reuse the existing plugin flow that prepares an external write intent.
   - POST the new review route.
   - Assert 200 response, blocked readiness fields, writes disabled, source surface `dashboard`.

## Affected files

Implementation files:

- `hermes_cli/control_tower.py`
  - Add table DDL.
  - Add review function.
  - Add projection in `build_snapshot()`.
  - Add event emission.

- `plugins/control-tower/dashboard/plugin_api.py`
  - Add request model if needed for actor fields.
  - Add review route.

- `plugins/control-tower/dashboard/dist/index.js`
  - Add one button/action and summary counts.

Tests:

- `tests/hermes_cli/test_control_tower_snapshot.py`
- `tests/plugins/test_control_tower_dashboard_plugin.py`

Docs:

- `docs/private/hermes-control-tower-implementation-packet-20260607.md`
  - Add Phase 10 once implemented.

## Why this is a good Phase 10 slice

- It is small and TDD-friendly.
- It advances the current Phase 9 boundary without crossing into external adapter writes.
- It creates a durable checklist for the human decisions already named in the packet.
- It can be verified entirely with temp `HERMES_HOME` pytest runs and does not require taskpool/Kanban adapter mutation fixtures.
- It keeps the stop condition intact: no external adapter write-through until adapter contract, rollback/reopen, evidence override, and approval digest semantics are explicitly approved.
