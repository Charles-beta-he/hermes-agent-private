# mattpocock/skills Hermes Gap Analysis Intake — 2026-05-25

## Source evidence

- Source: https://github.com/mattpocock/skills
- Repo: `mattpocock/skills`
- Observed metadata via GitHub API on 2026-05-25 and refreshed by follow-through at 2026-05-25T16:54:35+08:00:
  - Description: "Skills for Real Engineers. Straight from my .claude directory."
  - Stars: 104283
  - Forks: 9226
  - License: MIT
  - Default branch: `main`
  - Primary language: Shell
  - Last pushed: 2026-05-20T08:46:54Z
- Top-level layout observed from GitHub contents API: `.claude-plugin/`, `.out-of-scope/`, `CLAUDE.md`, `CONTEXT.md`, `LICENSE`, `README.md`, `docs/`, `scripts/`, `skills/`.
- Skill buckets observed read-only: `skills/engineering/{diagnose,grill-with-docs,improve-codebase-architecture,prototype,setup-matt-pocock-skills,tdd,to-issues,to-prd,triage,zoom-out}`, `skills/productivity/{caveman,grill-me,handoff,write-a-skill}`.

## Material shape

The repo is a public agent-skill bundle focused on practical engineering workflows rather than a runtime framework. README / skill framing emphasizes small, composable, model-agnostic skills that avoid process ownership and address common agent failure modes:

- misalignment before code work;
- verbosity / lack of project shared language;
- missing feedback loops for broken code;
- engineering workflows such as diagnose, TDD, architecture improvement, issue/PRD conversion, and triage.

Representative inspected skills, all read-only through GitHub raw/API, with no install and no execution:

- `engineering/diagnose`: reproduce/minimise/hypothesise/instrument/fix/regression-test; strongest portable pattern is forcing a fast deterministic feedback loop before code edits. It explicitly lists failing tests, curl/HTTP scripts, CLI fixtures, headless browser scripts, trace replay, throwaway harnesses, fuzz loops, bisection, and differential loops as ways to build the signal.
- `engineering/tdd`: behavior-through-public-interface tests, vertical red/green/refactor, and an explicit anti-pattern against horizontal “write all tests then all code” slicing.
- `engineering/improve-codebase-architecture`: shared vocabulary around module/interface/implementation/depth/seam/adapter/leverage/locality plus deletion-test and “interface is the test surface” heuristics.
- `engineering/triage`: issue-tracker state machine, AI-generated triage disclaimer, roles, and durable agent-brief references.
- `productivity/write-a-skill`: concise SKILL.md, progressive disclosure into bundled references/scripts, and review cycle.

## Hermes landing-zone decision

- Lane: `evolution`.
- Project: `hermes-agent`.
- Architecture target: `skill-graph`.
- Priority layer: `P2`.

Rationale: the repo is high-signal and directly relevant to Hermes skill quality, bug-diagnosis discipline, TDD, architecture review, and triage workflows, but it is not an immediate production blocker or safety regression. The right action is governed absorption / prototype planning, not urgent runtime modification.

## Local Hermes overlap inventory

Read-back evidence from current Hermes tree:

- `skills/software-development/systematic-debugging/SKILL.md`: already enforces root-cause investigation, reproduction, recent-change review, instrumentation, data-flow tracing, pattern analysis, single-hypothesis testing, regression test, and fix verification.
- `skills/software-development/test-driven-development/SKILL.md`: already enforces RED/GREEN/REFACTOR, test-first behavior, one behavior per test, real code over mocks, and undo-on-refactor-failure discipline.
- `skills/software-development/hermes-agent-skill-authoring/SKILL.md`: already covers in-repo skill frontmatter, validator constraints, peer-matched structure, size limits, references/templates/scripts placement, and verification checklist.
- `skills/autonomous-ai-agents/hermes-agent/SKILL.md`: already covers Hermes operation, gateway/profile/cron/plugins, update/selfcheck pitfalls, and taskpool-specific references.

## Overlap / gap matrix

| External pattern | Hermes current coverage | Gap | Decision |
|---|---|---|---|
| Diagnose feedback loop first | `systematic-debugging` requires reproduction/instrumentation/root cause but is less explicit about creative deterministic signal construction. | Add a stronger “feedback loop menu” concept to existing debugging skill or reference; do not create a duplicate `diagnose` skill. | ACCEPT as patch to existing skill. |
| Vertical TDD / tracer bullet | `test-driven-development` is strong on RED/GREEN/REFACTOR and one behavior at a time. | It does not explicitly name “horizontal slices” as an anti-pattern or “tracer bullet” as the preferred vertical increment. | ACCEPT as small patch/reference to existing TDD skill. |
| Architecture vocabulary: module/interface/depth/seam/adapter | Hermes has planning and self-evolution references, but no compact software architecture vocabulary skill found in the inspected set. | Missing shared language for “deep module” analysis and AI-navigability; useful but should be adapted to Hermes terms. | WATCH/PROTOTYPE: create/patch only after a separate scoped task with examples from Hermes code. |
| Triage issue state machine | Hermes taskpool has rich lifecycle governance; GitHub issue triage conventions are partly orthogonal. | Could help external repo issue grooming but not primary local taskpool lifecycle. | WATCH; do not absorb until a GitHub issue-triage need appears. |
| Skill authoring progressive disclosure | `hermes-agent-skill-authoring` already covers SKILL.md, references, scripts, size limits, validator constraints. | Could add a concise “progressive disclosure” label, but coverage is sufficient. | REJECT as standalone; optional wording patch only. |
| Domain glossary / CONTEXT.md usage | Hermes project context and AGENTS.md exist; taskpool skill has architecture targets and brain overlays. | Missing simple rule to check domain glossary/ADRs when present during architecture/debugging. | ACCEPT as light pitfall/checklist addition to debugging/architecture planning, not a new skill. |

## Selective migration / prototype matrix

| Candidate delta | Target Hermes surface | Bounded implementation shape | Closeout evidence required |
|---|---|---|---|
| Deterministic feedback-loop menu for hard bugs | `skills/software-development/systematic-debugging/SKILL.md` or supporting `references/feedback-loops.md` | Add a compact menu: failing test, curl script, CLI fixture, browser script, trace replay, harness, fuzz loop, bisect, differential loop. Preserve Hermes tool-use wording. | Read-back of patched skill/reference, skill frontmatter validation, focused skill text search, `selfcheck`, `proof-check`. |
| Horizontal-slice anti-pattern / vertical tracer-bullet TDD | `skills/software-development/test-driven-development/SKILL.md` | Add one section distinguishing vertical one-behavior cycles from bulk test/code phases. | Read-back of patched section, skill validator, maybe focused docs grep; no runtime tests unless code changed. |
| Deep-module architecture vocabulary | New or existing software-development architecture skill only after examples are mapped to Hermes modules such as `model_tools.py`, `run_agent.py`, taskpool orchestrator. | Prototype doc first; do not create a broad skill without local examples. | Architecture artifact with Hermes examples, no code mutation; later skill patch requires validation. |
| GitHub issue triage role/state machine | GitHub skills or taskpool/GitHub bridge only if a live workflow asks for it. | Leave as watch item; avoid mixing local taskpool lifecycle with GitHub issue labels. | Future task evidence with actual issue tracker target. |
| Skill authoring progressive disclosure | `hermes-agent-skill-authoring` wording only | Optional one-paragraph polish; current coverage sufficient. | Read-back only if patched. |

## Bounded follow-through decision

This run completed the governed intake/gap-analysis closeout evidence, not the downstream implementation patches. The next safe work item, if selected later, is a small patch to existing Hermes skills rather than a new third-party import:

1. Patch `systematic-debugging` with deterministic feedback-loop menu and domain-glossary/ADR check.
2. Patch `test-driven-development` with horizontal-slice anti-pattern / vertical tracer-bullet wording.
3. Defer architecture-vocabulary prototype until a separate scoped task can ground it in Hermes code examples.

No third-party installation, `npx`, repository clone, or external skill execution was performed. External access was limited to GitHub API/raw read-only metadata and representative `SKILL.md` text.

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
