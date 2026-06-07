# Hermes Control Tower Ledger schema proposal

Date: 2026-06-07
Status: proposed — Phase 0 schema contract
Parent ADR: `docs/private/hermes-control-tower-adr-20260607.md`
Implementation packet: `docs/private/hermes-control-tower-implementation-packet-20260607.md`

## Decision

Hermes Control Tower uses one canonical workflow fact source:

- SQLite database: `~/.hermes/control_tower.db`
- Name: **Control Tower Ledger**
- Role: append-only workflow ledger plus rebuildable projections

Kanban, taskpool, brain markdown, dashboard, CLI, worker queues, event stream, evidence pages, gateway, and cron views are adapters/projections/external inputs. They must not become independent workflow truth.

## Design rules

1. All durable workflow facts enter the ledger through commands/events.
2. Existing stores may be imported as observed facts, but conflicts become explicit events.
3. Projection tables are rebuildable from ledger events and referenced artifacts.
4. Artifact payloads may live outside SQLite, but ledger owns artifact metadata, hash, maturity, and acceptance-criteria links.
5. Every side effect has actor, idempotency key, command record, event record, and evidence expectation.
6. Every write-capable adapter must eventually write through the command gateway.
7. Read-only adapters may be used during migration, but their output is labeled `observed_external` until reconciled into ledger facts.

## SQLite layout

### 1. `ledger_meta`

Purpose: database identity and schema migration state.

Columns:

- `key TEXT PRIMARY KEY`
- `value TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Required keys:

- `schema_version`
- `created_at`
- `active_profile`
- `ledger_id`

### 2. `commands`

Purpose: admitted or rejected intents.

Columns:

- `command_id TEXT PRIMARY KEY`
- `command_type TEXT NOT NULL`
- `actor_type TEXT NOT NULL` — `user | agent | worker | gateway | cron | system`
- `actor_id TEXT NOT NULL`
- `source_surface TEXT NOT NULL` — `cli | dashboard | gateway | cron | worker | script | api`
- `target_type TEXT` — `task | workflow_run | worker_run | evidence | policy | projection`
- `target_id TEXT`
- `idempotency_key TEXT`
- `risk_level TEXT NOT NULL` — `L0 | L1 | L2 | L3 | L4`
- `payload_json TEXT NOT NULL`
- `policy_decision_id TEXT`
- `status TEXT NOT NULL` — `accepted | rejected | superseded | failed`
- `created_at TEXT NOT NULL`

Indexes:

- unique nullable `idempotency_key`
- `(target_type, target_id, created_at)`
- `(actor_type, actor_id, created_at)`

### 3. `events`

Purpose: immutable facts.

Columns:

- `event_id TEXT PRIMARY KEY`
- `sequence INTEGER NOT NULL UNIQUE`
- `event_type TEXT NOT NULL`
- `aggregate_type TEXT NOT NULL` — `task | workflow_run | worker_run | evidence | policy | projection | adapter`
- `aggregate_id TEXT NOT NULL`
- `command_id TEXT`
- `actor_type TEXT NOT NULL`
- `actor_id TEXT NOT NULL`
- `source_surface TEXT NOT NULL`
- `correlation_id TEXT NOT NULL`
- `causation_id TEXT`
- `payload_json TEXT NOT NULL`
- `schema_version INTEGER NOT NULL DEFAULT 1`
- `created_at TEXT NOT NULL`

Indexes:

- `(aggregate_type, aggregate_id, sequence)`
- `(correlation_id, sequence)`
- `(event_type, created_at)`

Rules:

- Events are append-only.
- Corrections are new events.
- No in-place mutation except storage-level migration metadata if unavoidable.

### 4. `tasks`

Purpose: canonical task projection, rebuilt from events.

Columns:

- `task_id TEXT PRIMARY KEY`
- `external_ref TEXT`
- `title TEXT NOT NULL`
- `project TEXT`
- `board TEXT`
- `priority TEXT` — `P0 | P1 | P2 | P3 | unknown`
- `status TEXT NOT NULL`
- `risk_class TEXT NOT NULL DEFAULT 'unknown'`
- `assignee TEXT`
- `workflow_template_id TEXT`
- `current_step_key TEXT`
- `acceptance_json TEXT NOT NULL DEFAULT '[]'`
- `red_lines_json TEXT NOT NULL DEFAULT '[]'`
- `dependencies_json TEXT NOT NULL DEFAULT '[]'`
- `evidence_refs_json TEXT NOT NULL DEFAULT '[]'`
- `blocked_reason TEXT`
- `next_action TEXT`
- `version INTEGER NOT NULL DEFAULT 0`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Rules:

- This is a projection table, not a separate write API.
- Updates are produced by event replay/projectors.
- Existing Kanban/taskpool states are imported as adapter observations until reconciled.

### 5. `workflow_runs`

Purpose: canonical workflow run projection.

Columns:

- `workflow_run_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL`
- `workflow_template_id TEXT NOT NULL`
- `status TEXT NOT NULL`
- `current_step_key TEXT`
- `started_at TEXT`
- `ended_at TEXT`
- `last_event_sequence INTEGER`
- `context_json TEXT NOT NULL DEFAULT '{}'`
- `version INTEGER NOT NULL DEFAULT 0`
- `updated_at TEXT NOT NULL`

### 6. `workflow_steps`

Purpose: step projection for map/gate inspector.

Columns:

- `step_run_id TEXT PRIMARY KEY`
- `workflow_run_id TEXT NOT NULL`
- `task_id TEXT NOT NULL`
- `step_key TEXT NOT NULL`
- `step_type TEXT NOT NULL`
- `status TEXT NOT NULL`
- `gate_type TEXT`
- `gate_result TEXT`
- `blocked_reason TEXT`
- `started_at TEXT`
- `ended_at TEXT`
- `evidence_refs_json TEXT NOT NULL DEFAULT '[]'`
- `event_refs_json TEXT NOT NULL DEFAULT '[]'`
- `updated_at TEXT NOT NULL`

### 7. `worker_runs`

Purpose: worker runtime projection.

Columns:

- `worker_run_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL`
- `workflow_run_id TEXT`
- `step_run_id TEXT`
- `worker_type TEXT NOT NULL`
- `worker_id TEXT NOT NULL`
- `model_provider TEXT`
- `model_name TEXT`
- `workspace TEXT`
- `status TEXT NOT NULL`
- `lease_owner TEXT`
- `lease_expires_at TEXT`
- `heartbeat_at TEXT`
- `result_ref TEXT`
- `evidence_refs_json TEXT NOT NULL DEFAULT '[]'`
- `exit_reason TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Rules:

- Worker completion produces `WorkerCompleted`, not `TaskClosed`.
- Stale lease reconciliation is a command/event path.

### 8. `evidence`

Purpose: evidence metadata and maturity projection.

Columns:

- `evidence_id TEXT PRIMARY KEY`
- `task_id TEXT NOT NULL`
- `workflow_run_id TEXT`
- `step_run_id TEXT`
- `evidence_type TEXT NOT NULL`
- `maturity TEXT NOT NULL` — `advisory | observed | verified | certified | release_confirmed`
- `source_type TEXT NOT NULL` — `command | test | browser | api | git | file | deployment | model | human | external_callback`
- `artifact_uri TEXT`
- `artifact_hash TEXT`
- `summary TEXT NOT NULL`
- `producer_type TEXT NOT NULL`
- `producer_id TEXT NOT NULL`
- `verifier TEXT`
- `acceptance_refs_json TEXT NOT NULL DEFAULT '[]'`
- `stale_status TEXT NOT NULL DEFAULT 'fresh'` — `fresh | stale | conflicting | waived`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Rules:

- Large artifacts stay outside SQLite.
- Ledger records path/URI/hash and links them to criteria.
- Advisory evidence cannot close tasks.

### 9. `policy_decisions`

Purpose: command admission and risk gate audit.

Columns:

- `policy_decision_id TEXT PRIMARY KEY`
- `command_id TEXT`
- `actor_type TEXT NOT NULL`
- `actor_id TEXT NOT NULL`
- `action TEXT NOT NULL`
- `risk_level TEXT NOT NULL`
- `decision TEXT NOT NULL` — `allow | deny | require_human | require_project_policy`
- `policy_source TEXT NOT NULL`
- `reason TEXT NOT NULL`
- `required_evidence_json TEXT NOT NULL DEFAULT '[]'`
- `created_at TEXT NOT NULL`

### 10. `human_approvals`

Purpose: human-in-command queue and audit projection.

Columns:

- `approval_id TEXT PRIMARY KEY`
- `task_id TEXT`
- `workflow_run_id TEXT`
- `command_id TEXT`
- `requested_action TEXT NOT NULL`
- `risk_level TEXT NOT NULL`
- `status TEXT NOT NULL` — `requested | approved | rejected | expired | cancelled`
- `request_summary TEXT NOT NULL`
- `evidence_summary_json TEXT NOT NULL DEFAULT '[]'`
- `consequence_summary TEXT NOT NULL`
- `escape_path TEXT`
- `requested_by TEXT NOT NULL`
- `decided_by TEXT`
- `requested_at TEXT NOT NULL`
- `decided_at TEXT`

### 11. `adapter_observations`

Purpose: capture observed external/legacy state without granting it canonical authority.

Columns:

- `observation_id TEXT PRIMARY KEY`
- `adapter_name TEXT NOT NULL` — `kanban | taskpool | brain | cron | gateway | session | filesystem`
- `external_ref TEXT NOT NULL`
- `observed_type TEXT NOT NULL`
- `observed_hash TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `reconciled_event_id TEXT`
- `conflict_event_id TEXT`
- `observed_at TEXT NOT NULL`

Rules:

- Observations are raw input.
- Reconciliation into canonical facts requires command/event handling.
- Mismatches produce `AdapterConflictDetected` events.

### 12. `projection_checkpoints`

Purpose: projector rebuild state.

Columns:

- `projection_name TEXT PRIMARY KEY`
- `last_sequence INTEGER NOT NULL DEFAULT 0`
- `status TEXT NOT NULL` — `ok | rebuilding | failed`
- `error TEXT`
- `updated_at TEXT NOT NULL`

## Core event types for Phase 1

Minimum event set:

- `TaskObservedFromAdapter`
- `TaskCreated`
- `TaskUpdated`
- `TaskBlocked`
- `TaskReopened`
- `TaskClosed`
- `EligibilityChecked`
- `WorkerRunObserved`
- `WorkerClaimed`
- `WorkerHeartbeatRecorded`
- `WorkerCompleted`
- `WorkerFailed`
- `EvidenceObserved`
- `EvidenceAttached`
- `ProofCheckPassed`
- `ProofCheckFailed`
- `PolicyDecisionRecorded`
- `HumanApprovalRequested`
- `HumanApprovalGranted`
- `HumanApprovalRejected`
- `AdapterConflictDetected`
- `ProjectionRebuilt`

## Adapter migration strategy

### Stage A — read-only observation

- Read existing Kanban/taskpool/cron/worker/evidence state.
- Store raw snapshots in `adapter_observations`.
- Emit observation events only when idempotency hash changes.
- Build projections from ledger observations.

### Stage B — canonical command gateway for new Control Tower actions

- New dashboard/CLI Control Tower actions write commands into ledger.
- Legacy stores are updated by adapters after ledger events.
- Conflicts produce conflict events.

### Stage C — write-through adapters

- Existing `hermes kanban` and taskpool write paths call the command gateway or emit ledger commands.
- Legacy stores become caches/read models.

### Stage D — enforcement

- Closeout, worker start, human approvals, and high-risk writes require ledger policy/evidence state.
- Direct legacy writes are either blocked or marked as external observations requiring reconciliation.

## First read-only snapshot shape

A Phase 1 `control_tower_snapshot` should return:

```json
{
  "schema_version": 1,
  "generated_at": "ISO-8601",
  "ledger": {
    "path": "~/.hermes/control_tower.db",
    "last_sequence": 0,
    "projection_status": "ok"
  },
  "taskpool_health": {
    "active": 0,
    "ready": 0,
    "blocked": 0,
    "claimed": 0,
    "running": 0,
    "review": 0,
    "stale_leases": 0
  },
  "next_dispatchable": null,
  "top_blockers": [],
  "worker_runs": [],
  "cron_watchdogs": [],
  "evidence_summary": {
    "advisory": 0,
    "observed": 0,
    "verified": 0,
    "certified": 0,
    "release_confirmed": 0,
    "stale_or_conflicting": 0
  },
  "adapter_conflicts": []
}
```

## Open decisions remaining

1. UI host: built-in page, repo plugin, or private plugin first.
2. Initial action level: read-only only vs L1/L2 actions.
3. Integration timing: ledger-first read-only aggregation vs immediate command write-through.
4. External dependency stance: Hermes-native only vs optional Temporal/OpenTelemetry hooks.

## Stop condition

This schema proposal completes the unique fact source decision for Phase 0. Code implementation should not start until UI host and initial action level are confirmed.
