# Modular Workflow Kernel Phase 22 ADR: Cron / Provider Degradation Triage

## Status

Accepted — the scheduled automation degradation discovered in Phase 21 is classified with a read-only triage artifact. No cron jobs are paused, resumed, removed, or retried in loops by this phase.

## Context

Phase 21 architecture self-check classified Hermes as `DEGRADED_NOT_BLOCKED`. The P1 degraded surface was cron/provider automation:

- 9 failed cron jobs in 53 active jobs.
- Most failures surfaced as `RuntimeError: Connection error.` from LLM/provider generation paths.
- One upstream ratchet job failed at a network fetch boundary (`github.com:443`, `SSL_connect`).
- One daily report also had a Telegram delivery proxy failure.

The interactive Hermes runtime, taskpool selfcheck, taskpool proof-check, mission doctor, and governed Telegram policy remained operational.

## Decision

Add a deterministic, read-only triage classifier:

```text
scripts/cron_provider_degradation_triage.py
```

The classifier reads `~/.hermes/cron/jobs.json`, identifies `last_status=error`, assigns primary classes, and writes local evidence only when requested.

Primary classes:

- `provider_generation`
- `script_network_failure`
- `telegram_delivery`
- `script_failure`
- `network_or_proxy`
- `unknown`

## Safety Boundaries

Phase 22 explicitly does **not**:

- mutate `~/.hermes/cron/jobs.json`;
- pause/resume/remove cron jobs;
- run failed jobs;
- retry model calls;
- send Telegram messages;
- migrate config;
- apply npm audit fixes.

## Evidence

Current run:

```text
status: DEGRADED_NOT_BLOCKED
failed_count: 9
class_counts:
  provider_generation: 7
  script_network_failure: 1
  telegram_delivery: 1
```

Artifacts:

```text
docs/private/workflow-kernel-cron-provider-triage/cron_provider_triage_20260613_phase22.json
docs/private/workflow-kernel-cron-provider-triage/cron_provider_triage_20260613_phase22.md
```

## Consequences

Benefits:

- Converts generic `RuntimeError: Connection error` cron red into actionable buckets.
- Prevents broad cron churn or repeated provider burn.
- Provides the next phase with a bounded repair queue.

Tradeoffs:

- Does not immediately fix provider/network availability.
- Leaves failed jobs marked as failed until their next scheduled run or a governed targeted retry.

## Next Phase

Phase 23 should repair one class at a time, preferably:

1. Add provider/network probe ledger so cron provider failures have a shared upstream health signal.
2. Add upstream ratchet `--no-fetch` fallback or fail-soft network classification in the no-agent wrapper.
3. Route Telegram retry through the Phase 20 governed notification ledger.
4. Only quarantine repeated failures after a second classified failure window.
