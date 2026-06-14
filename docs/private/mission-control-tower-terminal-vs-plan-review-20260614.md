# Mission Control Tower terminal-ladder vs whole-plan review — 2026-06-14

Mission: `msn_20260610_120149_27c88cfc`
Created: `2026-06-14T17:35:56Z`
Scope: one local/read-only stage slice plus durable private report. No external send, deploy, push, credential access, publication apply, destructive delete, or scheduler mutation was performed.

## Objective model

Success criteria being reconciled:
- Mission-level: durable Mission control plane loop with objective ledger, dynamic cron tick, stage-report, human handoff, and decision resume; every stage report traces to evidence and next step.
- Control Tower canonical target model: first-screen progressability, classified blockers, human actions as decisions/authorizations, safe read-only diagnosis/bounded executors, digest-bound write-through/rollback, and projection/adapters around a canonical Control Tower ledger.
- Workflow-kernel ladder: Phase 92 terminal publication governance summary is allowed to close only that ladder; it must not be misreported as whole-program completion.

Chosen path for this slice: reconcile Phase 92 terminal ladder artifacts against the canonical long-term Control Tower target model and live Control Tower snapshot, then classify remaining work without creating a new publication/apply phase.

## Evidence read

Commands/artifacts verified in this slice:

1. `hermes mission status msn_20260610_120149_27c88cfc`
   - Mission status: `active`
   - Stage: `intake`
   - Budget: `max_ticks=72`, `max_turns_per_tick=8`
   - Recent event: `mission_resumed: user authorized full continuation to fill remaining long-term stable plan items`

2. `docs/private/hermes-control-tower-long-term-stable-plan-20260611.md`
   - Canonical target model says Control Tower is the long-term operational console over a ledger-backed control plane.
   - Success criteria include first-screen progressability, machine-readable blockers, safe diagnosis/bounded executors, digest-bound write-through/rollback, and adapter projections around a canonical ledger.
   - Phase roadmap remains broader than the publication governance ladder: Phase A through G cover operator overview, blocker classifier, evidence debt/closeout, mission/worker/cron recovery, production action UX, safe executor expansion, and self-healing optimization.

3. `docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json`
   - `phase_count=76`
   - `next_ladder=[]`
   - `current_boundary=Phase92 has produced a terminal read-only governance summary; publication remains closed/no-apply and future real reopen requires human exact target/payload/rollback.`

4. `docs/private/workflow-kernel-publication-governance-terminal-summary/workflow_kernel_publication_governance_terminal_summary_20260613_phase92.json`
   - `status=PUBLICATION_GOVERNANCE_TERMINAL_SUMMARY_READY`
   - `terminal_state=closed_no_apply_human_gated_reopen_only`
   - `publication_apply_enabled=false`
   - `external_publication_enabled=false`
   - `unattended_dispatch_enabled=false`
   - `external_side_effects_executed=false`

5. Live Control Tower snapshot via `.venv/bin/python - <<'PY' ... control_tower.build_snapshot() ... PY`
   - `operator_overview.status=projection_drift`
   - `operator_overview.progressable=true`
   - `operator_overview.recommended_next_action=inspect_taskpool_projection_drift`
   - Headline: `Taskpool runtime is dispatchable, but the Control Tower projection is stale.`
   - Critical alerts: `taskpool_projection_drift`, `no_dispatchable_work`, and `projection_governance_degraded`.
   - Taskpool runtime: dispatchable P2 task `2026-06-15-分析抖音图文作品泽轩604vibe-coding大赏migo`.
   - Taskpool health projection: `active=11`, `blocked=11`, `ready=0`, `stale_leases=0`.
   - Projection governance: `flow_blocked_backlog`, `projection_drift_count=10`, `stale_projection_tasks=10`.
   - Source drift: branch ahead 105, dirty 19.

6. Focused tests:
   - Command: `.venv/bin/python -m pytest -q tests/hermes_cli/test_control_tower_snapshot.py tests/plugins/test_control_tower_dashboard_plugin.py tests/scripts/test_workflow_sequence_map.py tests/scripts/test_workflow_publication_governance_terminal_summary.py`
   - Result: `99 passed in 4.14s`

7. Git status read-back:
   - Existing dirty/ahead state observed, including 19 dirty/untracked entries. This slice added this report only; it did not push, deploy, publish, or mutate scheduler/skill/mission budget settings.

## Classification

| Item | Classification | Reason |
| --- | --- | --- |
| Workflow-kernel publication governance ladder through Phase 92 | complete for that ladder | Sequence map has `next_ladder=[]`; terminal summary is ready and all apply/publication/unattended flags remain disabled. |
| Real publication/apply/reopen | terminal fail-closed | Requires explicit human exact target, payload, rollback, and authorization. No automatic continuation is safe. |
| Whole Control Tower long-term stable plan | not complete | Canonical target model spans Phase A-G; live snapshot currently reports projection drift and stale taskpool/Kanban projection. |
| First-screen operator overview surface | implemented and test-covered, but live degraded | Snapshot includes `operator_overview`; focused tests pass; live status is `projection_drift`, not green. |
| Mission/taskpool/Kanban projection health | degraded-not-blocked | Runtime reports dispatchable work while Control Tower/Kanban projection shows 11 blocked and 10 stale projection drift entries. Next action is read-only drift inspection/reconciliation through governed Brain→Kanban path. |
| Human inbox / open decisions | clear for this slice | Live snapshot shows `pending_count=0`; no new value/permission decision is needed to inspect drift read-only. |
| Source drift | degraded-not-blocked | Branch is ahead and dirty; do not mix broad changes without scoped verification. |

## Outcome

This slice prevents a false green conclusion: Phase 92 closes the publication-governance ladder only. The broader Hermes Control Tower plan remains active because live evidence shows projection drift between canonical taskpool runtime and Control Tower/Kanban projections.

No Human Intervention Packet is needed yet because the next bounded step is read-only/local: inspect taskpool projection drift and prepare a governed reconciliation plan. If actual adapter mutation, Kanban writes, external sends, publication, cron changes, deletion, or budget/cadence changes are required, stop and create a mission handoff first.

## Recommended next bounded step

Run a read-only projection-drift evidence bundle:
- capture `brain_task_orchestrator next --json` or equivalent canonical taskpool runtime read-back;
- capture Kanban/taskpool projection rows for the 10 stale task IDs reported by Control Tower;
- classify whether each drift can be corrected by existing governed Brain→Kanban recovery or needs a human gate;
- do not mutate Kanban/taskpool/cron until the exact write intent and rollback/read-back semantics are known.
