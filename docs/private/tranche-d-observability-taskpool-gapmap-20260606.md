# Tranche D — Observability/taskpool governance scoped gap map (2026-06-06)

## Scope

Continue the private overlay ratchet after Tranche C without replaying the old private branch wholesale. Tranche D candidates from `docs/private/overlay-ratchet-merge-plan-20260606.md`:

- `kanban_cross_surface_progress_bridge`
- `taskpool_validation_security_gates`
- `skills_health_audit_cli`

## Findings

### Kanban cross-surface progress bridge

Current `private/main` already has safer primitives for the core progress-linking goal:

- native `tasks.session_id` support in kanban task records
- session filtering in kanban listing
- run/heartbeat/progress surfaces elsewhere in the current runtime

The old `task_session_links` bridge overlaps with current schema and would introduce a second source of truth. The old gateway-side progress integration also risks mutating kanban/task lifecycle from a presentation surface, so it is not suitable for direct replay.

A small delegate/tool-progress stringification hardening from the old family remains potentially useful, but it is a separate micro-port candidate and should avoid DB/schema changes.

### Taskpool validation/security gates

The old taskpool validators are still conceptually valuable, but they are best restored as read-only/report-only scripts first:

- taskpool long-task evidence validator
- provider routing cost-quality evidence validator
- taskpool security regression benchmark

They should not be wired directly into lifecycle auto-close or provider-routing mutation until mapped against the current taskpool semantics.

### Skills health audit CLI

The old skills health audit is the safest Tranche D port:

- deterministic
- read-only
- schema-versioned JSON output
- active-profile/cross-profile guardrails
- complements current `hermes skills audit` instead of replacing it
- directly addresses post-update-check's recurring skill-quality visibility gap

## Selected port

Port `skills_health_audit_cli` only:

- `tools/skills_health_audit.py`
- `tests/tools/test_skills_health_audit.py`
- `hermes_cli/main.py`
- `hermes_cli/skills_hub.py`

## Non-goals

- No kanban schema migration.
- No `task_session_links` resurrection.
- No gateway/run presentation-surface lifecycle mutation.
- No taskpool validator enforcement or lifecycle blocking.
- No skill auto-fix/mutation.
- No cross-profile writes.

## Verification plan

- `./venv/bin/python -m py_compile tools/skills_health_audit.py hermes_cli/skills_hub.py hermes_cli/main.py`
- `./venv/bin/python -m pytest -o addopts='' tests/tools/test_skills_health_audit.py -q`
- Temp-root CLI smoke with `hermes skills health --root ... --json`
- `git diff --check`
- static added-line security scan
- `python3 ~/.hermes/scripts/post-update-check.py --fix`
- independent pre-commit review before commit
