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
- `flow_signal_dispatch_started`
- `flow_signal_dispatched`
- `maintenance_tick`

Runtime behavior:

- `append_event(..., event_type in actionable)` enqueues one idempotent pending flow signal bound to the source event id.
- `stage_report` is deliberately not actionable: a cron agent must write a stage report before ending, and treating that report as a fresh wake signal creates an infinite self-trigger loop. Stage reports remain durable evidence/read-back only.
- If a mission cron job is installed, `dispatch_pending_flow_signals()` first atomically claims one `flow_signal_dispatch_started` intent for `(signal, exact job, run_due mode)`, then calls `cron.jobs.trigger_job(job_id)` to set `next_run_at=now`; this schedules the existing cron runner rather than executing work inline. A success audit is appended only after the trigger returns successfully.
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

- `./.venv/bin/python -m py_compile hermes_cli/missions.py hermes_cli/subcommands/mission.py hermes_cli/control_tower.py plugins/control-tower/dashboard/plugin_api.py tests/hermes_cli/test_control_tower_snapshot.py`
- `node --check plugins/control-tower/dashboard/dist/index.js`
- `./.venv/bin/python -m pytest -q -o addopts='' tests/test_missions.py tests/hermes_cli/test_missions.py tests/hermes_cli/test_control_tower_snapshot.py tests/plugins/test_control_tower_dashboard_plugin.py`

## 2026-08-10 continuation-edge Spec Gate

### Goal

Close the remaining timer-primary gap after a bounded Mission slice finishes: a successful or partially successful tick must be able to durably request the next dependency-safe tick without making `stage_report` actionable. The existing mission cron remains the governed runner and a low-frequency watchdog/recovery path.

### Non-goals and authority boundary

- Do not make `stage_report` actionable; it remains evidence-only so report writing cannot recursively wake itself.
- Do not execute agent work inline from the Mission DB write path or create a second worker/daemon.
- Do not permit more than one canonical integration writer or weaken candidate digest/review gates.
- Do not infer continuation after a human gate, exhausted budget, terminal/paused Mission, stale tick, or absent real evidence.
- Do not open push, deploy, external delivery, credentials, destructive actions, production effects, or any authority blocked by the Mission contract.

### Business rules and invariants

1. Every executable non-dry-run tick receives a fresh opaque continuation token recorded on its `tick_prompt_created` event and rendered in that exact tick prompt.
2. A new `continuation_requested` event is actionable and may schedule the already-installed exact Mission cron job to run soon.
3. Continuation admission requires: active Mission, no open human decision, remaining tick budget, non-empty next step, token belonging to the latest executable tick, and at least one `stage_report:<event_id>` that resolves to this Mission after that tick. The report must contain real evidence, have `needs_user=false`, and bind the exact same next step.
4. The token is the fencing/idempotency identity. Normal exact replay returns the existing event, creates no second signal, performs no new scheduler trigger, and appends no duplicate dispatch audit. If admission remains valid but any pending signal has a durable dispatch intent without its matching success audit, exact actionable replay instead fails explicitly with `dispatch_outcome_unknown`; it still performs no scheduler trigger and fabricates no success audit. A pre-atomic legacy row missing its signal is repaired only while the original admission conditions still hold; repair reuses the signal identity recorded by the existing enqueue audit and does not append a second enqueue audit. Replay after pause, human block, budget exhaustion, or a newer tick remains a receipt readback and must not recreate executable authority. Same-token semantic drift fails closed.
5. `stage_report` remains non-actionable. The tick prompt instructs the writer to append its stage report first and request continuation only when another safe bounded step exists.
6. The continuation event grants wake-up only. It does not approve candidate bytes, commit, phase promotion, budget expansion, or any external/runtime effect.
7. Cron cadence remains watchdog/recovery. Normal successful slice-to-slice advancement is `continuation_requested → mission_flow_signal → one exact runnable installed Mission script → event-mode tick`. Runnable means `enabled=true` and `state!=paused`; dispatch, doctor, and watchdog share that predicate. Name/prompt substrings, disabled jobs, and paused jobs are never wake authority; multiple runnable exact-script matches fail closed as ambiguous.
8. Every newly inserted actionable source event, its one flow signal, and the `flow_signal_enqueued` audit event commit in one SQLite transaction. Cron scheduling remains outside that transaction. Before crossing that boundary, one SQLite `flow_signal_dispatch_started` intent is atomically claimed for `(signal, exact job, run_due mode)`. Concurrent callers cannot trigger the same operation twice. A scheduler rejection that proves no trigger occurred releases the claim transactionally and appends `flow_signal_dispatch_rejected`, permitting a later safe retry. A crash before or after the jobs.json update but before the success audit remains `dispatch_outcome_unknown`: later callers do not retrigger or fabricate success, while doctor/watchdog surface the unresolved intent for operator inspection. This is fail-closed at-most-once scheduling, not a false cross-storage exactly-once claim.
9. Non-dry-run tick admission, hard-budget allocation, pending-signal selection/consumption, and `tick_prompt_created` persistence are serialized in one SQLite write transaction. Concurrent callers cannot allocate more durable prompts than `max_ticks`.
10. `continuation_requested` is a typed-only event. The generic `append_event()` sink must reject it so token, evidence, lifecycle, and budget admission cannot be bypassed.
11. A tick must not end with a required review left only in ephemeral delegation. The prompt requires synchronous completion or a durable external artifact plus `review-complete`. Independently of prompt compliance, doctor/watchdog detect an old executable tick with no later stage report, continuation, review completion, decision gate, or terminal/paused lifecycle event.

### Negative acceptance cases

- Stage report alone creates zero pending signals.
- Missing/forged/cross-Mission/pre-tick/`needs_user`/next-step-drift stage-report evidence or missing next step is rejected with zero continuation events/signals.
- Generic `append_event(..., "continuation_requested", ...)` is rejected with zero continuation events/signals.
- Paused, terminal, or human-blocked Mission rejects continuation.
- Stale-token submission after a newer tick is rejected.
- Concurrent exact replay creates exactly one continuation event and one flow signal.
- Concurrent executable tick calls never allocate more than the hard tick budget.
- A process failure after the SQLite commit but before cron dispatch leaves one durable pending signal; exact replay of a legacy stranded event repairs one missing signal without duplicating events.
- A crash after dispatch intent claim but before success audit leaves one intent, zero success audits, and a pending signal; replay/direct dispatch reports `dispatch_outcome_unknown` and performs zero additional scheduler triggers.
- Concurrent dispatch callers for one `(signal, exact job, run_due mode)` produce one intent, at most one scheduler trigger, and at most one success audit.
- Exhausted tick budget rejects continuation even before a later tick pauses the Mission status projection.
- Exact replay after pause or budget exhaustion never repairs or dispatches a missing executable signal.
- Same token with different next-step/evidence is an idempotency conflict, not latest-wins.
- Consuming the continuation signal produces an event-mode tick; it does not recursively emit another continuation signal.
- Cron dispatch matches exactly one runnable installed Mission script; disabled/paused jobs and name/prompt decoys are never triggered or re-enabled, and duplicate runnable exact-script jobs fail closed without dispatch.
- A falsy scheduler trigger result releases its started claim, records a rejection audit, and can be retried without `dispatch_outcome_unknown`.
- An old executable tick with no later durable closure/progress event makes doctor warn and watchdog alert, including the abandoned ephemeral-review case.
- Real CLI semantic failures propagate a non-zero process exit code to shell/cron callers.

### Evidence required

- Focused MissionStore and CLI RED→GREEN tests, including real temporary SQLite and concurrent writers.
- Migration/readback against an existing pre-change Mission DB, including concurrent initializers serialized before schema inspection/ALTER.
- Python compile, Ruff/diff check, focused Mission suites, and broader regression suite.
- Exact candidate identity plus independent read-only adversarial review before local commit.
- Live CLI readback proving the installed runtime exposes the new command.
- One existing Mission advanced through the new continuation event, with event/signal/cron/tick/Git/process readback. This is local-flow evidence only, not runtime or production certification.

## Next safe extensions

1. Keep the bounded no-agent watchdog silent for ordinary zero-pending healthy states and alert only on stale event flow.
2. Triage taskpool agent-team `human_escape` / post-ack worker timeout blockers separately from Mission flow; do not mix that executor repair into the Mission projection slice.
3. Consider a durable active-run lease only if real concurrent tick execution is reproduced; the continuation token added here is a stale-completion fencing token, not a claim that writer concurrency has been globally solved.

These should remain separate slices with tests and explicit side-effect boundaries.
