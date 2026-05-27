# Hermes Skills Health Audit Gate (2026-05-27)

Task SSOT: `2026-05-27-add-governed-skills-health-audit-gate-for-token-budget-duplicates-and-profile-dr`.

## Contract

`hermes skills health --json` and `python -m tools.skills_health_audit --json` emit schema-versioned JSON (`hermes_skills_health_audit_v1`) for deterministic taskpool evidence.

The audit is read-only by default: it discovers `SKILL.md` files, parses frontmatter metadata, and reports structured `violations` / `warnings` without changing skill content or profile configuration.

## Fail-closed boundaries

- Missing active Hermes profile context is a violation.
- A supplied skills root under `~/.hermes/profiles/<other>/skills` is a `cross_profile_skill_root` violation when `<other>` is not the active profile.
- Over-budget descriptions are `description_token_budget_exceeded` violations.
- Duplicate skill names are violations; duplicate trigger overlap is a warning for manual review.

## Evidence expectations

A valid closeout must include command/read-back evidence, not only `py_compile` or generic selfcheck/proof-check output. At minimum:

1. focused unit test proving clean, token-budget-exceeded/duplicate-overlap, and cross-profile fixture cases;
2. CLI or module JSON run against active skills;
3. taskpool `selfcheck`, `proof-check`, and `next --reconcile` after lifecycle transition.
