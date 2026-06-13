# Modular Workflow Kernel Phase 16 ADR: External Apply Executor Scaffold

## Status

Accepted — target-specific dry-run scaffold only.

## Context

Phase 15 created the external side-effect human gate and left the system blocked until a concrete adapter target is named. The next safe move is not real mutation; it is a fail-closed apply executor scaffold that proves the executor requires a target adapter and authorization packet before doing anything.

## Decision

Add a target-specific external apply scaffold:

```text
External Side-effect Human Gate
  → External Apply Executor Scaffold
  → required --target-adapter + --authorization-packet
  → dry-run receipt
  → STOP before external mutation
```

The scaffold allows only named dry-run target aliases:

```text
taskpool_outbox_simulator
lark_task_dry_run
github_issue_dry_run
telegram_notification_dry_run
```

It rejects missing authorization packets, mismatched gate packets, non-pending gate packets, unknown targets, and any task with `external_side_effects_enabled=true`.

## Explicit non-goals

This phase does not:

- call external APIs;
- send notifications;
- mutate Lark/GitHub/Jira/Telegram;
- enable external side effects;
- accept arbitrary target names.

## Artifacts

- Schema: `schemas/runtime_external_apply_scaffold_packet.schema.json`
- Script: `scripts/workflow_runtime_external_apply_scaffold.py`
- Tests: `tests/scripts/test_workflow_runtime_external_apply_scaffold.py`
- Runtime artifacts: `docs/private/workflow-kernel-runtime-external-apply-scaffold/*`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_external_apply_scaffold.py \
  --target-adapter taskpool_outbox_simulator \
  --authorization-packet docs/private/workflow-kernel-runtime-external-side-effect-gates/<gate>.json \
  --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_external_apply_scaffold.py
```

Expected invariants:

```text
workflow_external_apply_status = external_apply_scaffold_dry_run_ready
external_side_effects_enabled = false
dry_run_receipt.would_apply = false
```

## Next boundary

A future real apply executor must require explicit target-specific authorization and produce external readback/receipt/rollback evidence before any third-party mutation is considered complete.
