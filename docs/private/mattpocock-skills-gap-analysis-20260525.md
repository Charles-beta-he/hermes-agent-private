# mattpocock/skills Hermes Gap Analysis Intake — 2026-05-25

## Source evidence

- Source: https://github.com/mattpocock/skills
- Repo: `mattpocock/skills`
- Observed metadata via GitHub API on 2026-05-25:
  - Description: "Skills for Real Engineers. Straight from my .claude directory."
  - Stars: 104057
  - Forks: 9204
  - License: MIT
  - Default branch: `main`
  - Primary language: Shell
  - Last pushed: 2026-05-20T08:46:54Z
- Top-level layout: `.claude-plugin/`, `.out-of-scope/`, `CLAUDE.md`, `CONTEXT.md`, `docs/`, `scripts/`, `skills/`.
- Skill buckets: `engineering/`, `productivity/`, `misc/`, plus non-promoted `personal/`, `in-progress/`, `deprecated/`.

## Material shape

The repo is a public agent-skill bundle focused on practical engineering workflows rather than a runtime framework. README framing emphasizes small, composable, model-agnostic skills that avoid process ownership and address common agent failure modes:

- misalignment before code work;
- verbosity / lack of project shared language;
- missing feedback loops for broken code;
- engineering workflows such as diagnose, TDD, architecture improvement, issue/PRD conversion, and triage.

Representative inspected skills:

- `engineering/diagnose`: reproduce → minimize → hypothesize → instrument → fix → regression-test; strongly emphasizes building a fast deterministic feedback loop.
- `engineering/tdd`: vertical red-green-refactor / tracer-bullet loop; behavior-through-public-interface tests; avoids horizontal bulk test writing.
- `engineering/improve-codebase-architecture`: module/interface/depth/seam vocabulary; seeks deepening opportunities for testability and AI navigability.
- `engineering/triage`: issue state machine and label mapping.
- `productivity/write-a-skill`: skill structure and progressive disclosure conventions.

## Hermes landing-zone decision

- Lane: `evolution`.
- Project: `hermes-agent`.
- Architecture target: `skill-graph`.
- Priority layer: `P2`.

Rationale: the repo is high-signal and directly relevant to Hermes skill quality, bug-diagnosis discipline, TDD, architecture review, and triage workflows, but it is not an immediate production blocker or safety regression. The right action is governed absorption / prototype planning, not urgent runtime modification.

## Multi-model triage

A real T1 fan-out was run with `gpt-5.5`, `mimo-v2.5-pro`, and `deepseek-v4-pro`.

- Artifact: `/Users/charles/.hermes/tick-20260525-065259-975462-p1907-637d4ee5-01181e6c/`
- Fan-out certification: quorum met, 3/3 planners succeeded.
- Consensus: weak but directionally aligned.
- Shared recommendation: create a governed P2 skill-graph/local-brain absorption task; do not auto-install or execute third-party skills; `requires_multimodel=false` for the task itself because this is intake/gap-analysis work, not certification.
- Noted disagreement: DeepSeek suggested P1, while GPT-5.5/MiMo recommended P2. Final decision follows P2 because there is no active blocker and the work is high-value but not urgent.

## Capability delta

Hermes should evaluate whether Matt Pocock's skill patterns can strengthen:

1. bug diagnosis loops that force a deterministic feedback signal before repair;
2. TDD / vertical tracer-bullet implementation discipline;
3. shared-language / domain glossary usage to reduce verbosity and misalignment;
4. architecture-improvement vocabulary around modules, interfaces, seams, and depth;
5. issue/PRD/triage workflows with explicit tracker state machines;
6. skill authoring conventions and progressive disclosure.

## Anti-drift constraints

- Do not run `npx skills@latest` or install the third-party skill bundle during intake.
- Do not copy wholesale into Hermes skills.
- Do not create a long flat list of imported narrow skills.
- Preserve provenance for every accepted pattern.
- Prefer patching existing Hermes umbrella skills/references before creating new top-level skills.
- Treat popularity / star count as discovery signal only, not quality proof.
- Keep the output as Hermes gap mapping and selective migration/prototype decisions, not a generic repo summary.

## Recommended taskpool metadata

- Title: `Gap-analyze mattpocock/skills for Hermes skill-graph absorption`
- Objective: read-only provenance review, overlap/gap matrix against existing Hermes skills, and selective migration/prototype plan for accepted workflow deltas.
- Dependency order:
  1. verify repo metadata and representative skill files read-only;
  2. inventory overlap with existing Hermes skills;
  3. produce migration matrix with accept/reject/watch decisions;
  4. prototype or patch only accepted deltas with provenance;
  5. verify with skill lint/selfcheck/proof-check and focused regression where code changes occur.
- Parallel boundary: repo evidence collection and local Hermes skill inventory may run in parallel; migration/prototype design waits for both; no third-party install/execution during intake.
- Acceptance evidence: provenance doc, overlap/gap matrix, migration decisions, candidate follow-up tasks/patches, verification commands, and updated taskpool proof log.
