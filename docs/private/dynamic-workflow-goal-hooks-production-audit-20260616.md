# Dynamic Workflow / Goal / Hooks Production Audit — 2026-06-16

## Scope

Analyze Claude Code-style Dynamic Workflow, `/goal`, and hooks against Hermes' own architecture, then land the first low-risk production slice and identify remaining non-production-grade surfaces.

## Objective model

Success means Hermes gains a real, test-covered control-plane primitive rather than a prose-only recommendation:

- `/goal` has a machine-readable `goal_spec_v1` contract.
- `/goal` has an advisory `goal_route_decision_v1` dry-run route to `session | taskpool | mission | cron | human_gate`.
- No taskpool, mission, cron, or external side effect is triggered by setting a goal.
- Existing `/goal`, `/subgoal`, CLI, and TUI behavior remains backward compatible.
- Remaining gaps are classified as production gaps with a verifiable next slice.

## Evidence read

- `hermes_cli/goals.py`: persistent cross-turn goal loop stored in SessionDB `state_meta` under `goal:<session_id>`.
- `gateway/slash_commands.py`: `/goal` sets a goal and queues kickoff; `/subgoal` appends criteria.
- `gateway/hooks.py`: event hook system for gateway/session/agent/command events; handler errors are logged and do not block normal flow.
- `~/.hermes/diagnostics/DYNAMIC-WORKFLOW-MIGRATION.md`: documents composite tasks, parallel execution, convergence loops, adversarial verification, checkpoint/resume, and workflow pattern integration.
- `~/.hermes/diagnostics/OBSERVER-ARCHITECTURE.md`: documents preflight ambiguity detection, observer events, hook-based observation, and theoretical test coverage.
- `~/.hermes/scripts/hermes_hook_registry_selfcheck.py`: validates shell hook registry gates and allowlisted commands.

## Landed production slice

### GoalSpec + GoalRouteDecision dry-run

Changed:

- `hermes_cli/goals.py`
  - Added `GoalRouteDecision` dataclass.
  - Added `goal_route_decision()` dry-run classifier.
  - Added `build_goal_spec()`.
  - Extended `GoalState` with backwards-compatible `spec` and `route` dictionaries.
  - Extended `GoalManager.set()` to persist `goal_spec_v1` and `goal_route_decision_v1`.
  - Extended `status_line()` to show route and criteria count.

Tests:

- `tests/hermes_cli/test_goals.py`
  - `test_set_persists_goal_spec_and_route_decision`
  - `test_goal_route_decision_classifies_session_taskpool_mission_cron_and_human_gate`

Verification:

```bash
PYTEST_ADDOPTS='' ./venv/bin/python -m pytest \
  tests/hermes_cli/test_goals.py \
  tests/tui_gateway/test_goal_command.py \
  -q -o addopts=''
```

Result: `63 passed, 9 warnings`.

```bash
./venv/bin/python -m py_compile hermes_cli/goals.py
```

Result: pass.

## Production-gap audit

### G1 — `/goal` is still session-first, not workflow-control-plane complete

Current: `/goal` can now persist a spec and route dry-run, but it still executes as a session continuation loop.

Risk: complex goals can still overuse conversation context and tool-turn budget if not promoted to taskpool/mission.

Next production slice: gated `/goal promote --dry-run` that renders the taskpool/mission/cron candidate without writing lifecycle state.

Acceptance evidence:

- fixture goals route to the correct layer;
- promotion output is a JSON candidate;
- no taskpool/mission/cron state mutates in dry-run.

### G2 — Judge is still response-centric, not proof-bundle-centric

Current: `judge_goal()` evaluates the last response plus optional subgoals.

Risk: a reply that claims completion may pass if the judge accepts prose, while Hermes production standard requires command/file/UI/proof evidence.

Next production slice: `GoalEvidenceBundle` and evidence-aware judge contract.

Acceptance evidence:

- no-proof completion claim cannot become `done_verified`;
- file/command/proof evidence can become `done_verified`;
- blocked/needs-human remains a terminal-but-not-successful state.

### G3 — Gateway hooks are observer-grade by default

Current: `gateway/hooks.py` catches handler errors and never blocks the main path.

Risk: fine for observation, insufficient for gate hooks.

Existing related strength: `hermes_hook_registry_selfcheck.py` validates hook registry gates and shell-hook allowlists, and current run reports `ok=true`.

Next production slice: gateway hook manifest classes:

- `observer`: fail-open, no side effect;
- `advisor`: fail-open, evidence-only;
- `gate`: fail-closed with timeout and reason;
- `executor`: default-off, explicit side-effect class.

Acceptance evidence:

- observer hook errors do not block;
- gate hook errors produce `require-human`/blocked classification;
- manifests without class/fail policy fail selfcheck.

### G4 — Dynamic Workflow migration remains partly theoretical/prototype-shaped

Current: diagnostics claim Dynamic Workflow migration and tests pass, but the main `/goal` path is not compiled into workflow DAG nodes.

Verified tests:

```bash
python3 ~/.hermes/scripts/test_theoretical_improvements.py
python3 ~/.hermes/scripts/test_dynamic_workflow.py
```

Result: `89/89 passed` and `26 passed`.

Risk: operators may read the migration doc as production-complete, while actual production integration is partial.

Next production slice: `workflow_plan_v1` dry-run compiler from `GoalSpec`.

Acceptance evidence:

- complex GoalSpec renders an acyclic DAG;
- every node has evidence requirement and rollback boundary;
- no node executes during compile.

### G5 — Checkpoint/resume exists in Dynamic Workflow diagnostics but is not bound to `/goal` lifecycle

Current: checkpoint manager is documented/test-covered in dynamic workflow scripts; `/goal` state persists, but active plan/proof/remaining work is not checkpointed as a full workflow state.

Risk: context compression or gateway restart can preserve the goal text but lose execution plan/proof context.

Next production slice: goal checkpoint sidecar:

- `goal_id`, `goal_spec`, `route`, `subgoals`, `last_verdict`, `proof_refs`, `remaining_plan`.

Acceptance evidence:

- force checkpoint;
- clear in-memory manager;
- reload from SessionDB + sidecar;
- status shows same route/subgoals/proof refs.

## Recommended next order

1. Goal promotion dry-run (`/goal promote --dry-run`).
2. Evidence-aware judge (`GoalEvidenceBundle`).
3. Hook manifest class/fail-policy selfcheck.
4. GoalSpec → `workflow_plan_v1` dry-run compiler.
5. Goal checkpoint sidecar / pre-compact binding.

## Boundary

This slice intentionally does not:

- enqueue taskpool work from `/goal`;
- dispatch mission/cron actions;
- change gateway hook runtime blocking behavior;
- execute third-party dynamic workflow code;
- alter system prompts or toolsets.

Those are separate gated implementation slices.
