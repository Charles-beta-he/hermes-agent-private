# Hermes Cron Provider Health Gate — Phase 24

Status: **GATE_COMPILED_NO_MUTATION**
Failed jobs evaluated: **3**
Decision counts: `{"manual_investigation_required": 2, "retry_allowed_by_provider_health_manual_only": 1}`
Degraded endpoints: `[]`

## Decisions
- **861b14d4d887 local-brain-taskpool-auto-completer** — `manual_investigation_required` endpoint=None reason=no endpoint inference available
- **c01e3574e31c aihot-daily-report** — `retry_allowed_by_provider_health_manual_only` endpoint=telegram reason=telegram endpoint reachable; retry must still go through governed notification ledger
- **aa8f66ecbe10 taskpool-follow-through-agent** — `manual_investigation_required` endpoint=None reason=no endpoint inference available

## Safe Boundaries
- no cron job mutation
- no failed job rerun
- no LLM generation call
- no external message send
- no provider credential check

## Next Actions
- wire cron wrappers to read this gate before provider generation in a later opt-in phase
- skip only the target degraded provider; do not globally disable cron
- use governed notification ledger for Telegram retry even when endpoint is reachable
