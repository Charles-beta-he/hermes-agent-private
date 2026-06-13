# Hermes Cron Provider Health Gate — Phase 24

Status: **READY**
Probe status: **DEGRADED_PARTIAL**
Decision counts: `{"retry_allowed_by_provider_health": 1, "run_with_degraded_provider_exclusions": 1, "skip_provider_health_degraded": 18}`

## Decisions
- **600370f80d83 夜间自主进化审计** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **1d5c2ca35c01 skill-steward-daily-audit** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **6510a716937c 双脑自进化 (Hermes→ClaudeCode→落地→Eval)** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **0eb700202918 snapshot-GC + evolve.log月归档** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **0ca2cfd4068b hermes-failure-taxonomy** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **fbba30dd4a8b synthetic-trace-ratio-guard** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **c5cf6a078a72 planner-failure-triage** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **2c14ca2a9bd2 multi-model-smoke-every-4h** — `run_with_degraded_provider_exclusions`; multi-provider job can proceed only if runtime excludes degraded providers
- **42baeebb646c multi-model-smoke-daily-full** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **9e30cb4804db brain-kanban-bridge-sync** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **6bc821b4743e pipeline-tick-hourly** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **286f7b115fa5 agent-team-cycle-hourly** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **2f7b58c5bc90 envelope-executor-cycle** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **6bfc53bb6734 v-state-hourly** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **b289e9307230 orchestrator-selfcheck-6h** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **8c40491124b1 memory-selfcheck-6h** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **5b55c94d37d0 task-pool-guardian-10m** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **c01e3574e31c aihot-daily-report** — `retry_allowed_by_provider_health`; endpoint openai reachable; failure likely not current edge reachability
- **5061c026bc76 hermes-health-check** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error
- **1b78a33f7b73 behavioral-benchmark-daily** — `skip_provider_health_degraded`; endpoint xiaomi classified tls_error

## Safe Boundaries
- no cron job mutation
- no failed job rerun
- no model generation call
- no credential read/write
- no external message send

## Next Actions
- wire cron provider jobs to read this gate before generation
- for skip_provider_health_degraded, emit typed local skip instead of RuntimeError red
- for multi-provider jobs, exclude degraded providers or fall back before generation
