# Andrej Karpathy Skills → Hermes behavior/skill gap analysis

Source inspected: https://github.com/forrestchang/andrej-karpathy-skills
GitHub API canonical owner observed: `multica-ai/andrej-karpathy-skills` for the same repository surface.
Local clone: `/tmp/andrej-karpathy-skills`
Commit inspected: `2c60614` on `main`
Inspection date: 2026-05-21

## Correction note

The earlier OpenClaw awesome-list analysis was based on the initially supplied URL and should be treated as the wrong source for the video. The likely correct project is `forrestchang/andrej-karpathy-skills` / canonical GitHub API surface `multica-ai/andrej-karpathy-skills`, not `VoltAgent/awesome-openclaw-skills`.

Checked candidates:

- `yaochang008/andrej-karpathy-skills`: GitHub API returned 404.
- `yaochang008/tandrej-karpathy-skills`: GitHub API returned 404.
- `yaochang008/karpathy-skills`: GitHub API returned 404.
- GitHub repo search for `andrej karpathy skills github` returned `vtroisWhite/andrej-karpathy-skills` as a Chinese translation/fork and multiple references pointing to `forrestchang/andrej-karpathy-skills`.
- Direct GitHub API for `forrestchang/andrej-karpathy-skills` resolves to canonical `multica-ai/andrej-karpathy-skills`.

## Live source evidence

Repository contents inspected:

- `README.md`
- `README.zh.md`
- `CLAUDE.md`
- `CURSOR.md`
- `EXAMPLES.md`
- `skills/karpathy-guidelines/SKILL.md`
- `.cursor/rules/karpathy-guidelines.mdc`
- `.claude-plugin/*`

GitHub/API snapshot for canonical repo:

- full_name: `multica-ai/andrej-karpathy-skills`
- description: `A single CLAUDE.md file to improve Claude Code behavior, derived from Andrej Karpathy's observations on LLM coding pitfalls.`
- stars: `141980`
- forks: `14572`
- default branch: `main`
- license: null in API, while README says MIT

## Core content

The repo packages four behavioral principles for coding agents:

1. **Think Before Coding** — do not assume; surface ambiguity/tradeoffs; ask when truly unclear.
2. **Simplicity First** — minimum code that solves the request; no speculative abstractions/features.
3. **Surgical Changes** — touch only necessary lines; no drive-by refactors; clean only orphans created by the current change.
4. **Goal-Driven Execution** — convert tasks into success criteria and verification loops.

This is closer to a coding-agent behavior constitution than a multi-tool local Garden skill pack.

## Hermes overlap and gap

Hermes already has many of these preferences in memory and developer policy:

- evidence-first execution,
- do not fake completion,
- patch existing/umbrella skill before creating duplicates,
- tests/build after code edits,
- avoid unnecessary broad changes.

Still, the repo is useful because it gives a compact, teachable rubric that can improve Hermes coding workflow skills and code-review prompts.

Best absorption target:

- Patch existing Hermes skills rather than create a standalone duplicate:
  - `code-simplification`
  - `incremental-implementation`
  - `systematic-debugging`
  - `test-driven-development`
  - `github-code-review`
  - possibly `development-closure-consensus`
- Add a short reference/checklist named around "Karpathy coding guardrails" only if repeated use needs a dedicated progressive-loading file.

## Recommended migration strategy

Do not import the upstream `SKILL.md` verbatim. Instead, absorb it as a Hermes coding guardrail layer:

1. Add/verify trigger language in relevant skills for:
   - ambiguous coding requests,
   - overengineering risk,
   - broad diff/refactor risk,
   - weak success criteria.
2. Add reviewer checks:
   - every changed line traces to request,
   - no speculative API/config/feature,
   - assumptions were surfaced before implementation,
   - tests or verification were defined before/with implementation.
3. Add negative space:
   - trivial typo/one-line edits should not require heavy ceremony,
   - non-coding tasks should not be forced through coding workflow skills,
   - user-explicit broad refactors may intentionally touch larger surfaces,
   - existing project-specific red lines override generic simplicity advice.
4. Verify by applying the checklist to one Hermes code task and one uvisa-style project task.

## Taskpool classification

- lane: `evolution`
- project: `hermes-agent`
- architecture_target: `skill-graph`
- priority: `P2`
- multimodel: optional for pure documentation patch; required only if changing routing/default coding policy broadly.

## Acceptance evidence

- Source URL confirmed via search/API and wrong candidate URLs recorded.
- Repo cloned locally at commit `2c60614`.
- Core files inspected: README, CLAUDE, SKILL, EXAMPLES.
- Migration decision: patch existing Hermes coding workflow skills first; avoid duplicate standalone skill unless trigger evals justify it.
- Future implementation must include routing/negative-space evals and read-back verification of any patched skill.

## Red lines

- Do not treat this as the Garden local-private knowledge-base project described earlier; it is a coding-agent behavior guideline repo.
- Do not overwrite Hermes developer/system policies with third-party CLAUDE.md text.
- Do not make agents ask needless questions for low-stakes obvious defaults; Hermes has an explicit act-don't-ask rule.
- Do not broaden "surgical changes" into refusing necessary cross-cutting infrastructure changes; user preference remains long-term maintainable architecture when required.
