# Continuation Control Plane Architecture

Date: 2026-05-18
Status: landed-minimum-viable-control-plane

## Goal

Make Hermes execution resumable across long conversations, delegated workers, taskpool handoffs, scheduled jobs, background review, and gateway delivery without relying on the current Python call stack staying alive.

## Design synthesis

This design is a multi-perspective synthesis, not an external multi-model quorum run:

- Architect perspective: unify all resumable execution as a durable packet protocol instead of copying auto-relay logic into every subsystem.
- Reliability perspective: every continuation must be bounded, idempotent, and auditable; recursive immediate relay remains capped.
- Operator perspective: taskpool tick summaries must expose continuation backlog so stalls are diagnosable without reading internal directories.
- Compatibility perspective: existing relay debugging paths under `~/.hermes/state/relay-packets/` remain available while packet contents move to the global schema.

## Core invariant

All work that may not finish synchronously follows:

Detect -> Packet -> Resume -> Verify -> Close

A continuation packet is not proof of success. It is a resumable state snapshot with explicit scope, reason, policy, and evidence hooks.

## Packet schema

Implemented in `agent/continuation.py` as `hermes_continuation_packet_v1`.

Required semantic fields:

- `packet_id`: stable packet identity.
- `scope`: execution surface, e.g. `conversation`, `delegate`, `taskpool`, `cron`, `bg_review`, `gateway`.
- `reason`: normalized reason, e.g. `max_iterations`, `budget_exhausted`, `provider_timeout`.
- `raw_reason`: original exit reason for debugging.
- `summary`: compact handoff summary.
- `remaining_work`: optional structured next steps.
- `evidence`: optional proof/evidence references.
- `resume`: policy and executor metadata.
- `delivery`: target delivery metadata for gateway/cron uses.
- `limits`: bounded retry/relay counters.
- `metadata`: compatibility or subsystem-specific payload.

## Storage layout

Default registry root:

`~/.hermes/state/continuations/`

States:

- `active/`
- `completed/`
- `failed/`
- `archived/`

Existing conversation auto-relay also writes a compatibility copy to:

`~/.hermes/state/relay-packets/`

The compatibility file keeps the `relay-*` filename convention but uses the unified schema and stores legacy metadata under `metadata.legacy_schema`.

## Landed integration

1. Conversation loop

`agent/conversation_loop.py` now builds auto-relay packets through `agent.continuation`.

Relay still triggers only when:

- exit reason is `budget_exhausted` or `max_iterations_reached(...)`,
- final summary exists,
- relay is enabled,
- `_relay_count < max_relay_count`.

2. Taskpool observability

`~/.hermes/scripts/brain_task_dispatch_tick.py` now reports:

- `continuation.active_count`
- `continuation.counts`
- `continuation.by_scope`
- `continuation.backlog_warning`

3. Anti-fragmentation self-check

`~/.hermes/scripts/post-update-check.py` now includes `continuation_fragmentation_guard`.

The guard scans Hermes repo code plus user scripts for durable `.hermes/state/*handoff*|*relay*|*resume*|*retry*|*recovery*|*missed*` writers that bypass `agent.continuation`. It is intentionally conservative: ordinary in-memory retry loops and read-only mentions are not violations; new durable resumable-state writers must either use the canonical API or be explicitly justified.

Config knob:

```yaml
taskpool:
  continuation_backlog_threshold: 100
```

## Not yet expanded in this slice

This slice intentionally does not yet change runtime behavior of delegate_task, cron, bg-review, or gateway delivery. Those surfaces should consume the same `agent.continuation` API in later slices.

Recommended next surfaces:

1. delegate_task: child budget exhaustion returns a child continuation packet instead of only a summary.
2. taskpool: autorun/parallel handoff files reference continuation packet IDs.
3. bg-review: timeout writes `scope=bg_review` packet for next idle/tick recovery.
4. cron: timeout/missed-run writes `scope=cron` packet with bounded next-tick retry.
5. gateway: failed delivery writes `scope=gateway` packet with delivery metadata.

## Verification

Current focused regression command:

```bash
cd ~/.hermes/hermes-agent
./venv/bin/python -m pytest \
  tests/agent/test_continuation.py \
  tests/run_agent/test_auto_relay_continuation.py \
  tests/run_agent/test_taskpool_dispatch_tick_runtime.py \
  tests/scripts/test_continuation_fragmentation_guard.py \
  -q -o addopts=
```

Expected result: all tests pass.
