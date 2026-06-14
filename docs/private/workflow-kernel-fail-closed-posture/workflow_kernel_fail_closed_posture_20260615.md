# Workflow Kernel Fail-Closed Posture

Status: `FAIL_CLOSED_POSTURE_ENABLED`
Fail-closed enabled: `true`
Digest: `d72ad2b91abf8f94e365d1ebd0224427680031c8fc126188c70baee92b41a52f`

## Safe defaults
- `external_side_effects_enabled` = `false`
- `unattended_dispatch_enabled` = `false`
- `publication_apply_enabled` = `false`
- `generic_adapter_write_through_enabled` = `false`
- `taskpool_enqueue_enabled` = `false`
- `executor_enabled` = `false`

## Next boundary

fail-closed is active; any real send/publication/adapter apply/task enqueue still requires exact target, payload digest, idempotency, rollback, and human authorization
