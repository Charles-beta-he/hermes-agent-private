# Workflow Kernel Pack Registry — Phase 51

Status: **PACK_REGISTRY_PROJECTION_READY**
Workflow pack: `workflow_kernel_external_dispatch_governance`
Agent pack: `agent.workflow_kernel_operator`
Modules: `4`

## Modules
- `workflow_kernel.runtime_auto_enable.v1` — layer=`runtime_switch`, risk=`local_write`, gate=`completed_phase45_full_authorization`
- `workflow_kernel.post_auto_enable_observation.v1` — layer=`runtime_observation`, risk=`read_only`, gate=`none_for_readback`
- `workflow_kernel.external_dispatch_candidate.v1` — layer=`external_dispatch_governance`, risk=`read_only`, gate=`exact_adapter_target_payload_required_for_external_effect`
- `workflow_kernel.external_dispatch_local_outbox.v1` — layer=`external_dispatch_policy_gate`, risk=`local_write`, gate=`default_safe_local_outbox_when_no_external_target`

## Phase50 guarded send requirements
- exact_adapter
- exact_target
- exact_payload
- mode
- idempotency_key
- rollback_or_correction_path
- post_send_readback

## Safe boundaries
- read-only registry projection
- no cron mutation
- no task lifecycle mutation
- no model generation
- no credential read/write
- no external message send
- no unattended dispatch enablement
