# Modular Workflow Kernel Phase 30 ADR: Xiaomi/MiMo Probe Shape Calibration

## Status

Accepted — the Xiaomi/MiMo provider-health classification was recalibrated after validating the user's hypothesis that the probe request shape did not match runtime request shape.

## Context

Earlier phases classified `xiaomi` as degraded because Phase 23 probed:

```text
HEAD https://api.mimogpt.com/
```

That evidence produced:

```text
tls_error: SSL UNEXPECTED_EOF_WHILE_READING
```

But Hermes' Xiaomi provider and smoke scripts use OpenAI-compatible URLs:

```text
https://api.xiaomimimo.com/v1
https://token-plan-cn.xiaomimimo.com/v1  # configured XIAOMI_BASE_URL on this machine
```

And runtime operations append paths like:

```text
/models
/chat/completions
```

Therefore the old probe was not testing the same provider edge used by runtime.

## Decision

Change the provider network probe for Xiaomi to resolve the OpenAI-compatible runtime base URL and probe:

```text
<base_url>/models
```

Resolution order:

```text
XIAOMI_BASE_URL environment
~/.hermes/.env XIAOMI_BASE_URL
https://api.xiaomimimo.com/v1 default
```

This reads only the non-secret base URL; it does not print or use API key values.

Also fix the two opt-in wrappers to read the actual generated health-gate report directory:

```text
docs/private/workflow-kernel-cron-provider-health-gate/
```

instead of the stale earlier path:

```text
docs/private/workflow-kernel-provider-health-gate/
```

## Evidence

Unauthenticated edge probe now returns reachable auth boundary:

```text
xiaomi classification: reachable
status_code: 401
url_host: token-plan-cn.xiaomimimo.com
url_path: /v1/models
configured_base_url_host: token-plan-cn.xiaomimimo.com
```

Health gate after recalibration:

```text
degraded_endpoints: []
decision_counts:
  retry_allowed_by_provider_health: 1
  retry_allowed_by_provider_health_manual_only: 1
```

Every-4h wrapper plan after path fix:

```text
script_args: [--skip-claude, --timeout, 45]
```

No Xiaomi exclusion remains because the current provider edge probe is reachable.

## Consequences

Benefits:

- Xiaomi/MiMo is no longer falsely quarantined due to a retired/stale host probe.
- Health gate decisions now reflect the configured runtime base URL.
- Wrappers no longer consume stale health-gate artifacts.

Remaining boundary:

- `401 reachable` proves DNS/TLS/routing and auth boundary reachability only.
- It does not prove the API key is valid, model generation succeeds, quota exists, or thinking-mode replay semantics are correct.

## Verification

```text
py_compile provider probe + wrappers + health gate: PASS
focused tests: 22 passed, 0 failed
Xiaomi probe: reachable 401 on token-plan-cn.xiaomimimo.com/v1/models
health gate: degraded_endpoints=[]
every4h plan: base args only, no model generation
```

## Next Phase

Phase 31 should either observe the next scheduled run or add a stale-gate detector. Do not equate unauthenticated provider reachability with credential/model-generation health.
