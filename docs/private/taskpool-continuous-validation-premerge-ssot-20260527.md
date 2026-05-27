# Hermes Taskpool Continuous Validation + Pre-merge Layer SSOT

- Task: `2026-05-20-p1-吸收-ai-编码后置-ci-cd-向内嵌连续验证与预合并层迁移的架构范式`
- Created: 2026-05-27T21:35:14+08:00
- Scope: turn the user's AI-coding/CI-CD observation into a Hermes taskpool mainline architecture, not an immediate broad code rewrite.
- Status of this tranche: architecture SSOT + live control-plane read-back + deterministic verification. It is sufficient to move the task to `review`, not to `done`, because no production implementation patch has landed for the pre-merge layer yet.

## Source evidence read during this tranche

1. Task note: `/Users/charles/Documents/brain/05-实践与项目/tasks/2026-05-20-p1-吸收-ai-编码后置-ci-cd-向内嵌连续验证与预合并层迁移的架构范式.md`
   - Frontmatter requires `acceptance_evidence`: current Hermes verification gap map, pre-merge/continuous-validation state machine, conflict/rollback boundaries, staged landing order, and selfcheck/test evidence.
   - It also carries `manual_review_final_landed: false`, so final `done` needs a real implementation artifact plus feature-specific verification, not advisory/py_compile/hash-only evidence.
2. Current final-landed guard: `/Users/charles/.hermes/scripts/brain_task_orchestrator.py:3583-3693`
   - `task_requires_final_landed_evidence(...)` detects reopened/manual-review tasks.
   - `_final_landed_evidence_classes(...)` rejects weak-only proof/advisory evidence and requires both implementation and verification classes.
   - `completion_gate_failures(...)` keeps evolution tasks on `ratification-or-selfcheck-review-done` and adds the final-landed bundle requirement when applicable.
3. Current proof-check surface: `/Users/charles/.hermes/scripts/brain_task_orchestrator.py:5654-5690`
   - Checks terminal proof/evidence debt, structured evidence, proof hashes, and Proof Log / Final Handoff sections.
4. Current selfcheck surface: `/Users/charles/.hermes/scripts/brain_task_orchestrator.py:8741-8798`
   - Validates paths, lifecycle graph, claim-bound status fields, proof chain, boundary check, dashboard warnings, and semantic-control-plane soft warnings.
5. Regression coverage for the recently landed guard: `/Users/charles/.hermes/scripts/test_brain_task_orchestrator.py:1560-1640`
   - Verifies manual-review false-final-landed tasks do not close from weak `command:selfcheck PASS` / `py_compile`-like evidence alone.

## Current Hermes verification chain gap map

| Stage | Current evidence surface | Gap exposed by this task | Required pre-merge improvement |
|---|---|---|---|
| Intake / dispatch | `next --reconcile`, `dispatch-dry-run`, `eligibility-check`, task frontmatter fields | Dispatchability proves a task can start, not that the promised product behavior exists. | Persist a task-local `verification_contract` snapshot before claim, including feature-specific probes and forbidden weak-only closeout classes. |
| Execution | `transition pending -> claimed -> running`, worker/handoff artifacts | A worker can produce generic proof (`py_compile_taskpool`, hashes) that satisfies generic control-plane health while missing task-specific acceptance evidence. | During `running`, require worker output to attach acceptance-specific proof slots derived from `acceptance_evidence`, `capability_delta`, and manual-review constraints. |
| Review | `completion_gate_failures`, `auto-close-review` | Review-close historically over-trusted advisory and static proof. The final-landed gate now mitigates the known false-positive path but remains closeout-time only. | Add a pre-review verifier that classifies evidence before `review`, so insufficient implementation/verification is caught while the task is still running/retryable. |
| Done | `proof-check`, proof hashes, task evidence list | `done` is auditable after the fact, but false-positive done can still require retrospective reopen if the specific feature path was never tested. | Introduce a pre-merge ledger row per task that stores `implementation_artifact`, `feature_verification`, `rollback_boundary`, and `read_back_command`. |
| Dashboard / health | `selfcheck`, `boundary-check`, dashboards | Global PASS/WARN does not equal feature delivery for a specific task. | Dashboard should display per-task closeout bundle completeness separately from global taskpool health. |

## Proposed continuous-validation / pre-merge state machine

```text
pending
  -> claimed
    - snapshot task SSOT: objective, acceptance_evidence, red lines, manual-review fields
    - compute required proof slots
  -> running
    - worker may edit code/docs/scripts/config only within declared surfaces
    - after each bounded tranche, run task-specific verifier, not just generic py_compile/selfcheck
  -> pre_review_validation
    - classify evidence into implementation_artifact / feature_specific_verification / governance_health / advisory_only
    - fail closed to retry if required classes are missing
  -> review
    - human/model review sees the same classified bundle, not only raw evidence strings
  -> done
    - allowed only when lifecycle gate + final-landed gate + task-specific proof slots are complete
  -> rollback/retry
    - if conflict group, dirty worktree, or read-back verification fails, transition to retry/blocked with explicit evidence
```

Implementation note: `pre_review_validation` can initially be a derived/virtual phase inside `transition(... --to review)` or `auto-close-review`, rather than a new public lifecycle status. That avoids destabilizing the existing lifecycle graph while still making the gate visible.

## Conflict and rollback governance boundary

- Conflict group: this task maps to `hermes-taskpool` / `taskpool` surfaces. It must not be merged together with unrelated prompt-builder, gateway, or transport changes already present in the checkout.
- Rollback unit: one scoped commit or artifact bundle per task. If the task only produces a design SSOT, the rollback is deleting/reverting this artifact and its task evidence, not changing runtime code.
- Dirty worktree rule: before any runtime code implementation of the pre-merge layer, inspect `git status --short --branch` and stage only files touched by the task. Existing unrelated modifications must remain untouched.
- Evidence classes:
  - Implementation artifact: code/config/script/skill/docs artifact that directly embodies the task's promised deliverable.
  - Feature-specific verification: focused pytest/vitest/script/read-back/dry-run proving the deliverable's promised behavior.
  - Governance health: `selfcheck`, `proof-check`, proof hashes; required but insufficient alone.
  - Advisory-only: multimodel discussion reports, recommendations, or architectural prose without live path verification; insufficient for final `done`.

## Staged landing order

1. **Done in this tranche:** create this SSOT artifact and verify it by read-back. Move the task to `review`, not `done`.
2. Add a narrow classifier helper/test around `completion_gate_failures(...)` that exposes the evidence classes in machine-readable JSON for review/dashboards.
3. Wire the classifier into `transition --to review` or `auto-close-review` as WARN-only for ordinary tasks and fail-closed for `manual_review_final_landed: false` tasks.
4. Add dashboard/control-center display for missing per-task proof slots.
5. Only after tests pass, consider closing this task to `done` with a scoped code commit and feature-specific regression output.

## Verification commands for this tranche

These commands are the bounded proof for the artifact tranche:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path('/Users/charles/.hermes/hermes-agent/docs/private/taskpool-continuous-validation-premerge-ssot-20260527.md')
text = p.read_text(encoding='utf-8')
required = [
    'Current Hermes verification chain gap map',
    'Proposed continuous-validation / pre-merge state machine',
    'Conflict and rollback governance boundary',
    'Staged landing order',
    'manual_review_final_landed: false',
    'pre_review_validation',
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit({'status': 'FAIL', 'missing': missing})
print({'status': 'PASS', 'path': str(p), 'required_sections': len(required)})
PY
python3 ~/.hermes/scripts/brain_task_orchestrator.py selfcheck --json
python3 ~/.hermes/scripts/brain_task_orchestrator.py proof-check --json
python3 ~/.hermes/scripts/brain_task_orchestrator.py next --reconcile --json
```

## Closeout decision for this run

Move to `review` with evidence:

- `file:/Users/charles/.hermes/hermes-agent/docs/private/taskpool-continuous-validation-premerge-ssot-20260527.md`
- `command:artifact_readback_premerge_ssot PASS`
- `command:selfcheck PASS`
- `command:proof-check PASS`

Do **not** move to `done` in this run: the artifact is an architecture SSOT and planning deliverable. The task's `manual_review_final_landed: false` history means final closeout still requires a runtime implementation patch or explicit approved partial closeout plus feature-specific verification.
