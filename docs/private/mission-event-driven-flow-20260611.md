# Hermes Mission event-driven flow landing

Date: 2026-06-11
Scope: Hermes Agent mission/self-architecture control plane.
SSOT purpose: keep the event-driven mission flow contract in one place so chat, cron, code, tests, and future taskpool wiring reference the same model instead of copying divergent rules.

## Objective

Convert long-running Hermes self-architecture advancement from timer-primary polling to event-primary flow:

1. Mission state changes create durable flow signals.
2. Existing governed mission cron jobs are moved to run soon when pending signals exist.
3. Periodic cron remains only as a maintenance/watchdog boundary when no event signal is pending.
4. Agent execution remains behind the existing mission tick prompt, budget, blocked-action, evidence, and human-intervention gates.

## Non-goals / red lines

- Do not execute agent work inline from `append_event`.
- Do not bypass mission budgets or open human decisions.
- Do not create a second taskpool or second worker system.
- Do not authorize external sends, remote push/merge, production deploy, destructive deletes, secret writes, or arbitrary shell execution.
- Do not treat a pending signal as completion evidence.

## Implemented slice

Files:

- `hermes_cli/missions.py`
- `hermes_cli/subcommands/mission.py`
- `tests/test_missions.py`
- `tests/hermes_cli/test_missions.py`
- `hermes_cli/control_tower.py`
- `tests/hermes_cli/test_control_tower_snapshot.py`

New ledger layer:

- SQLite table: `mission_flow_signals`
- Fields: `signal_id`, `mission_id`, `source_event_id`, `signal_type`, `status`, `payload_json`, `created_at`, `consumed_at`
- Pending signals are append-only control edges; source events remain in `mission_events`.

Actionable source events:

- `mission_enabled`
- `mission_resumed`
- `decision_recorded`
- `taskpool_status_changed`

New mission event types:

- `flow_signal_enqueued`
- `flow_signal_dispatched`
- `maintenance_tick`

Runtime behavior:

- `append_event(..., event_type in actionable)` enqueues one idempotent pending flow signal bound to the source event id.
- `stage_report` is deliberately not actionable: a cron agent must write a stage report before ending, and treating that report as a fresh wake signal creates an infinite self-trigger loop. Stage reports remain durable evidence/read-back only.
- If a mission cron job is installed, `dispatch_pending_flow_signals()` calls `cron.jobs.trigger_job(job_id)` to set `next_run_at=now`; this schedules the existing cron runner rather than executing work inline.
- The explicit recovery/manual close-the-loop path is `hermes mission dispatch-events <mission_id> --run-due`: after scheduling existing mission cron jobs it invokes `cron.scheduler.tick(verbose=False, sync=True)` once, so the governed cron runner actually executes due work instead of only projecting a due timestamp. The default taskpool event hook still does not run agents inline.
- `MissionStore.tick(... dry_run=False)` first consumes the oldest pending flow signal and marks the prompt mode as `event`.
- If there is no pending signal, the same tick is classified as `maintenance`, and a `maintenance_tick` event is appended.
- `brain_task_orchestrator.py` now bridges relevant Hermes self-architecture taskpool events (`task_added`, `task_transitioned`, `task_dispatch_authorized`) into `taskpool_status_changed` mission events. The bridge is scoped to Hermes/local-brain/evolution architecture tasks, is fail-soft, can be disabled with `HERMES_TASKPOOL_MISSION_BRIDGE_DISABLED=1`, and only mutates the local mission ledger / schedules the already-installed mission cron.
- Control Tower now exposes two read-only Mission projections: `mission_flow_signals` with pending/stale age, dispatch, maintenance, consumed count, and consumed-latency metrics; and `mission_taskpool_bridge` with recent bridged taskpool events plus pending bridge signal ids. These projections read the Mission ledger only and are not lifecycle/executor owners.
- The Control Tower dashboard plugin renders an operator-facing `Mission event flow` panel from those two projections: top-line signal/latency/bridge metrics, pending signal rows, and recent taskpool bridge rows. This is visual/read-only; mutation remains through MissionStore/Control Tower gated APIs only.
- Open human decisions still block tick prompt generation.
- Budget exhaustion still pauses the mission before any prompt is produced.

CLI surface:

- `hermes mission signals <mission_id> [--json]`
- `hermes mission dispatch-events <mission_id> [--run-due] [--json]`
- `hermes mission status --json` and `doctor --json` expose pending flow signals.

## Consequence model

Chosen path: event-primary signal queue inside MissionStore plus cron trigger scheduling.

Why this path:

- Minimal blast radius: mission control plane only.
- Preserves existing cron/agent scheduler as the governed execution boundary.
- Provides durable read-back and idempotency through SQLite.
- Lets periodic cron become a watchdog instead of the only progress mechanism.

Rejected path A: direct agent execution inside `append_event`.
Reason: collapses event recording, authorization, execution, and evidence into one side effect; violates event-governed executor boundaries.

Rejected path B: separate daemon / new worker pool.
Reason: creates a competing control plane and new lifecycle owner before MissionStore has a proven event queue.

## Verification commands

- `./venv/bin/python -m py_compile hermes_cli/missions.py hermes_cli/subcommands/mission.py hermes_cli/control_tower.py plugins/control-tower/dashboard/plugin_api.py tests/hermes_cli/test_control_tower_snapshot.py`
- `node --check plugins/control-tower/dashboard/dist/index.js`
- `./venv/bin/python -m pytest -q -o addopts='' tests/test_missions.py tests/hermes_cli/test_missions.py tests/hermes_cli/test_control_tower_snapshot.py tests/plugins/test_control_tower_dashboard_plugin.py`

## Next safe extensions

1. Add a bounded no-agent watchdog report that only reports scheduler/runner failures, not ordinary zero-pending healthy states.
2. Triage taskpool agent-team `human_escape` / post-ack worker timeout blockers separately from Mission flow; do not mix that executor repair into the Mission projection slice.

These should be separate slices with tests and explicit side-effect boundaries.
