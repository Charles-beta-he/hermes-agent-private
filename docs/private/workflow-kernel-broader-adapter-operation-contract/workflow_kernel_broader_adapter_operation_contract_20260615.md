# Broader Adapter Operation Contract

Status: `BROADER_ADAPTER_OPERATION_CONTRACT_READY`

Digest: `bd421fad35dfdcd7c60cb844fd641faeb2f5e50efbd88315ba92514b3c440b53`

## Current supported operations
- `taskpool_note.closeout_status_write` — implemented_narrow external_side_effect=False
- `kanban_db.closeout_status_write` — implemented_narrow external_side_effect=False

## Candidate operations locked
- `github.issue_or_pr_mutation` requires: exact repo/issue/pr id + payload digest + rollback/comment plan
- `lark.task_or_doc_mutation` requires: project profile boundary + exact object id + bot/user-token authority + rollback
- `telegram.notification_send` requires: exact allowlisted target + payload digest + idempotency + compensation
- `publication_target.external_publication_apply` requires: exact target + payload digest + rollback + blast-radius choice
- `generic_workflow.arbitrary_write_through` requires: not allowed without new adapter-specific contract

## Invariants
```json
{
  "credential_read_required": false,
  "external_side_effects_enabled": false,
  "no_generic_adapter_write_through": true,
  "publication_apply_enabled": false,
  "task_lifecycle_mutated_by_this_report": false,
  "unattended_dispatch_enabled": false
}
```
