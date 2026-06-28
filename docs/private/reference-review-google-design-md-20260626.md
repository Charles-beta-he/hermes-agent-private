# Reference Review: google-labs-code/design.md → Hermes persistent design context

## Source

- Repository: https://github.com/google-labs-code/design.md
- Local clone: `/Users/charles/.hermes/runs/reference-repos/design.md`
- Verified HEAD: `2a19f5dd97ab887971b417ebdf1e7e8fda0c7f79`
- Read timestamp: 2026-06-26T02:42:49Z
- License observed in source files: Apache-2.0 headers in CLI source files.

## Evidence read

- `README.md:1-4`: DESIGN.md is a format specification for describing a visual identity to coding agents and gives agents persistent, structured understanding of a design system.
- `README.md:52-58`: CLI lint validates a DESIGN.md and returns structured JSON findings.
- `README.md:89-100`: file has two layers: YAML front matter for normative tokens and Markdown prose for rationale/context.
- `README.md:164-172`: unknown content is mostly preserve/warn; duplicate section heading is an error.
- `README.md:291-299`: `spec` command can output spec/rules for prompt injection.
- `docs/spec.md:6-8`: the file is a self-contained living source of truth humans and AI can refine; tokens are normative and prose is contextual.
- `packages/cli/src/linter/linter/rules/missing-sections.ts:18-25`: missing optional sections produce fallback warnings rather than hard failure.

## Hermes mapping decision

Adopt the pattern as an optional **project-local design/context artifact**, not as a new global Hermes authority.

Recommended hierarchy for Hermes:

1. Existing Hermes skills, taskpool lifecycle/proof gates, and project-profile source weighting remain higher authority.
2. `DESIGN.md` / `design.md` may be discovered inside a project repo and injected into UI/product implementation prompts as durable visual/rationale context.
3. The file should be treated as a scoped context source with explicit read-back evidence, not as completion proof.
4. If global skill/profile behavior changes are proposed later, require a separate task with multimodel certification.

## Minimal reversible integration shape

- Discovery order: `DESIGN.md`, `design.md`, `.hermes/DESIGN.md`, `docs/DESIGN.md` under the project root only.
- Output contract: a small context bundle containing source path, token summary, section names, and a warning that this does not replace skills/memory/project profile/taskpool proof gates.
- Validation: fail-soft. Missing file means no extra context. Structural parser/linter errors should warn and skip injection for that project only.
- Rollback: delete the project-local DESIGN.md artifact or disable the optional context reader; no taskpool or skill migration required.

## Local dry-run evidence

- Dry-run command: `python3 - <<'PY' ...` run from `/Users/charles/.hermes/hermes-agent` on 2026-06-26.
- Sample project: `/Users/charles/.hermes/runs/design-md-hermes-dryrun/sample-project/DESIGN.md`
- Result artifact: `/Users/charles/.hermes/runs/design-md-hermes-dryrun/discovery-result.json`
- Read-back result: discovered `DESIGN.md`, extracted `version=alpha`, `name=Hermes Console Sample`, sections `Overview`, `Colors`, `Typography`, and preserved `does_not_replace=[skills,memory,project_profile,taskpool_evidence_gates]`.

## Non-goals / red lines

- Do not weaken taskpool proof, permission, or mission-envelope gates.
- Do not treat a synthesized envelope or DESIGN.md read as implementation proof.
- Do not copy the Google CLI wholesale or introduce a new global runtime gate in this task.
- Do not override product PRD/project-profile source weighting with visual design prose.

## Follow-through read-back (2026-06-26T05:53Z)

- Artifact: `/Users/charles/.hermes/runs/design-md-hermes-dryrun/follow-through-readback-20260626T0553Z.json`
- Result: PASS.
- Deterministic checks: live `git ls-remote` HEAD matched local clone HEAD `2a19f5dd97ab887971b417ebdf1e7e8fda0c7f79`; source URL/read timestamp were present; mapping decision was present; dry-run discovery stayed project-local, found `DESIGN.md`, and preserved `skills`, `memory`, `project_profile`, and `taskpool_evidence_gates` as higher authority.
- Closeout boundary: this is implementation/evidence read-back for review readiness only. It does not satisfy `requires_multimodel` done certification while the task's latest multimodel gate remains `FAIL`.

## Closeout assessment

This bounded tranche satisfies the intake/evaluation acceptance evidence: source URL + commit/read timestamp, Hermes mapping note under `docs/private`, and a local dry-run showing project DESIGN.md discovery without replacing existing skills. Because the task frontmatter still has `requires_multimodel: true` and the latest multimodel gate is `FAIL`, this note supports transition to `review` for certification/closeout, not direct `done`.
