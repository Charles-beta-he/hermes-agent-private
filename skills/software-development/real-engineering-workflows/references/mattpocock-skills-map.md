# Matt Pocock Skills → Hermes Transfer Map

Source repo: https://github.com/mattpocock/skills  
Reviewed commit: `0288510dd61ff6ef7c2003834082ab8f2387e80e`

## Active source skills and Hermes mapping

### `diagnose`

Transfer:
- Start by building the feedback loop.
- Improve the loop before fixing: faster, sharper, more deterministic.
- Reproduce the user's reported symptom, not a nearby failure.
- Use reproduce → minimise → hypothesise → instrument → fix → regression-test.

Hermes mapping:
- `systematic-debugging`
- `incident-response` for production incidents
- taskpool proof logs for reproduction/fix/regression evidence

### `grill-with-docs` / `grill-me`

Transfer:
- Misalignment is a primary engineering failure mode.
- Interrogate only the branches that change the work.
- Challenge fuzzy terms against code and docs.
- Update domain language and ADRs inline when decisions become durable.

Hermes mapping:
- runtime goal-calibration norm
- `planning-with-files`
- `documentation-and-adrs`
- project profile / Workpad boot context

### `improve-codebase-architecture`

Transfer:
- Review architecture using module/interface/implementation/depth/locality/seam/adapter vocabulary.
- Prefer deep modules: small interfaces hiding meaningful implementation.
- Apply the deletion test to distinguish leverage from pass-through abstractions.
- Treat one adapter as a hypothetical seam and two adapters as a real seam.
- Present candidates with files, problem, solution, benefits, before/after model, and recommendation strength.

Hermes mapping:
- `codebase-intelligence`
- `api-and-interface-design`
- `code-simplification`
- review artifacts under `docs/private/` or project docs for durable decisions

### `prototype`

Transfer:
- Choose prototype mode by uncertainty.
- Logic/state uncertainty: runnable terminal/script prototype.
- UI/product uncertainty: several distinct variants switchable in one route.
- Treat prototypes as disposable unless explicitly promoted.

Hermes mapping:
- `spike`
- `claude-design` / UI skills when visual exploration is needed
- project-specific browser-harness validation after promotion

### `setup-matt-pocock-skills`

Transfer:
- Hard-dependency setup belongs in one explicit setup skill, not scattered across every workflow.
- Record issue tracker, triage labels, and domain-doc layout once.

Hermes mapping:
- project profiles and Workpad boot context
- `project-preflight-profile-workpad` norm
- `docs/agents/`-style files may be useful inside projects, but Hermes should not require them globally.

### `tdd`

Transfer:
- Red-green-refactor.
- Behaviour-first tests over implementation-shape tests.
- Avoid horizontal slices.
- Use tracer bullets and vertical increments.
- Consider interface design and deep modules before locking tests.

Hermes mapping:
- `test-driven-development`
- `incremental-implementation`
- user preference: do not lose functionality or reduce precision; use real backend/data where required.

### `to-issues`

Transfer:
- Break plans into independently grabbable vertical slices.
- Quiz dependency order and AFK/HITL boundary.
- Each issue needs parent, what to build, acceptance criteria, and blocked-by.

Hermes mapping:
- taskpool add fields: `dependency_order`, `acceptance_evidence`, `red_line`, `non_goal`, `evidence_ref`, `priority_layer`
- user preference: tasks入池带顺序/依赖

### `to-prd`

Transfer:
- Convert current context into a PRD without re-interviewing when enough context exists.
- Include problem statement, solution, user stories, implementation decisions, testing decisions, out-of-scope, further notes.

Hermes mapping:
- `writing-plans`
- `planning-with-files`
- project Workpad / taskpool SSOT

### `triage`

Transfer:
- Triage as a state machine.
- Distinguish `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`.
- Ready-for-agent needs an agent brief, not just a label.
- Wontfix/out-of-scope should leave reusable negative knowledge.

Hermes mapping:
- taskpool lifecycle states and proof gates
- `taskpool-control-plane`
- project docs for durable out-of-scope decisions

### `zoom-out`

Transfer:
- Before changing unfamiliar code, ask for or produce a broader system-context explanation.

Hermes mapping:
- `codebase-intelligence`
- project preflight norm

### `caveman`

Transfer:
- Compact output can dramatically reduce token use.
- Keep evidence nouns and exact identifiers; drop filler.
- Auto-clarity exception: if ambiguity matters, stop being terse and resolve it.

Hermes mapping:
- concise Telegram summaries
- user preference for evidence-first, no fake completion
- token-saving skills without reducing correctness

### `handoff`

Transfer:
- Handoff must be action-ready: goal, state, artifacts, decisions, commands, blockers, next step.

Hermes mapping:
- `planning-with-files`
- continuation packet / taskpool handoff
- session summaries after long-running work

### `write-a-skill`

Transfer:
- Progressive disclosure: core skill plus references/scripts/templates only when needed.
- Skill descriptions are trigger surfaces.
- Bundle deterministic helper scripts when repeatable.

Hermes mapping:
- `hermes-agent-skill-authoring`
- `skill-eval`

### Misc skills

- `git-guardrails-claude-code`: concept transfers as destructive-command guardrails; mechanics are Claude Code-specific.
- `setup-pre-commit`: generally useful, but Hermes already uses project-specific validation; apply only per project.
- `migrate-to-shoehorn`: stack-specific; do not absorb generally.
- `scaffold-exercises`: useful for course projects only.

## Absorption rule

For future external skill packages, follow this pattern:

1. Inspect repo instructions, active skill inventory, plugin/manifest, and docs.
2. Separate active/shipped skills from personal/in-progress/deprecated material.
3. Extract transferable control-flow patterns, not host-specific slash commands.
4. Map each pattern to existing Hermes skills/norms/taskpool fields.
5. Create one synthesized Hermes skill or patch existing skills; avoid a flood of narrow duplicates.
6. Write a reference review SSOT before landing changes.
