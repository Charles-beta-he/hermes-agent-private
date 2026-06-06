# Feynman → Hermes Gap Analysis Intake — 2026-06-02

## Source evidence

- Reference repo: https://github.com/getcompanion-ai/feynman
- Observed clone commit: `51236f6ae60a2894f9987465f431e9fe7c1a777a`
- Key inspected files:
  - `README.md`
  - `AGENTS.md`
  - `.feynman/SYSTEM.md`
  - `.feynman/agents/{researcher,verifier}.md`
  - `src/cli.ts`
  - `src/pi/runtime.ts`
  - `src/pi/package-ops.ts`
  - `src/bootstrap/sync.ts`
  - `extensions/research-tools.ts`
  - `package.json`

## What Feynman is actually doing

Feynman is not just a prompt pack. It is a research-specific shell built on top of Pi with four notable control-plane layers:

1. Runtime wrapper:
   - `src/cli.ts` drives a dedicated `feynman` CLI surface.
   - `src/pi/runtime.ts` injects Feynman-specific prompt templates, system prompt, extension path, session dir, memory dir, and package-manager env into the underlying Pi runtime.
2. Research tool extension layer:
   - `extensions/research-tools.ts` installs a session-start header plus alphaXiv, Hugging Face, discovery, output, and service-tier controls.
3. Bundled asset / install lane:
   - `src/bootstrap/sync.ts` syncs managed themes, agents, and skills from the packaged repo into the live agent directory with hash-based skip/update protection.
   - `src/pi/package-ops.ts` handles optional runtime package installs/updates with environment pinning and peer-package repair.
4. Artifact discipline for long research workflows:
   - `AGENTS.md` requires a slugged artifact family, plan file under `outputs/.plans/`, `CHANGELOG.md` lab notebook, and provenance sidecars.
   - `.feynman/agents/verifier.md` forces claim-level citation anchoring and URL verification before delivery.

## Transferable patterns vs. Hermes

| Feynman pattern | Hermes current coverage | Gap | Decision |
|---|---|---|---|
| Externalized plan artifact as live working memory | Hermes has `planning-with-files`, `todo`, kanban comments, and session search. | Hermes planning guidance is strong on “write a plan file” but weaker on a canonical artifact bundle for long, evidence-heavy runs. | ACCEPT |
| Workspace lab notebook (`CHANGELOG.md`) for resumable long runs | Hermes kanban comments + summaries exist, but they are board-native rather than workspace-native chronological notes. | Missing a compact pattern that pairs plan file + local lab notebook for long/restartable work. | ACCEPT |
| Provenance sidecar / verification artifact separate from polished deliverable | Hermes has evidence/proof culture, but plan-file guidance does not explicitly normalize a separate verification/provenance sidecar next to the main artifact. | Missing a small, reusable file pattern. | ACCEPT |
| Slugged artifact family to avoid collisions in concurrent runs | Hermes plans often use timestamps/paths, but no memorable family convention is taught in the planning skill. | Small naming convention gap. | ACCEPT |
| Runtime-installed bundled skills/agents with hash-safe sync | Hermes already has richer native skills, skills hub, curator telemetry, and profile-aware storage. | Feynman’s sync model is useful as reference but not obviously superior to Hermes’s existing skill architecture. | WATCH only |
| Research-specific verifier subagent that removes unsupported claims | Hermes has general verification discipline and review-required kanban flow, but not a universal source-verifier subagent. | Valuable for future research/report lanes, but larger than a bounded slice for this task. | WATCH / future lane |

## Hermes landing-zone decision

Primary landing zone: `planning-with-files` skill.

Why this lane:
- It already owns the “state survives compression via files” problem.
- Feynman’s most transferable contribution is not its Pi runtime wrapper; it is the artifact discipline around long-running work.
- A skill/reference patch is low-risk, immediately reusable, and does not force a runtime or protocol migration.

## Bounded slice landed in this run

Patched Hermes `planning-with-files` with a Feynman-inspired artifact-bundle pattern via:
- new reference: `skills/software-development/planning-with-files/references/feynman-artifact-bundle.md`
- SKILL.md wording patch to point readers at the reference and explain when to use the bundle.

The absorbed pattern is deliberately scoped:
- plan file remains the primary execution ledger;
- optional local lab notebook records chronological progress/failures for long runs;
- optional provenance/verification sidecar records what was actually checked;
- slugged artifact family reduces collision and resume confusion.

This is an adoption of the pattern, not a claim that Hermes now behaves like Feynman end-to-end.

## Non-claims / boundaries

- No Pi runtime integration or package-manager behavior was ported into Hermes.
- No Feynman code was executed as part of Hermes.
- No claim is made that Hermes now has Feynman-equivalent research verification automation.
- No new Hermes runtime dependency or provider was introduced.

## Recommended next slices

1. Research/report lane: prototype a Hermes-native “verification sidecar” output pattern for source-heavy reports.
2. Delegation lane: define when delegate/kanban workers should keep a local lab notebook in addition to board comments.
3. Skills lane: evaluate whether `subagent-driven-development` should explicitly prefer file-based handoffs plus verification notes for long delegated work.

## Verification expected for this slice

- Read back the new reference file.
- Read back the patched `planning-with-files` section.
- Confirm the new reference appears under `skill_view(name='planning-with-files')` linked files.

