# Modular Workflow Kernel Phase 19 ADR: Telegram Dispatch Smoke Test

## Status

Accepted — Telegram notification dispatch was explicitly authorized by the user and executed as a one-message system test.

## Context

Phase 18 stopped before external dispatch and retained only a local outbox simulator record. The user then said: "接TG消息发送，系统测试". This is a concrete authorization for a Telegram message send smoke test to the current/home Telegram channel.

## Decision

Execute exactly one external side effect:

```text
target_adapter = telegram_notification
operation = send_system_test_message
target = telegram home channel / current user chat
```

No Lark/GitHub/external task mutation is included in this authorization.

## Real receipt

The `send_message` tool returned:

```text
success: true
platform: telegram
chat_id: 5524172309
message_id: 4209
note: Sent to telegram home channel (chat_id: 5524172309)
```

## Safety and rollback

Telegram delivery cannot be truly rolled back from Hermes as a data mutation. The rollback strategy is social/compensating only: send a correction/retraction message if needed. No correction was needed for this test.

## Artifacts

- Receipt: `docs/private/workflow-kernel-runtime-external-dispatch/tdisp_4209.runtime_external_apply_receipt.json`
- Rollback evidence: `docs/private/workflow-kernel-runtime-external-dispatch/tdisp_4209.rollback_evidence.json`
- Dispatch packet: `docs/private/workflow-kernel-runtime-external-dispatch/tdisp_4209.telegram_dispatch_packet.json`

## Verification

```text
send_message success = true
message_id = 4209
receipt schema validation = PASS
rollback schema validation = PASS
selfcheck = PASS
proof-check = PASS
```

## Next boundary

A single TG smoke test is complete. Any future automatic or repeated notification dispatch still needs a rate/target/purpose policy before enabling unattended sends.
