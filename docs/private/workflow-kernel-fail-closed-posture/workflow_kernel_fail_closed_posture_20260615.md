# Workflow Kernel Fail-Closed Posture

Status: `FAIL_CLOSED_POSTURE_ENABLED`
Fail-closed enabled: `true`
Digest: `bcebfe80e6eb6da2d05c6b8f781badc20d37a80c02b5be9c26165208a935a902`

## Safe defaults
- `external_side_effects_enabled` = `false`
- `unattended_dispatch_enabled` = `false`
- `publication_apply_enabled` = `false`
- `generic_adapter_write_through_enabled` = `false`
- `taskpool_enqueue_enabled` = `false`
- `executor_enabled` = `false`

## Next boundary

fail-closed is active; any real send/publication/adapter apply/task enqueue still requires exact target, payload digest, idempotency, rollback, and human authorization
