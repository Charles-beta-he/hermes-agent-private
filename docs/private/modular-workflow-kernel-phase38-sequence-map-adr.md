# Modular Workflow Kernel Phase 38 ADR: Sequence Map and Ordering

## Status

Accepted — the workflow kernel now has an explicit ordered sequence map from Phase 17 through Phase 37, with dependencies, authority class, gates, and next-ladder tracks.

## Context

The user asked to “梳理顺序，全盘推进”. The prior state had reached Phase 37, where crossing runtime/scheduler enablement requires explicit authority. A safe continuation is to make the sequence and next ladder explicit, so later work cannot accidentally skip gates or reorder evidence-sensitive steps.

## Decision

Add:

```text
scripts/workflow_sequence_map.py
```

It emits:

```text
WORKFLOW_SEQUENCE_MAP_READY
```

and records:

- Phase 17-37 phase order;
- dependency edges;
- authority boundary per phase;
- gate text per phase;
- current Phase 38 map boundary;
- next ladder for Phase 39-42;
- closed boundaries without explicit authorization.

## Next ladder

```text
Phase 39: Enablement Runbook and Rollback Drill — A_READ_ONLY_CONTINUE
Phase 40: Receipt/Projection Drift Watcher — A_READ_ONLY_CONTINUE
Phase 41: Single-wrapper Runtime-enable Dry-run — requires explicit B
Phase 42: Single-wrapper Scheduler-native Rollout — requires explicit C
```

## Evidence

Verification:

```text
./venv/bin/python -m py_compile \
  scripts/workflow_sequence_map.py \
  scripts/workflow_runtime_enablement_packet.py \
  scripts/workflow_runtime_readiness_summary.py

./scripts/run_tests.sh \
  tests/scripts/test_workflow_sequence_map.py \
  tests/scripts/test_workflow_runtime_enablement_packet.py \
  tests/scripts/test_workflow_runtime_readiness_summary.py
```

Readback:

```text
12 tests passed, 0 failed
status=WORKFLOW_SEQUENCE_MAP_READY
phase_count=22
validation_errors=[]
recommended_next=Phase39 Enablement Runbook and Rollback Drill under A_READ_ONLY_CONTINUE
```

## Non-goals

This phase does not enable runtime switch, mutate cron, trigger jobs, call credentialed model generation, or send external messages.

## Consequence

The architecture transition now has an explicit order map. Safe default remains Phase 39 under `A_READ_ONLY_CONTINUE`; Phase 41/42 require explicit user authorization.
