# Hermes Cron / Provider Degradation Triage — Phase 22

Status: **DEGRADED_NOT_BLOCKED**
Failed jobs: **9 / 57**
Class counts: `{"provider_generation": 7, "script_network_failure": 1, "telegram_delivery": 1}`

## Failed Jobs
- **789ca7d5e718 自进化健康监控告警** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops
- **2c14ca2a9bd2 multi-model-smoke-every-4h** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops
- **42baeebb646c multi-model-smoke-daily-full** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops
- **30842cd6b22b cron-health-quarantine-report-6h** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops
- **b289e9307230 orchestrator-selfcheck-6h** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops
- **8c40491124b1 memory-selfcheck-6h** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops
- **c01e3574e31c aihot-daily-report** — `telegram_delivery`; keep message in ledger and retry through governed notification policy after proxy recovers
- **8aa11d544e8c hermes-upstream-ratchet-review** — `script_network_failure`; add no-fetch/offline fallback and classify as network-bound script failure
- **56cba2b9feff Hermes self-architecture mission** — `provider_generation`; retry only after provider/network probe; avoid burning model calls in tight loops

## Safe Boundaries
- no cron schedule mutation
- no job pause/resume/remove
- no model-call retry loop
- no external message send
- no config migration

## Next Actions
- run provider/network probe once and persist result
- add no-fetch fallback for upstream ratchet review
- route Telegram retry through governed notification policy ledger
- quarantine repeated provider_generation jobs only after a second classified failure window
