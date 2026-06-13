# Workflow Kernel Xiaomi Probe Calibration — Phase 30

Status: **XIAOMI_PROBE_SHAPE_CALIBRATED_REACHABLE**

## Root cause
Phase23 provider probe used stale host api.mimogpt.com with HEAD /, while Hermes/Xiaomi runtime uses OpenAI-compatible base_url + /models or /chat/completions on api.xiaomimimo.com or configured token-plan-cn.xiaomimimo.com/v1.

## Fixes
- provider_network_probe_ledger resolves Xiaomi probe from XIAOMI_BASE_URL/default base URL and probes /models
- daily/every4h wrappers read the actual cron-provider-health-gate directory instead of stale provider-health-gate directory

## Xiaomi probe readback
```json
{
  "classification": "reachable",
  "configured_base_url_host": "token-plan-cn.xiaomimimo.com",
  "error": null,
  "method": "HEAD",
  "status_code": 401,
  "url_host": "token-plan-cn.xiaomimimo.com",
  "url_path": "/v1/models"
}
```

## Health gate readback
```json
{
  "decision_counts": {
    "retry_allowed_by_provider_health": 1,
    "retry_allowed_by_provider_health_manual_only": 1
  },
  "degraded_endpoints": [],
  "status": "GATE_COMPILED_NO_MUTATION"
}
```

## every4h wrapper plan readback
```json
{
  "cron_state_mutated": false,
  "model_generation_attempted": false,
  "script_args": [
    "--skip-claude",
    "--timeout",
    "45"
  ],
  "status": "provider_health_exclusion_plan"
}
```

## Safe boundaries
- no provider credential values printed
- no model generation during verification
- no cron/jobs.json mutation
- no external message send
- no deletion of historical evidence

## Verification
- 22 focused tests passed
- Xiaomi probe now reachable with 401 on token-plan-cn.xiaomimimo.com/v1/models
- health gate degraded_endpoints empty
- every4h wrapper plan uses base args because no degraded provider exclusion remains
