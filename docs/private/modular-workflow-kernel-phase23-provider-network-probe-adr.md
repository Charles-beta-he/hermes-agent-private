# Modular Workflow Kernel Phase 23 ADR: Provider / Network Probe Ledger

## Status

Accepted — Phase 23 adds a bounded, unauthenticated provider/network probe ledger to explain cron provider-generation degradation without burning model calls or mutating scheduler state.

## Context

Phase 22 classified cron failures as:

```text
provider_generation: 7
script_network_failure: 1
telegram_delivery: 1
```

The next safe repair layer is a shared probe ledger. Cron jobs should not each independently retry expensive provider generation when the broader network/provider edge is degraded.

## Decision

Add:

```text
scripts/provider_network_probe_ledger.py
```

The probe checks only public HTTPS reachability for fixed endpoints:

- GitHub — upstream fetch visibility.
- OpenAI — default provider edge reachability.
- DeepSeek — configured provider edge reachability.
- Xiaomi/MiMo — configured provider edge reachability.
- Google Gemini — OAuth provider edge reachability.
- Telegram — delivery edge reachability.

The probe uses unauthenticated `HEAD` requests and treats `401/403/404` as `reachable`, because those statuses prove DNS, proxy, TLS, and edge routing. It does not test credentials or model generation.

## Safety Boundaries

Phase 23 explicitly does **not**:

- call LLM generation APIs with prompts;
- read/write credentials;
- mutate cron jobs;
- pause/resume/remove jobs;
- send Telegram messages;
- retry failed provider jobs;
- migrate config or run dependency fixes.

## Evidence

Current probe:

```text
status: DEGRADED_PARTIAL
probe_count: 6
classification_counts:
  reachable: 5
  tls_error: 1
```

Endpoint results:

```text
github: reachable 200
openai: reachable 421
deepseek: reachable 401
xiaomi: tls_error, UNEXPECTED_EOF_WHILE_READING
google_gemini: reachable 404
telegram: reachable 200
```

Artifacts:

```text
docs/private/workflow-kernel-provider-network-probe/provider_network_probe_20260613_phase23.json
docs/private/workflow-kernel-provider-network-probe/provider_network_probe_20260613_phase23.md
```

## Consequences

The provider-generation failures are no longer treated as one opaque class. Current evidence suggests general connectivity is mostly OK, while the Xiaomi/MiMo endpoint has a TLS/edge issue under this probe. That supports a targeted next slice instead of broad cron churn.

## Next Phase

Phase 24 should add a provider health gate consumed by cron/provider jobs:

1. Read the latest provider network probe ledger.
2. If target provider edge is degraded, skip expensive generation and emit a typed local reason.
3. If only a specific provider is degraded, allow other provider jobs to run normally.
4. For upstream ratchet, use a no-fetch fallback when GitHub reachability is degraded.
