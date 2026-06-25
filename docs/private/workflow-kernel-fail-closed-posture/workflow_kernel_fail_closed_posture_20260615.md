# Workflow Kernel Fail-Closed Posture

Status: `FAIL_CLOSED_POSTURE_ENABLED`
Fail-closed enabled: `true`
Digest: `ccbdd3125426799ceef4a692e43a5bf58f0941658af26a5c8bde2b2a880d6101`

## Safe defaults
- `external_side_effects_enabled` = `false`
- `unattended_dispatch_enabled` = `false`
- `publication_apply_enabled` = `false`
- `generic_adapter_write_through_enabled` = `false`
- `taskpool_enqueue_enabled` = `false`
- `executor_enabled` = `false`

## Next boundary

fail-closed is active; any real send/publication/adapter apply/task enqueue still requires exact target, payload digest, idempotency, rollback, and human authorization
