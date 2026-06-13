# Hermes Provider Cron Health Gate — Phase 24

Status: **DEGRADED_GATED**
Probe status: **DEGRADED_PARTIAL**
Failed jobs gated: **7**
Gate counts: `{"hold_for_unmapped_provider_probe": 4, "manual_governed_notification_retry_candidate": 1, "skip_provider_generation_retry": 2}`

## Job Decisions
- **789ca7d5e718 自进化健康监控告警** — `hold_for_unmapped_provider_probe` (provider_generation, endpoint=None, probe=None): provider-generation failure has no mapped endpoint probe; do not retry blindly
- **2c14ca2a9bd2 multi-model-smoke-every-4h** — `hold_for_unmapped_provider_probe` (provider_generation, endpoint=None, probe=None): provider-generation failure has no mapped endpoint probe; do not retry blindly
- **42baeebb646c multi-model-smoke-daily-full** — `skip_provider_generation_retry` (provider_generation, endpoint=xiaomi, probe=tls_error): target provider endpoint xiaomi is currently tls_error; avoid burning retries
- **30842cd6b22b cron-health-quarantine-report-6h** — `hold_for_unmapped_provider_probe` (provider_generation, endpoint=None, probe=None): provider-generation failure has no mapped endpoint probe; do not retry blindly
- **8c40491124b1 memory-selfcheck-6h** — `skip_provider_generation_retry` (provider_generation, endpoint=xiaomi, probe=tls_error): target provider endpoint xiaomi is currently tls_error; avoid burning retries
- **c01e3574e31c aihot-daily-report** — `manual_governed_notification_retry_candidate` (telegram_delivery, endpoint=openai, probe=reachable): telegram edge is reachable, but unattended/external sends remain blocked by mission policy
- **56cba2b9feff Hermes self-architecture mission** — `hold_for_unmapped_provider_probe` (provider_generation, endpoint=None, probe=None): provider-generation failure has no mapped endpoint probe; do not retry blindly

## Safe Boundaries
- no cron schedule mutation
- no job pause/resume/remove
- no model generation call
- no provider retry loop
- no credential read/write
- no external message send

## Next Actions
- wire this gate into future cron retry planning as a dry-run preflight first
- only retry provider_generation jobs when endpoint is reachable and a separate governed retry scope exists
- route telegram retries through the governed notification policy and explicit send authorization
