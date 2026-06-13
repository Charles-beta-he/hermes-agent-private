# Workflow Kernel Pack Manifests — Phase 49

Status: **WORKFLOW_AGENT_PACK_MANIFESTS_READY**
Workflow pack: `workflow_kernel_external_dispatch_governance`
Agent pack: `agent.workflow_kernel_operator`

## Modules
- `workflow_kernel.runtime_auto_enable.v1` — layer=`runtime_switch`, risk=`local_write`, gate=`completed_phase45_full_authorization`
- `workflow_kernel.post_auto_enable_observation.v1` — layer=`runtime_observation`, risk=`read_only`, gate=`none_for_readback`
- `workflow_kernel.external_dispatch_candidate.v1` — layer=`external_dispatch_governance`, risk=`read_only`, gate=`exact_adapter_target_payload_required_for_external_effect`
- `workflow_kernel.external_dispatch_local_outbox.v1` — layer=`external_dispatch_policy_gate`, risk=`local_write`, gate=`default_safe_local_outbox_when_no_external_target`

## Boundaries
- schema/projection only
- no cron mutation
- no model generation
- no credential read/write
- no external message send
- no unattended dispatch enablement
