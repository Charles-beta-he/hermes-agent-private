# Modular Workflow Kernel Phase 24 ADR: Cron Provider Health Gate

## Status

Accepted — Phase 24 compiles read-only cron/provider health-gate decisions from the Phase 22 triage report and Phase 23 provider network probe ledger. It does not mutate cron state or run any jobs.

## Context

Phase 22 classified cron failures into provider/network/delivery classes. Phase 23 proved that the current network edge is mostly reachable but `xiaomi` is degraded under a bounded unauthenticated TLS probe:

```text
reachable: 5
tls_error: 1
degraded endpoint: xiaomi
```

The next safe layer is not to rerun failed jobs. It is to compile per-job decisions that future cron wrappers can consume before burning model-generation calls.

## Decision

Add:

```text
scripts/cron_provider_health_gate.py
```

The script consumes:

```text
~/.hermes/cron/jobs.json
docs/private/workflow-kernel-provider-network-probe/provider_network_probe_20260613_phase23.json
docs/private/workflow-kernel-cron-provider-triage/cron_provider_triage_20260613_phase22.json
```

It emits decisions such as:

- `skip_provider_health_degraded`
- `retry_allowed_by_provider_health`
- `retry_allowed_by_provider_health_manual_only`
- `skip_fetch_use_no_fetch_fallback`
- `run_with_degraded_provider_exclusions`
- `manual_investigation_required`

## Current Evidence

Current run evaluated the cron jobs that are still in `last_status=error` at execution time:

```text
status: GATE_COMPILED_NO_MUTATION
failed_job_count: 2
decision_counts:
  retry_allowed_by_provider_health_manual_only: 1
  skip_provider_health_degraded: 1
degraded_endpoints:
  xiaomi
```

Decisions:

```text
42baeebb646c multi-model-smoke-daily-full
  decision: skip_provider_health_degraded
  endpoint: xiaomi
  reason: target endpoint xiaomi classified as tls_error

c01e3574e31c aihot-daily-report
  decision: retry_allowed_by_provider_health_manual_only
  endpoint: telegram
  reason: telegram endpoint reachable; retry must still go through governed notification ledger
```

This differs from the Phase 22 count of 9 because several cron jobs no longer have `last_status=error` in the current `jobs.json` readback. The gate intentionally uses current scheduler state.

## Safety Boundaries

Phase 24 explicitly does **not**:

- mutate `~/.hermes/cron/jobs.json`;
- pause/resume/remove jobs;
- rerun failed jobs;
- call model generation APIs;
- send Telegram messages;
- validate provider credentials.

## Consequences

Benefits:

- Converts provider/network evidence into per-job action decisions.
- Avoids globally disabling cron because only Xiaomi/MiMo is currently degraded.
- Prevents blind retries for the Xiaomi-backed daily multi-model smoke job.
- Keeps Telegram retry behind the governed notification ledger even when Telegram endpoint is reachable.

Tradeoffs:

- Actual cron wrapper integration is not enabled yet.
- Current decisions are snapshot-based and should be regenerated before use.

## Next Phase

Phase 25 should add an opt-in wrapper integration for one low-risk provider-generation cron path:

1. Read latest health-gate decision.
2. If decision is `skip_provider_health_degraded`, exit 0 with typed local evidence rather than failing the cron job.
3. If decision permits retry, continue to the existing script path.
4. Do not apply to all cron jobs at once.
