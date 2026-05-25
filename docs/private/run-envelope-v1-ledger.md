# Hermes RunEnvelopeV1 / Run Event Ledger

Status: initial implementation for 12Factor Agents Factor 5 + 12.

## Fact source

`agent/run_envelope.py` defines the shared append-only run fact contract:

- `RunEnvelopeV1`
- `append_run_event(...)`
- `run_events.jsonl` append-only event stream
- `run_reducer_checkpoint.json` reducer projection keyed by `run_id`

Default storage:

- `${HERMES_RUN_LEDGER_DIR}` when set
- otherwise `${HERMES_HOME:-~/.hermes}/state/run_ledger/`

## Schema shape

Required core fields in every event:

- `schema`: `hermes_run_envelope_v1`
- `run_id`: stable per run/attempt (`kanban:<task_runs.id>`, `taskpool:<markdown-run-id>`, `dispatch_tick:<generated_at>`)
- `surface`: origin surface (`kanban`, `taskpool`, `dispatch_tick`)
- `lifecycle_state`: reducer state for this run
- `trigger`: structured origin/actor/task payload
- `proof_refs`: evidence references that prove the lifecycle state
- `projection_refs`: compatibility/projection locations that humans or legacy code still read
- `reducer_checkpoint`: `hermes_run_reducer_checkpoint_v1` summary for rebuilding current state

Optional fields include `task_id`, `session_id`, `prompt_version`, `tool_batch`, `handoff_refs`, and `metadata`.

## Projections / compatibility layers

These stores are explicitly projections or compatibility surfaces, not the unified run fact source:

- local-brain markdown task notes and dashboards (`brain_markdown`, `brain_dashboard`, `brain_task_sorted_pool`)
- Kanban SQLite board (`kanban.db` tasks/task_runs/task_events)
- dispatch tick JSONL/state files (`brain_task_dispatch_tick_log`, `brain_task_dispatch_tick_state`)

They continue to exist for existing UI/CLI flows, but new cross-surface audit/replay should consume the run ledger first and use projection refs for drill-down.

## Wired paths in this slice

1. Kanban worker path
   - `hermes_cli/kanban_db.py::claim_task`
   - emits `run_id=kanban:<task_runs.id>`
   - emits `lifecycle_state=running`
   - proof ref: `kanban_db:task_events:<task_id>:claimed`

## Pending external-script paths

The local-brain taskpool and dispatch tick paths are intentionally not claimed as wired in this repo slice. They depend on scripts under `~/.hermes/scripts` / `personal-hermes-brain`, which are outside this checkout and must be landed with their own script-side tests before this repo documents them as complete.

## Verification

Targeted tests:

```bash
./.venv/bin/python -m pytest \
  tests/agent/test_run_envelope.py \
  tests/agent/test_run_controller.py \
  tests/hermes_cli/test_kanban_db.py::test_claim_writes_unified_run_envelope_for_kanban_projection \
  tests/hermes_cli/test_kanban_db.py::test_kanban_run_envelope_tracks_heartbeat_complete_and_block \
  -q
```

Runtime smoke for future external-script wiring should be added in the personal-brain/script repository when `append_dispatch_tick_run_envelope` lands there.
