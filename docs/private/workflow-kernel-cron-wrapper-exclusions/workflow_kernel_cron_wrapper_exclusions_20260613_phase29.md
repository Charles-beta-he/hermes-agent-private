# Workflow Kernel Cron Wrapper Exclusions — Phase 29

Status: **MULTI_PROVIDER_WRAPPER_EXCLUSION_HINT_READY**
Scope: `one opt-in cron wrapper: multi-model-smoke-every4h-wrapper.py / job 2c14ca2a9bd2`
Excluded providers: `['xiaomi']`
Selected models: `['gpt-5.5', 'deepseek-v4-pro']`

## Plan-only readback
```json
{
  "cron_state_mutated": false,
  "degraded_endpoints_excluded": [
    "xiaomi"
  ],
  "gate_decision": {
    "decision": "run_with_degraded_provider_exclusions",
    "degraded_endpoints": [
      "xiaomi"
    ],
    "endpoint": null,
    "gate_report": "/Users/charles/.hermes/hermes-agent/docs/private/workflow-kernel-provider-health-gate/cron_provider_health_gate_20260613_phase24.json",
    "gate_report_id": "cron_provider_health_gate_20260613_phase24",
    "inferred_provider": "multi_provider",
    "job_id": "2c14ca2a9bd2",
    "last_status": "error",
    "model": null,
    "name": "multi-model-smoke-every-4h",
    "provider": null,
    "reason": "multi-provider job can proceed only if runtime excludes degraded providers",
    "script": "multi-model-smoke-every4h-wrapper.py"
  },
  "job_id": "2c14ca2a9bd2",
  "job_name": "multi-model-smoke-every-4h",
  "model_generation_attempted": false,
  "script_args": [
    "--skip-claude",
    "--timeout",
    "45",
    "--model",
    "gpt-5.5",
    "--model",
    "deepseek-v4-pro"
  ],
  "selected_models": [
    "gpt-5.5",
    "deepseek-v4-pro"
  ],
  "status": "provider_health_exclusion_plan"
}
```

## Safe boundaries
- no cron/jobs.json mutation
- no global scheduler mutation
- no model generation during verification
- no credentials read/write
- no external messages sent
- only every4h wrapper newly opted in

## Verification
- py_compile every4h/daily wrappers and health gate PASS
- wrapper gate tests PASS
- plan-only wrapper readback excludes mimo-v2.5-pro and keeps gpt-5.5/deepseek-v4-pro

## Next boundary
optional real scheduled run observation or scheduler-level typed projection; do not batch patch all wrappers
