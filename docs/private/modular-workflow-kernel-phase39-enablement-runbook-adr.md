# Modular Workflow Kernel Phase 39 ADR: Enablement Runbook and Rollback Drill

## Status

Accepted — an operator runbook and rollback drill now exist for the runtime/scheduler enablement boundary, without executing any runtime or scheduler change.

## Context

Phase 38 mapped the order and recommended Phase 39 under `A_READ_ONLY_CONTINUE`. The next safe step is to turn the Phase 37 authorization packet and Phase 38 sequence map into concrete preflight, dry-run, rollback, and go/no-go commands.

## Decision

Add:

```text
scripts/workflow_enablement_runbook.py
```

It compiles:

- preflight commands;
- dry-run commands using temp receipt ledger only;
- rollback drill steps;
- go/no-go gates;
- closed boundaries inherited from the authorization packet.

The script emits:

```text
ENABLEMENT_RUNBOOK_READY
```

## Evidence

Verification:

```text
./venv/bin/python -m py_compile \
  scripts/workflow_enablement_runbook.py \
  scripts/workflow_sequence_map.py \
  scripts/workflow_runtime_enablement_packet.py

./scripts/run_tests.sh \
  tests/scripts/test_workflow_enablement_runbook.py \
  tests/scripts/test_workflow_sequence_map.py \
  tests/scripts/test_workflow_runtime_enablement_packet.py
```

Readback:

```text
12 tests passed, 0 failed
status=ENABLEMENT_RUNBOOK_READY
preflight=4
dry_run=3
rollback=5
```

## Non-goals

This phase does not execute the runbook. It does not enable runtime switch, mutate cron, trigger jobs, call credentialed model generation, or send external messages.

## Next Phase

Phase 40 should add a read-only receipt/projection drift watcher under `A_READ_ONLY_CONTINUE`, unless the user explicitly authorizes B or C.
