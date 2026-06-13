# Workflow Kernel Cron Wrapper Kernel Shim — Phase 31

Status: **KERNEL_SHIM_ACTIVE_FOR_OPT_IN_CRON_WRAPPERS**

## Scope
Move daily/every4h provider-health gate consumption into workflow_kernel.cron_wrappers while keeping wrappers as opt-in cron entrypoints.

## Switched components
- workflow_kernel/cron_wrappers.py
- ~/.hermes/scripts/multi-model-smoke-daily-full-wrapper.py
- ~/.hermes/scripts/multi-model-smoke-every4h-wrapper.py

## every4h plan readback
```json
{
  "cron_state_mutated": false,
  "degraded_endpoints_excluded": [],
  "gate_decision": null,
  "job_id": "2c14ca2a9bd2",
  "job_name": "multi-model-smoke-every-4h",
  "kernel_shim": true,
  "model_generation_attempted": false,
  "script_args": [
    "--skip-claude",
    "--timeout",
    "45"
  ],
  "selected_models": [],
  "status": "provider_health_exclusion_plan"
}
```

## Safe boundaries
- no cron/jobs.json mutation
- no global runtime switch enablement
- no scheduler rewrite
- no model generation during verification
- no credential read/write
- no external message send

## Verification
- py_compile workflow_kernel/cron_wrappers.py and wrappers PASS
- focused wrapper/kernel tests 19 passed 0 failed
- every4h plan-only readback includes kernel_shim=true
