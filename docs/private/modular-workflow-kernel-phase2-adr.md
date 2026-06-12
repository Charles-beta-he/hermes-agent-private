# Modular Workflow Kernel Phase 2 ADR

Date: 2026-06-12
Status: accepted for Phase 2 read-only projection implementation
Scope: route trace schema, run lockfile schema, and read-only workflow projection for Hermes modular self-advancing workflow kernel.

## Decision

Phase 2 extends the Phase 1 contract seed with two reproducibility artifacts and one read-only compiler:

1. `route_trace.schema.json` records how a module's model route is selected: alias, selected abstract model, fallback chain, policy version, checked constraints, and eval gate.
2. `run_lockfile.schema.json` records the frozen preview of a workflow run: workflow version, registry versions, module versions, route traces, acceptance criteria, evidence policy, and fail-closed side-effect flags.
3. `workflow_projection.py` compiles the static registries into operator-readable preview artifacts without claiming tasks, calling models, writing adapters, mutating taskpool state, or sending external messages.

The projection is explicitly a `run_preview_*`, not a runtime authorization. It exists to make future taskpool/executor integration reproducible and auditable before any worker can act.

## Why this path

The user approved continuing until the scheme boundary. The next safe boundary after Phase 1 is not executor integration; it is reproducibility and observability:

```text
contract seed
  → route trace
  → run lockfile
  → read-only projection
  → dry-run acceptance compiler
  → local reversible executor
  → gated write-through adapter
```

Jumping directly from registry to runtime execution would couple model routing, taskpool claims, and adapter effects before the system can explain which model/module/policy version produced a decision.

## Facts and invariants

- Runtime execution remains disabled.
- Write-through remains disabled.
- External side effects remain disabled.
- Model routing remains pinned by default.
- Dynamic routing and auto-promotion remain disabled in production controls.
- Projection artifacts are local files under `docs/private/workflow-kernel-projections/`.
- Generated route traces include `phase2_projection_only_no_model_call` to prevent confusing preview routing with actual model invocation.

## Implemented artifacts

- `schemas/route_trace.schema.json`
- `schemas/run_lockfile.schema.json`
- `scripts/workflow_projection.py`
- `tests/scripts/test_workflow_projection.py`
- Validator extension for route trace and run lockfile schema presence/title checks.
- Projection artifacts for `decision_brief_pack`:
  - `docs/private/workflow-kernel-projections/decision_brief_pack.run_preview_78eee6afcec1f12a.run_lockfile.json`
  - `docs/private/workflow-kernel-projections/decision_brief_pack.run_preview_78eee6afcec1f12a.route_traces.json`
  - `docs/private/workflow-kernel-projections/decision_brief_pack.run_preview_78eee6afcec1f12a.summary.json`

## Acceptance evidence

- `python3 scripts/workflow_contract_validator.py --json` returns PASS and includes route/run schema paths.
- `python3 scripts/workflow_projection.py --json` returns a projection-only package with 5 route traces and disabled runtime/write-through/external side effects.
- `python3 scripts/workflow_projection.py --write --json` writes local read-only projection artifacts.
- `./scripts/run_tests.sh tests/scripts/test_workflow_contract_validator.py tests/scripts/test_workflow_projection.py` passes 14 tests.
- `python3 -m py_compile scripts/workflow_contract_validator.py scripts/workflow_projection.py tests/scripts/test_workflow_contract_validator.py tests/scripts/test_workflow_projection.py` passes.

## Explicit non-goals

- No taskpool claim/handoff.
- No worker execution.
- No model API call.
- No gateway or external adapter write.
- No dynamic route promotion.
- No dashboard as second fact source.

## Next boundary

The next safe step is a dry-run Acceptance Compiler that consumes:

```text
Workflow Registry + Model Registry + Run Lockfile + Project/Profile Context
```

and emits frozen criteria for a run preview. It must remain projection-only until a human gate authorizes local reversible execution.
