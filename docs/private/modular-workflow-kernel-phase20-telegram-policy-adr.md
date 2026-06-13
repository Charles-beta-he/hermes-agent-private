# Modular Workflow Kernel Phase 20 ADR: Governed Telegram Notification Policy

## Status

Accepted — automated/repeated Telegram sends remain disabled; a governed policy + dry-run request compiler + receipt ledger is landed.

## Context

Phase 19 proved the Telegram `send_message` path with one explicitly authorized smoke-test message. The next production-grade step is not to send more messages; it is to add the governance needed before unattended sends: allowlist, idempotency, rate limits, dry-run preview, receipt ledger, and correction path.

## Decision

Add a Telegram notification governance layer:

```text
Telegram smoke-test receipt
  → Telegram notification policy
  → governed dry-run request compiler
  → idempotency/rate-limit checks
  → receipt ledger
  → correction/rollback model
  → STOP before unattended real send
```

The executor script `scripts/workflow_runtime_telegram_notification_policy.py` does **not** send Telegram messages. It emits a `send_instruction` for a future explicit `send_message` tool action and records receipts when provided with a real tool result.

## Policy

Policy artifact:

```text
config/telegram_notification_policy.json
```

Key invariants:

```text
unattended_send_enabled = false
allowlist = telegram home channel only
max_sends_per_window = 3 / 3600s / target_chat
idempotency duplicate_policy = block_duplicate_send_and_return_existing_receipt
rollback = compensating_message_only
```

## Verification

Run:

```bash
python3 scripts/workflow_runtime_telegram_notification_policy.py --write --json \
  --purpose mission_stage_report \
  --message '【Hermes 预览】Phase 20 governed telegram policy dry-run preview；不发送。'

./scripts/run_tests.sh tests/scripts/test_workflow_runtime_telegram_notification_policy.py \
  tests/scripts/test_workflow_runtime_external_apply_local.py \
  tests/scripts/test_workflow_runtime_external_apply_contract.py \
  tests/scripts/test_workflow_contract_validator.py
```

Observed:

```text
policy_status = dry_run_ready
unattended_send_enabled = false
telegram_policy_schema = PASS
22 tests passed
selfcheck = PASS
proof-check = PASS
```

## Next boundary

To enable automated sends, a future phase must explicitly choose the send class and authority:

```text
manual send only | mission-stage-report sends | alert sends | scheduled sends
```

and set target allowlist/rate/idempotency/correction requirements for that class. This phase intentionally stops before enabling unattended sends.
