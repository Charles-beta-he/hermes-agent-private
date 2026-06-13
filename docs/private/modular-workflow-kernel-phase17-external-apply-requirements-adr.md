# Modular Workflow Kernel Phase 17 ADR: External Apply Requirements Fully Defined

## Status

Accepted — real-apply requirements and target choices fully enumerated; real external apply remains blocked.

## Context

Phase 16 proved the external apply executor scaffold requires a target adapter and authorization packet and still produces only a dry-run receipt. The user asked to "推进至全部明确". The safe interpretation is to remove ambiguity around the next real-apply boundary without inventing or executing a concrete external mutation target.

## Decision

Add the real-apply requirement layer:

```text
External Apply Executor Scaffold
  → Target Adapter Capability Registry
  → Real Apply Contract Packet
  → Receipt Schema
  → Rollback Evidence Schema
  → Pending target selection
```

This phase makes explicit:

- all currently modeled target adapter options;
- which are contract-only vs dry-run implemented;
- required authority per adapter;
- required inputs before real apply;
- blast radius;
- post-apply verification;
- rollback evidence;
- the safe default.

## Target options

Initial registry:

```text
taskpool_outbox_simulator  implemented_dry_run_only
lark_task                  contract_only_no_real_apply
github_issue               contract_only_no_real_apply
telegram_notification      contract_only_no_real_apply
```

## Explicit non-goals

This phase does not:

- choose one real external target;
- call external APIs;
- send notifications;
- enable `external_side_effects_enabled`;
- weaken the Phase 16 target/authorization checks.

## Artifacts

- Registry: `config/target_adapter_capability_registry.json`
- Contract packet schema: `schemas/runtime_external_apply_contract_packet.schema.json`
- Receipt schema: `schemas/runtime_external_apply_receipt.schema.json`
- Rollback evidence schema: `schemas/runtime_external_apply_rollback_evidence.schema.json`
- Script: `scripts/workflow_runtime_external_apply_contract.py`
- Tests: `tests/scripts/test_workflow_runtime_external_apply_contract.py`
- Runtime contract packet: `docs/private/workflow-kernel-runtime-external-apply-contracts/*`

## Verification

Run:

```bash
python3 scripts/workflow_contract_validator.py --json
python3 scripts/workflow_runtime_external_apply_contract.py --write --json
./scripts/run_tests.sh tests/scripts/test_workflow_runtime_external_apply_contract.py
```

Expected invariants:

```text
workflow_external_apply_contract_status = real_apply_contracts_defined_no_target_selected
real_apply_enabled = false
external_side_effects_enabled = false
selection_status = target_adapter_options_fully_enumerated_pending_selection
```

## Next boundary

The next non-local action requires the human to choose exactly one:

```text
taskpool_outbox_simulator | lark_task | github_issue | telegram_notification
```

and provide/approve operation scope, target identifiers, rollback path, and post-apply verification. Until then, the system has "全部明确" contracts but no real external side effects.
