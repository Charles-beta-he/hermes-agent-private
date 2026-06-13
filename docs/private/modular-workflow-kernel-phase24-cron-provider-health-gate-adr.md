# Modular Workflow Kernel Phase 24 ADR: Cron Provider Health Gate

## Status

Accepted — Phase 24 adds a read-only provider health gate that consumes the Phase 23 provider/network probe ledger and compiles cron job run/skip decisions without mutating scheduler state.

## Context

Phase 23 proved the current provider/network state is `DEGRADED_PARTIAL`:

```text
reachable: 5
tls_error: 1
degraded endpoint: xiaomi
```

Cron jobs should not blindly retry provider generation when their target provider endpoint is already degraded. However, the gate must not make broad scheduler changes or block unrelated providers.

## Decision

Add:

```text
scripts/cron_provider_health_gate.py
```

The gate:

1. Reads `~/.hermes/cron/jobs.json`.
2. Reads the latest provider network probe ledger.
3. Infers each job's provider/endpoint from explicit provider, model, name, or script.
4. Emits local decisions:
   - `run`
   - `retry_allowed_by_provider_health`
   - `skip_provider_health_degraded`
   - `run_with_degraded_provider_exclusions`
   - `needs_provider_resolution`
   - `needs_probe`
5. Writes local evidence under `docs/private/workflow-kernel-provider-health-gate/` when requested.

## Safety Boundaries

The gate explicitly does **not**:

- mutate `~/.hermes/cron/jobs.json`;
- pause/resume/remove cron jobs;
- rerun failed jobs;
- call LLM generation APIs;
- read/write credentials;
- send external messages.

## Evidence

Current output:

```text
status: READY
decision_counts:
  skip_provider_health_degraded: 18
  run_with_degraded_provider_exclusions: 1
  retry_allowed_by_provider_health: 1
```

Important examples:

```text
multi-model-smoke-every-4h:
  run_with_degraded_provider_exclusions
  reason: multi-provider job can proceed only if runtime excludes degraded providers

multi-model-smoke-daily-full:
  skip_provider_health_degraded
  reason: endpoint xiaomi classified tls_error

aihot-daily-report:
  retry_allowed_by_provider_health
  reason: endpoint openai reachable; failure likely not current edge reachability
```

Artifacts:

```text
docs/private/workflow-kernel-provider-health-gate/cron_provider_health_gate_20260613_phase24.json
docs/private/workflow-kernel-provider-health-gate/cron_provider_health_gate_20260613_phase24.md
```

## Consequences

Benefits:

- Converts repeated generic `RuntimeError: Connection error` into provider-targeted run/skip decisions.
- Avoids burning model retries against a degraded provider endpoint.
- Keeps OpenAI-backed jobs separate from Xiaomi/MiMo degradation.
- Gives multi-provider jobs an exclusion strategy instead of full stop.

Tradeoffs:

- This phase only compiles decisions. Cron runners do not yet consume the gate automatically.
- Provider inference from legacy jobs is heuristic until jobs declare explicit provider targets.

## Next Phase

Phase 25 should wire a small, opt-in cron wrapper around selected provider-generation jobs:

1. Read the latest health gate report.
2. If decision is `skip_provider_health_degraded`, emit a typed local skip record and exit 0.
3. If decision is `run_with_degraded_provider_exclusions`, pass an exclusion hint to the underlying script where supported.
4. Keep the rollout to one or two jobs first; do not batch mutate all cron entries.
