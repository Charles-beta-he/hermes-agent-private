# ReferenceReview: Claude Code Harness → Hermes taskpool evidence loop

- Source repo: https://github.com/Chachamaru127/claude-code-harness
- Local clone: `/Users/charles/.hermes/runs/claude-code-harness-reference-review-20260601`
- Commit reviewed: `2980653354c3f91c1bddfe53476d6fa3f93d5da5`
- Taskpool task: `2026-06-01-评估-claude-code-harness将-ai-编程聊天转为可验证交付流程`
- Safety boundary: read-only/reference review only; no third-party plugin install, no Harness binary execution as a trusted integration, no lifecycle-gate relaxation.

## Live source evidence read

- `README.md:28-39` states the core problem and loop: raw agent work drifts; Harness turns it into write spec/plan → implement approved slice → verify → independently review → package PR/release evidence.
- `README.md:81-97` names the small verb surface: `/harness-plan`, `/harness-work`, `/harness-review`, `/harness-release`; unobserved data must stay `unknown` rather than be invented.
- `README.md:100-108` lists command responsibilities including setup/migration report.
- `README.md:112-119` defines stage outputs/gates: investigate evidence/unknowns, plan `spec.md` + `Plans.md`, work code/tests, review independent verdict, PR evidence pack, release preflight.
- `README.md:140-147` explicitly forbids inheriting support claims from other projects and says `not_observed != absent`.
- `IMPLEMENTATION_GUIDE.md:6-21` says the current architecture is Go-first: `bin/harness`, `go/`, plugin manifests/hooks, skills, templates, tests/validators.
- `IMPLEMENTATION_GUIDE.md:75-93` gives validation surfaces: plugin validation, skill validation, consistency, template registry, Go build/test/vet, and shell-test sweep.
- `spec.md:44-57` defines SSOT precedence: `spec.md` product contract > sub-specs > `Plans.md` task ledger > runtime evidence; task completion cannot contradict applicable spec.
- `spec.md:59-121` requires `/harness-plan` to produce both spec delta/skip reason and task contract, with non-trivial planning validated by product/architecture/security/QA/skeptic perspectives.
- `CLAUDE.md:73-86` and `CLAUDE.md:90-97` show development flow and anti-tampering/version boundaries.
- `skills/harness-plan/SKILL.md:49-87` encodes planning-quality gates and non-trivial plan perspectives.
- `skills/harness-work/SKILL.md:44-63` selects solo/parallel/breezing by task count and makes Codex opt-in only.
- `skills/harness-review/SKILL.md:41-50` keeps review read-only by default; approval does not auto-commit/release.
- `skills/harness-setup/SKILL.md:65-90` treats setup as baseline generation + binary/plugin/doctor checks.

## Mapping: Harness five-step loop → Hermes taskpool/control plane

| Harness surface | Harness contract | Hermes analog today | Gap / transferable pattern |
|---|---|---|---|
| Setup | Establish a known baseline (`harness version`, sync, doctor) before work. | `post-update-check.py --fix`, `selfcheck`, `proof-check`, project profiles, brain overlays. | Add a taskpool preflight evidence bundle that records repo/profile/brain/proof baseline before claiming implementation tasks. Keep it read-only/default-safe. |
| Plan | `spec.md` + `Plans.md`, explicit unknowns, non-trivial validation perspectives. | Task note frontmatter/body, mission envelope, `acceptance_criteria`, project profiles, multimodel/advisory gates. | Add a generated `task_contract_snapshot` / `spec_delta_or_skip_reason` section to handoff artifacts so workers cannot silently flatten product contract into a vague task summary. |
| Work | Execute only approved slice, task-count based topology, backend role boundaries. | `claim → running`, `parallel_boundary`, agent-team workers, dynamic N, authority tiers. | Borrow the role-scoped backend idea: worker execution backend can vary, but reviewer/arbiter authority must remain independent and recorded in evidence. |
| Review | Independent review, read-only by default, major findings block acceptance. | `review` lifecycle, proof/lifecycle gates, multimodel/advisory review gaps. | Preserve Hermes stronger rule: advisory/model PASS is not delivery proof. Add a first-class review-gap artifact for tasks missing feature-specific verification. |
| Release / closeout | Evidence pack, PR/release readiness distinct from release completeness. | `review → done`, structured evidence, proof hashes, dashboard/control center. | Make closeout evidence pack explicit: implementation artifact + feature-specific verification artifact + read-back/regression. Do not accept py_compile/selfcheck alone for implementation work. |

## Minimum reversible integration slice

**Slice name:** `taskpool-harness-style-closeout-pack-v0`.

**Intent:** Improve Hermes taskpool handoff/closeout ergonomics without adopting or installing Claude Code Harness.

**Scope:**
1. Add a read-only artifact generator (script or orchestrator subcommand) that emits a Markdown closeout pack for one task:
   - task id/title/status/lane/project;
   - source contract snapshot (`objective`, `acceptance_criteria`, `red_lines`, `non_goals`, `evidence_refs`);
   - implementation artifact slots;
   - feature-specific verification slots;
   - review-gap section if required evidence is absent;
   - `spec_delta_or_skip_reason` / `profile_contract_ref` when project profile exists.
2. Wire it only as optional evidence in review/handoff paths; no lifecycle gate weakening.
3. Add focused tests that a work/implementation task cannot be marked done from this pack unless it contains both implementation and feature-specific verification evidence.

**Rollback:** remove the optional generator and references; no task lifecycle semantics change.

**Validation plan for actual implementation:**
- Unit test for generated pack contents from a fixture task.
- Lifecycle test that `py_compile` + selfcheck without feature-specific verification still fails done transition for implementation-class/manual-review-final tasks.
- Dry-run/read-back on one real task note showing pack path recorded as `artifact:` evidence while status remains review/pending until closeout criteria are met.

## Rejections / non-adoptions

- Do not install `/plugin marketplace add Chachamaru127/claude-code-harness` into Hermes as part of this intake.
- Do not replace Hermes taskpool statuses with `Plans.md` markers; Hermes taskpool remains SSOT.
- Do not treat Harness support tiers for Codex/OpenCode/Cursor as proof of Hermes provider/runtime health.
- Do not make release automation auto-commit/push from a cron follow-through executor.
- Do not relax multimodel/proof/lifecycle gates to imitate Harness convenience surfaces.

## Decision

Adopt the **evidence-pack and SSOT-precedence pattern**, not the external runtime. The next implementation task, if created later, should be a narrow optional closeout-pack generator plus tests, not a wholesale Harness workflow import.
