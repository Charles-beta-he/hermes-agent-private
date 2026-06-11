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

New ledger layer:

- SQLite table: `mission_flow_signals`
- Fields: `signal_id`, `mission_id`, `source_event_id`, `signal_type`, `status`, `payload_json`, `created_at`, `consumed_at`
- Pending signals are append-only control edges; source events remain in `mission_events`.

Actionable source events:

- `mission_enabled`
- `mission_resumed`
- `decision_recorded`
- `stage_report`

New mission event types:

- `flow_signal_enqueued`
- `flow_signal_dispatched`
- `maintenance_tick`

Runtime behavior:

- `append_event(..., event_type in actionable)` enqueues one idempotent pending flow signal bound to the source event id.
- If a mission cron job is installed, `dispatch_pending_flow_signals()` calls `cron.jobs.trigger_job(job_id)` to set `next_run_at=now`; this schedules the existing cron runner rather than executing work inline.
- `MissionStore.tick(... dry_run=False)` first consumes the oldest pending flow signal and marks the prompt mode as `event`.
- If there is no pending signal, the same tick is classified as `maintenance`, and a `maintenance_tick` event is appended.
- Open human decisions still block tick prompt generation.
- Budget exhaustion still pauses the mission before any prompt is produced.

CLI surface:

- `hermes mission signals <mission_id> [--json]`
- `hermes mission dispatch-events <mission_id> [--json]`
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

- `./venv/bin/python -m py_compile hermes_cli/missions.py hermes_cli/subcommands/mission.py`
- `./venv/bin/python -m pytest -q -o addopts='' tests/test_missions.py tests/hermes_cli/test_missions.py`

## Next safe extensions

1. Add taskpool bridge events that enqueue mission flow signals when Hermes self-architecture tasks move to review/done/blocked.
2. Add dashboard projection for pending mission flow signals in Control Tower.
3. Add a bounded no-agent maintenance watchdog that reports stale pending signals without running agent work.
4. Add event-driven dispatch metrics: pending age, dispatched job ids, consumed latency, maintenance tick count.

These should be separate slices with tests and explicit side-effect boundaries.
