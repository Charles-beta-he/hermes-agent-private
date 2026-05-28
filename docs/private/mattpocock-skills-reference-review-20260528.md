# ReferenceReview: mattpocock/skills → Hermes real-engineering workflow absorption

- Source repo: https://github.com/mattpocock/skills
- Local checkout: `/Users/charles/.hermes/runs/mattpocock-skills-reference-review`
- Commit reviewed: `0288510dd61ff6ef7c2003834082ab8f2387e80e`
- Review date: 2026-05-28
- License observed: `LICENSE` in source repo; transferred content below is synthesized guidance, not a verbatim vendored copy.
- Hermes artifact created from this review: `skills/software-development/real-engineering-workflows/`

## Source evidence

### Repo contract

- `CLAUDE.md` defines bucket governance: `engineering/`, `productivity/`, and `misc/` are shipped skills; `personal/`, `in-progress/`, and `deprecated/` are not promoted. Active skills must appear both in top-level `README.md` and `.claude-plugin/plugin.json`.
- `README.md` positions the package as small, composable skills for real engineering rather than agent-owned frameworks. It names four failure modes: misalignment, verbosity/domain-language drift, weak feedback loops, and architecture entropy.
- `.claude-plugin/plugin.json` exposes 14 active Claude plugin skills: 10 engineering + 4 productivity.
- Active shipped skills under `skills/misc/` add 4 optional operational utilities.

### Active skill inventory

Engineering:
- `diagnose` — reproduce/minimise/hypothesise/instrument/fix/regression-test; first build a fast feedback loop.
- `grill-with-docs` — challenge a plan against domain language and ADRs; update `CONTEXT.md` and ADRs as decisions crystallise.
- `improve-codebase-architecture` — find deepening opportunities using module/interface/implementation/depth/locality/deletion-test vocabulary.
- `prototype` — branch between logic/state terminal prototypes and UI variation prototypes.
- `setup-matt-pocock-skills` — scaffold per-repo issue tracker, triage labels, and domain-doc layout.
- `tdd` — red-green-refactor, behaviour-first tests, tracer bullets, avoid horizontal slices.
- `to-issues` — break a plan into independently grabbable vertical-slice issues with dependency/HITL/AFK metadata.
- `to-prd` — synthesize current context into a PRD and publish it to the issue tracker.
- `triage` — state-machine issue triage with agent briefs and out-of-scope knowledge capture.
- `zoom-out` — ask for higher-level code/system context before local changes.

Productivity:
- `caveman` — ultra-compressed, no-filler communication mode with an auto-clarity exception.
- `grill-me` — relentless interview until shared understanding is reached.
- `handoff` — compact current context into a continuation document.
- `write-a-skill` — create skills with progressive disclosure and bundled resources.

Misc:
- `git-guardrails-claude-code` — block destructive git commands in Claude Code hooks.
- `migrate-to-shoehorn` — TypeScript test-data helper migration.
- `scaffold-exercises` — course exercise scaffolding conventions.
- `setup-pre-commit` — Husky/lint-staged/typecheck/test pre-commit setup.

## Transferable patterns for Hermes

### 1. Setup pointer only for hard dependencies

The source repo avoids forcing setup for every skill; setup is explicitly required only when a skill needs repo-specific config such as issue tracker labels or domain-doc paths. Hermes should preserve this: do not add setup friction to general skills; require setup only when a workflow depends on durable local configuration.

### 2. Misalignment is a first-class engineering failure

The `grill-*` skills make alignment an explicit loop before implementation. Hermes already has goal calibration; this review adds a concrete engineering variant: challenge plans against repo language, existing architecture decisions, and realistic scenarios before writing code.

### 3. Domain language reduces token and architecture drift

`CONTEXT.md` is used as a shared language document, not generic notes. Hermes should treat project/domain glossary as an execution primitive: names in code, PRDs, issues, and summaries should converge on the same domain terms.

### 4. Feedback-loop quality is the speed limit

`diagnose` starts by building and improving a fast, sharp, deterministic loop before fixing. Hermes should make loop quality explicit in debugging and implementation plans: reproduce the user-described symptom, minimise scope, instrument, then fix and regression-test.

### 5. Vertical slices beat horizontal scaffolding

`tdd` and `to-issues` warn against horizontal slices. Hermes taskpool/issue generation should prefer thin, independently verifiable slices crossing schema/API/UI/tests over bulk layer-by-layer scaffolding.

### 6. Architecture review should use deep-module vocabulary

`improve-codebase-architecture` gives a compact vocabulary: module, interface, implementation, depth, seam, adapter, locality, deletion test, and “one adapter = hypothetical seam; two adapters = real seam.” This is immediately useful for Hermes code-review and refactor planning.

### 7. Rejected work should leave reusable negative knowledge

`triage` captures out-of-scope items instead of forgetting them. Hermes should use project docs or taskpool evidence to prevent repeatedly proposing rejected approaches.

### 8. Compression has an exception for ambiguity

`caveman` is useful only when meaning is clear; it has an auto-clarity exception. Hermes should keep concise output as a mode, not a license to skip calibration when ambiguity is load-bearing.

## Non-transferable / do not cargo-cult

- Claude Code hook wiring is host-specific; do not import it into Hermes unchanged.
- The source repo’s setup skill assumes a Claude plugin/slash-command UX; Hermes should expose the patterns via skills and project preflight rather than copying command names.
- `migrate-to-shoehorn` and course `scaffold-exercises` are narrowly tied to Matt’s TypeScript/course setup; keep as reference only unless the user works in that stack.
- Do not duplicate Hermes skills wholesale. Prefer a synthesized skill that maps patterns into Hermes taskpool, evidence, project profile, and verification conventions.

## Hermes absorption decision

Implemented as a low-risk built-in skill:

- `skills/software-development/real-engineering-workflows/SKILL.md`
- `skills/software-development/real-engineering-workflows/references/mattpocock-skills-map.md`

The skill is a routing/workflow synthesis, not a copied vendor package. It is intended to load when a user asks for Matt Pocock skills, real-engineering workflows, plan grilling, vertical slicing, domain-language alignment, or architecture deepening.

## Verification plan

- Validate new skill frontmatter and linked reference files.
- Read back the created skill files.
- Run `python3 -m py_compile` only if scripts are added; none are added in this change.
- Commit only the new review/skill files, leaving pre-existing Hermes worktree modifications untouched.
