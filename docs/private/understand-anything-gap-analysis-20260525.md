# Understand Anything Hermes Gap Analysis Intake — 2026-05-25

## Source discovery

The video did not include a direct GitHub URL, so the source was discovered by GitHub repository search for the quoted phrase `Understand Anything` sorted by stars.

Canonical repo selected:

- Source: https://github.com/Lum1104/Understand-Anything
- Repo: `Lum1104/Understand-Anything`
- Why selected: top matching GitHub result; description and README match the video summary: interactive knowledge graph for codebases, Claude Code/Cursor/Copilot/Gemini integrations, TypeScript implementation.

## Source evidence

Observed via GitHub API on 2026-05-25:

- Description: `Graphs that teach > graphs that impress. Turn any code into an interactive knowledge graph you can explore, search, and ask questions about. Works with Claude Code, Codex, Cursor, Copilot, Gemini CLI, and more.`
- Stars: 27926
- Forks: 2359
- License: MIT
- Primary language: TypeScript
- Default branch: `main`
- Last pushed: 2026-05-24T13:12:57Z
- Topics include: `codebase-analysis`, `knowledge-graph`, `claude-code`, `codex`, `codex-skills`, `gemini-cli-skills`, `memory`, `developer-tools-ai-agent`.

Top-level layout:

- `.claude-plugin/`
- `.copilot-plugin/`
- `.cursor-plugin/`
- `CLAUDE.md`
- `README.md`
- `docs/`
- `homepage/`
- `install.sh`
- `package.json`
- `tests/`
- `understand-anything-plugin/`

Internal plugin shape:

- `understand-anything-plugin/packages/core` — analysis engine: tree-sitter, persistence, search, schema, tours, plugins.
- `understand-anything-plugin/packages/dashboard` — React + TypeScript dashboard using React Flow / Zustand / Tailwind.
- `understand-anything-plugin/src` — commands for chat, diff, explain, onboarding.
- `understand-anything-plugin/skills` — `/understand`, `/understand-dashboard`, `/understand-domain`, `/understand-knowledge`, `/understand-chat`, `/understand-diff`, `/understand-explain`, `/understand-onboard`.
- `understand-anything-plugin/agents` — `project-scanner`, `file-analyzer`, `architecture-analyzer`, `domain-analyzer`, `tour-builder`, `graph-reviewer`, etc.

README / CLAUDE evidence:

- Multi-agent pipeline scans project files, functions, classes, and dependencies.
- Graph is stored as `.understand-anything/knowledge-graph.json`.
- Dashboard provides interactive structural graph, node summaries, relationships, guided tours, file explorer, and source viewer.
- Domain view maps code to real business processes: domains, flows, and steps.
- Knowledge-base mode supports Karpathy-style LLM wiki graphs.
- Features include fuzzy/semantic search, diff impact analysis, persona-adaptive UI, layer visualization, and language-concept explanations.
- README describes team sharing by committing the generated graph JSON; microservices-demo is cited as an example.

## User-supplied video framing

The user described a SCAN-MAP-TEACH flow:

1. SCAN: automatically scan code structure.
2. MAP: map dependencies and architectural/business relationships.
3. TEACH: explain logic via guided, teaching-style navigation.

The user also reported simple/medium/complex multi-level views for microservice architecture and business logic. This exact phrasing was not fully found in the README excerpt, so it should be treated as video-derived framing and verified during the later read-only repo pass rather than assumed as implemented semantics.

## Hermes landing-zone decision

- Lane: `evolution`.
- Project: `hermes-agent`.
- Architecture target: `local-brain`.
- Priority layer: `P2`.
- Multimodel requirement for execution: `false`.

Rationale:

- High leverage for Hermes codebase intelligence, local brain, taskpool evidence browsing, and future project-onboarding UX.
- Not an immediate production blocker or safety defect, so not P1 by default.
- The first Hermes step should be read-only gap analysis and selective absorption/prototype planning, not installing/running the plugin.
- Multi-model was useful for intake triage, but the queued task itself can be executed deterministically via source review and local design mapping. Escalate to multimodel only if a later task proposes a core Hermes architecture change.

## Multi-model triage

A real T1 fan-out was run with:

- `gpt-5.5`
- `mimo-v2.5-pro`
- `deepseek-v4-pro`

Artifact:

- `/Users/charles/.hermes/tick-20260525-072447-629196-p23430-5d36edc3-50a1117b/`

Result:

- Fan-out quorum met: 3/3 planners succeeded.
- Consensus was weak but directionally aligned: create a governed intake task, do not install/execute third-party code, and preserve fail-closed multimodel boundaries.
- Priority disagreement: one planner suggested P1 due to relevance; final decision is P2 because the repo is high-value but not tied to a current blocker.

## Capability delta

Hermes should evaluate whether Understand Anything can strengthen:

1. codebase-to-knowledge-graph extraction for files/functions/classes/dependencies;
2. local interactive graph/dashboards for large-project onboarding and architecture navigation;
3. business-domain mapping from code to domains/flows/steps;
4. guided tours / teaching-style path generation for repo comprehension;
5. semantic/fuzzy search over code graph nodes;
6. diff-impact analysis integrated with taskpool or PR review workflows;
7. shareable `.understand-anything/knowledge-graph.json`-style artifact strategy for teams and agents;
8. adapter lessons for Claude Code, Cursor, Copilot, Gemini CLI, Codex, and Hermes-style integrations.

## Anti-drift constraints

- Do not run `install.sh`, plugin marketplace commands, or any Understand Anything scripts during intake.
- Do not commit generated `.understand-anything/` graphs to user projects during intake.
- Do not copy the implementation wholesale into Hermes.
- Do not turn this into a generic repo summary or star-count report.
- Treat README/video claims as claims until source paths and behavior are inspected.
- Prefer a small prototype boundary or existing Hermes skill/reference patch over broad new subsystem work.
- Keep codebase graph artifacts local, provenance-bearing, and subordinate to user/system instructions.

## Recommended taskpool metadata

- Title: `Gap-analyze Understand Anything for Hermes codebase knowledge-graph absorption`
- Objective: perform a read-only provenance and architecture review of `Lum1104/Understand-Anything`, map SCAN-MAP-TEACH / graph dashboard / domain-view mechanisms onto Hermes local-brain and codebase-intelligence surfaces, then propose selective absorption/prototype tasks without executing the third-party plugin.
- Dependency order:
  1. verify repo identity, license, and representative source paths;
  2. inspect scanner/analyzer/schema/dashboard/tour-builder implementation;
  3. map mechanisms to Hermes local-brain/taskpool/skill graph surfaces;
  4. produce accept/reject/watch migration matrix;
  5. create follow-up prototype or patch tasks only for accepted deltas;
  6. verify with selfcheck/proof-check and focused tests if code changes occur.
- Parallel boundary: repo architecture review, dashboard UX review, and Hermes landing-zone inventory may run in parallel; migration/prototype work waits for the gap matrix; no third-party install/execution during intake.
- Acceptance evidence: source provenance, repo structure evidence, implementation-path inventory, Hermes gap matrix, migration decisions, follow-up task list, and verification commands.
