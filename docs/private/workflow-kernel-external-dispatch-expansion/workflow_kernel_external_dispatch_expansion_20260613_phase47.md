# Workflow Kernel External Dispatch Expansion — Phase 47

Status: **EXTERNAL_DISPATCH_EXPANSION_CANDIDATE_READY**
Default safe adapter: `local_outbox_simulator`
Unattended external dispatch enabled: `False`

## Adapter options
- `local_outbox_simulator` — mode=`local_record_only`, external_side_effects=`False`, authority=`none beyond local evidence write`
- `telegram` — mode=`guarded_single_send_or_dry_run_preview`, external_side_effects=`True`, authority=`explicit target + exact message/purpose + one-send authorization`
- `lark_feishu` — mode=`guarded_task_or_message_write`, external_side_effects=`True`, authority=`explicit tenant/workspace target + object id + operation`
- `github` — mode=`guarded_issue_pr_comment_or_status_write`, external_side_effects=`True`, authority=`explicit repo + issue/pr/ref + operation`

## Policy requirements
- target_allowlist
- purpose_allowlist
- idempotency_key_schema
- duplicate_policy
- rate_limit_per_target
- dry_run_preview_required
- append_only_receipt_ledger
- compensating_action_path
- post_apply_readback
- rollback_evidence_schema

## Safe boundaries
- candidate packet only
- no send_message call
- no lark/github mutation
- no credential read/write
- no scheduler mutation
- no model generation
