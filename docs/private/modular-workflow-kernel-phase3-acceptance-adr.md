# Modular Workflow Kernel Phase 3 Acceptance Dry-Run ADR

Date: 2026-06-12
Status: accepted for dry-run acceptance compiler implementation
Scope: compile frozen acceptance criteria from workflow projection artifacts without opening runtime execution.

## Decision

Add a dry-run Acceptance Compiler that consumes the Phase 2 run lockfile and route traces, then emits a frozen acceptance bundle. The bundle records criteria derived from:

- run lockfile acceptance criteria;
- module contract boundaries;
- model route trace requirements;
- fail-closed safety flags;
- human gate packet template for any transition beyond dry-run.

The compiler does not execute a workflow. It produces the smallest safe artifact needed before asking whether to cross into local reversible executor work.

## Boundary reached

This phase reaches the current方案边界 for autonomous safe landing:

```text
contract seed
  → route trace
  → run lockfile
  → read-only projection
  → frozen dry-run acceptance bundle
  → STOP before local executor/runtime integration
```

The next step, even if local-only, changes from evidence/projection into an executor boundary. That requires a separate human gate because it would introduce runnable workflow behavior and eventually taskpool integration.

## Implemented artifacts

- `schemas/acceptance_bundle.schema.json`
- `scripts/acceptance_compiler.py`
- `tests/scripts/test_acceptance_compiler.py`
- Generated bundle:
  - `docs/private/workflow-kernel-acceptance/decision_brief_pack.run_preview_78eee6afcec1f12a.acc_3c811427ac76ce88.acceptance.json`

## Acceptance evidence

- `python3 scripts/acceptance_compiler.py --json` returns a frozen dry-run bundle with `runtime_enabled=false`, `write_through_enabled=false`, and `external_side_effects_enabled=false`.
- `python3 scripts/acceptance_compiler.py --write --json` writes the local acceptance bundle artifact.
- `./scripts/run_tests.sh tests/scripts/test_workflow_contract_validator.py tests/scripts/test_workflow_projection.py tests/scripts/test_acceptance_compiler.py` passes 16 tests.
- `python3 scripts/workflow_contract_validator.py --json` returns PASS and recognizes the acceptance bundle schema.

## Human gate for next phase

To continue beyond this boundary, the operator must choose one of:

1. `continue_dry_run_only` — keep improving schemas/projections/evals only.
2. `authorize_local_reversible_executor_stub` — implement a local-only executor stub that writes no taskpool state and calls no external adapter.
3. `stop_and_review_architecture` — pause implementation and review architecture.

Recommended default: `continue_dry_run_only` unless executor behavior is explicitly needed.
