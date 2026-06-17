# Telegram reliable delivery: competitor / enterprise production patterns

Date: 2026-06-18
Scope: Hermes Gateway Telegram outbound reply recovery after network/proxy turbulence.
Target: derive a production-grade design from public competitor/enterprise messaging reliability patterns, then map it into Hermes without silently expanding privacy or destructive side effects.

## Objective and constraints

Success criteria:
- A final assistant reply generated during Telegram/proxy turbulence is not silently lost.
- Retry/recovery behavior is explicit, bounded, observable, and testable.
- Duplicate sends are prevented or at least detectable.
- Persistent storage of user/model content is opt-in or explicitly governed.
- Existing Telegram polling, streaming, cron sends, DM/topic routing, and fallback behavior are preserved.

Hard constraints:
- Do not store secrets.
- Do not persist private message content by default without an explicit policy gate.
- Do not retry ambiguous Telegram send timeouts as if they are safe: the API call may have reached Telegram and duplicate delivery is possible.
- Keep current low-risk hotfixes working: immediate `get_me()` recovery probe and 10/30/60s degraded-send window.

## Public evidence from comparable systems

### Stripe: idempotency keys for safe retries

Source: https://docs.stripe.com/api/idempotent_requests
Observed evidence from docs:
- Stripe says idempotency lets clients retry after connection errors without accidentally performing the same operation twice.
- Keys may be up to 255 characters.
- Stripe recommends avoiding sensitive data in idempotency keys.
- Keys can be removed automatically after at least 24 hours.
- The idempotency layer compares incoming parameters and errors if reused inconsistently.

Transferable pattern:
- Hermes outbound messages need an idempotency/dedupe key that contains no sensitive content.
- Key shape should be derived from stable metadata and a content hash, not raw content.
- TTL should exist; 24h is a known public precedent, but Hermes can choose shorter defaults for privacy.

### Slack Events API: bounded retries + delayed retry option

Source: https://api.slack.com/apis/connections/events-api#retries
Observed evidence from docs:
- Slack retries failed event delivery three times over a few minutes.
- Retry reason is exposed in `x-slack-retry-reason`.
- Slack has a Delayed Events option that follows with hourly retries for 24 hours.
- Slack describes event delivery as best-effort and may delay during incidents.

Transferable pattern:
- The default path should have short bounded retries.
- A stronger production mode should have a longer delayed retry tier.
- Retry reason/status should be surfaced to operators, not only buried in logs.

### AWS SQS DLQ: retry budget then dead-letter queue

Source: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
Observed evidence from docs:
- SQS supports dead-letter queues for messages that cannot be consumed successfully.
- `maxReceiveCount` should be high enough to allow sufficient retries.
- Failed items move out of the hot path after the retry budget is exhausted.

Transferable pattern:
- Hermes outbox should separate `pending`, `sent`, and `dead_letter` states.
- Exhausted delivery attempts should not loop indefinitely in the hot path.
- Operator commands/projections should make dead-letter items inspectable and redeliverable.

### Temporal: activities have retry policies; workflows stay deterministic

Source: https://docs.temporal.io/encyclopedia/retry-policies
Observed evidence from docs:
- Temporal activities are intended for operations that may fail and have built-in retry support.
- Retry policies include initial interval, backoff coefficient, maximum interval, maximum attempts, and non-retryable errors.
- Temporal guidance separates deterministic workflow state from failure-prone external operations.

Transferable pattern:
- Hermes should model “generate reply” and “deliver reply” as separate phases.
- Delivery is an external side effect with retry policy and state; it should not force regeneration of the assistant reply.
- Retry policy should classify non-retryable vs retryable failures.

### Twilio webhooks: configurable retries, total timeout, idempotency token

Source: https://www.twilio.com/docs/usage/webhooks/webhooks-connection-overrides
Observed evidence from docs:
- Twilio exposes retry count, retry policy, total timeout, and connection/read timeouts.
- Twilio includes `I-Twilio-Idempotency-Token`, a unique string to distinguish retry attempts.
- Real-time call paths have hard timeout ceilings for user experience.

Transferable pattern:
- Hermes should make delivery retry ceilings configurable by platform/path.
- Retry attempts should carry a stable attempt/delivery identifier in logs/outbox state.
- There must be a UX ceiling; do not silently retry forever.

### GitHub webhooks: failed delivery visibility and manual/scripted redelivery

Source: https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries
Observed evidence from docs:
- GitHub does not automatically redeliver failed webhook deliveries.
- Failed deliveries can be manually redelivered or handled by code that checks failures and redelivers.
- Failures are visible through delivery records.

Transferable pattern:
- Even if Hermes chooses not to auto-redeliver content by default, it should record a redelivery candidate with inspectable status.
- Manual redelivery is a valid production pattern when privacy/duplicate risk is high.

### Telegram Bot API: 24h inbound update retention, retry_after, duplicate update offsets

Source: https://core.telegram.org/bots/api
Observed evidence from docs:
- Telegram inbound updates are stored on the server until received but not longer than 24 hours.
- `retry_after` is provided in some flood-control responses.
- Telegram getUpdates guidance says to recalculate offset to avoid duplicate updates.

Transferable pattern:
- Hermes can align default pending TTL with Telegram’s 24h update window, or choose a shorter privacy-preserving default.
- Flood-control responses should honor `retry_after` where available.
- Deduplication must be explicit because Telegram-side and Hermes-side retries can overlap.

## Hermes production target model

Layer 0 — already landed low-risk recovery:
- On polling reconnect: immediate bounded Bot API probe clears `_send_path_degraded`.
- On send while degraded: one preflight probe self-heals if Bot API is back.
- Base retry wrapper gives `send_path_degraded` a longer 10/30/60s recovery window before failure notice.

Layer 1 — production outbox contract:
- Store a delivery envelope before attempting a final outbound send.
- Envelope fields:
  - `delivery_id`: random UUID.
  - `dedupe_key`: HMAC/hash of platform, chat_id, thread/session id, content hash, and logical turn id; no raw content in the key.
  - `platform`, `chat_id`, optional `thread_id`, optional `reply_to`, `session_key`.
  - `content_ref`: either encrypted content blob reference or in-memory-only marker depending policy.
  - `content_sha256`: for dedupe/readback without exposing content.
  - `status`: pending | sending | sent | dead_letter | expired | suppressed_duplicate.
  - `attempt_count`, `last_error`, `next_attempt_at`, `created_at`, `expires_at`, `sent_message_id`.
  - `policy`: content_storage=none|plaintext-local|encrypted-local, auto_redeliver=true|false.

Layer 2 — delivery semantics:
- Safe retry classes:
  - connection failed before request / `send_path_degraded` / connect timeout: retryable.
  - Telegram flood control with `retry_after`: retry at/after `retry_after`.
  - read timeout / ambiguous timeout: do not blind retry unless dedupe/idempotency proof exists; mark `needs_manual_or_probe`.
  - BadRequest/Forbidden/permanent auth: non-retryable dead_letter.
- Auto-redelivery default should be conservative:
  - If content storage is disabled: record metadata-only failure and notify user if possible.
  - If content storage is enabled: retry within TTL and suppress duplicates via dedupe key.

Layer 3 — operator surface:
- CLI/status projection:
  - list pending/dead_letter counts by platform/chat age.
  - inspect metadata with content redacted by default.
  - manual redeliver by delivery_id only after confirmation.
- Logs:
  - include delivery_id, dedupe_key prefix, attempt_count, retry_reason, next_attempt_at.
- Tests:
  - TDD for dedupe, TTL expiry, retry classification, dead_letter transition, manual redelivery, crash/restart drain.

## Recommended implementation sequence

1. Land metadata-only outbox scaffold, default enabled for recording but content_storage=none.
   - No privacy boundary crossed: it does not store message text.
   - Value: operators can see “a final reply existed but content was not persisted by policy”.

2. Add optional encrypted/plaintext-local content storage behind explicit config.
   - This is the human gate.
   - Required to truly auto-redeliver generated replies after process crash or long outage.

3. Add outbox drain loop and manual redelivery command.
   - Auto-redeliver only when content storage policy allows it.

4. Add observability and post-update check.
   - py_compile and focused pytest.
   - optional watchdog for growing dead_letter backlog.

## Human decision boundary

To implement full automatic pending outbox redelivery, Hermes needs an explicit content persistence policy:

A. Metadata-only outbox, no content persistence.
- Safest privacy default.
- Cannot auto-resend after process crash because reply body is not stored.
- Can still expose failure and ask user to resend.

B. Plaintext local outbox with TTL.
- Most practical local personal-assistant mode.
- Stores assistant replies and possibly user-visible generated content under `~/.hermes/state/` for up to TTL.
- Needs explicit approval.

C. Encrypted local outbox with TTL.
- Better long-term enterprise pattern.
- Requires key management decision; do not invent weak crypto silently.

Default recommendation: land A now; stop before B/C unless user authorizes content persistence and TTL.
