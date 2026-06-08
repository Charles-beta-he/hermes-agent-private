# Agent Architecture Coverage Plan — 2026-06-08

Goal: turn the prior Claude Code / Hermes agent-architecture coverage discussion into a Hermes-owned, evidence-grounded control-plane artifact and land the first safe implementation slices until the next boundary requires runtime authority or human approval.
Status: in_progress
SSOT: this file

## Scope

- Codify the architecture coverage matrix as Hermes report-only control-plane contract data.
- Keep Hermes as canonical ledger / policy / evidence / certification control plane.
- Treat Claude Code / Codex / OpenCode / delegate workers as bounded execution adapters, not new fact sources.
- Land low-risk deterministic/report-only slices first; do not enable broad worker execution, lifecycle mutation, external writes, or automatic done.

## Slices

- [x] Slice 0: Establish SSOT and boundary file — docs/private/agent-architecture-coverage-plan-20260608.md
- [x] Slice 1: Add report-only agent architecture coverage contract to hermes_cli.event_governed_agents with CLI JSON access and tests.
- [x] Slice 1b: Add worker adapter result contract so Claude Code/Codex/OpenCode outputs are evidence/proposal only and cannot smuggle lifecycle/external-write authority.
- [x] Slice 2: Run focused tests and py_compile for the touched control-plane module.
- [x] Slice 3: Scoped commit if verification passes. Commit: 06fbbe238 Add agent architecture coverage contract.
- [x] Slice 4: Add fork/join graph contract validation as ledger projection only; runtime parallel dispatch remains disabled.
- [x] Slice 5: Add multi-model certification packet validation; model consensus remains non-fact and cannot mark done.
- [x] Slice 6: Scoped commit for graph/certification contracts if verification passes. Commit: 58f086b5f Add agent workflow graph certification contracts.
- [x] Slice 7: Add artifact pointer contract so evidence remains summary/hash/pointer only in the hot ledger.
- [x] Slice 8: Add workflow metadata contract for lane/workflow_state/checkpoint substate without lifecycle authority.
- [x] Slice 9: Scoped commit for artifact/workflow metadata contracts if verification passes. Commit: 268e63de0 Add agent artifact workflow metadata contracts.
- [x] Slice 10: Add first real started runtime boundary: ledger-only bounded run start with durable JSONL record, idempotent retry, no external executor invocation, no lifecycle mutation, no adapter write-through.
- [x] Slice 11: Scoped commit for ledger-only runtime start if verification passes. Commit: 9a5440557 Start bounded agent runtime ledger runs.
- [x] Slice 12: Add first governed executor invocation: allowlisted Hermes builtin read-only executor completes a started run and appends result evidence to ledger; unknown/external executors remain rejected.
- [x] Slice 13: Scoped commit for builtin read-only executor if verification passes. Commit: 747f419d9 Execute bounded builtin agent runtime runs.
- [x] Slice 14: Add first external process boundary: allowlisted Claude/Codex/OpenCode-style agent version probe appends a receipt to ledger; no task prompt/tool execution/lifecycle/adapter write.
- [x] Slice 15: Scoped commit for external agent version probe if verification passes. Commit: df9fbc6bb Probe external agent runtime executors.
- [x] Slice 16: Add first external task-prompt boundary: allowlisted Claude/Codex/OpenCode-style agent receives a no-tool/no-write dry-run prompt, full output is captured as an artifact, and only artifact pointer/hash/summary enters the ledger.
- [x] Slice 17: Scoped commit for external prompt dry-run if verification passes. Commit: 9f2bda9b5 Dry run external agent prompt execution.
- [x] Slice 18: Add external readonly context packet: allowlisted agent receives a no-tool/no-write prompt that references a file artifact by URI/hash; inline context is rejected and ledger stores only context/output artifact pointers.
- [x] Slice 19: Scoped commit for external readonly context packet if verification passes. Commit: 5da09a457 Attach readonly context to external agent runs.
- [x] Slice 20: Add external proposal artifact intake: worker proposals are accepted only as review_proposed artifacts with evidence-backed claims and failure classification; done/lifecycle authority remains rejected.
- [x] Slice 21: Scoped commit for proposal artifact intake if verification passes. Commit: 95c20b13b Record external agent proposal artifacts.
- [x] Slice 22: Add multi-model certification promotion packet: certification artifacts must bind to a recorded proposal, require quorum/truth gate/reviewer evidence/claim evidence, and may only target certification_passed_for_review.
- [x] Slice 23: Scoped commit for certification promotion packet if verification passes. Commit: 1146f643a Record multi-model certification promotions.
- [x] Slice 24: Add human/elevated done authorization projection: require certification_recorded, elevated actor, approval decision, target done, TTL, and certification artifact hash binding while keeping fact mutation/write-through disabled.
- [x] Slice 25: Scoped commit for done authorization projection if verification passes. Commit: 2e909860e Project elevated done authorizations.
- [x] Slice 26: Execute ledger-local canonical fact mutation: consume unexpired approved done authorization, rebind certification hash, append canonical_lifecycle_status=done fact, and keep external adapters disabled.
- [x] Slice 27: Scoped commit for ledger-local fact mutation if verification passes. Commit: 8b41b7426 Execute ledger-local canonical fact mutations.
- [x] Slice 28: Add external adapter write intent projection: require canonical done fact, adapter allowlist, readiness check, readback plan, rollback plan, and evidence while keeping write-through disabled.
- [x] Slice 29: Scoped commit for adapter write intent projection if verification passes. Commit: 54431a916 Project external adapter write intents.
- [x] Slice 30: Add external adapter write authorization projection: require recorded intent, elevated actor, approval decision, TTL, target/digest unchanged, and evidence while still not executing write-through.
- [x] Slice 31: Scoped commit for adapter write authorization projection if verification passes. Commit: 00e039a8f Project external adapter write authorizations.
- [x] Slice 32: Add narrow local-file adapter write-through executor: consume unexpired write authorization, update adapter_state_path status=done, read back, record verified/mismatch, and keep real taskpool/Kanban/Lark/Telegram adapters disabled.
- [x] Slice 33: Scoped commit for local-file adapter executor if verification passes. Commit: 9f007e480 Execute local-file adapter write through.
- [x] Slice 34: Add taskpool note closeout write-through executor: consume unexpired taskpool authorization, reject path/symlink escapes, mutate only frontmatter status to done, append audit marker, read back status.
- [x] Slice 35: Scoped commit for taskpool note closeout executor if verification passes. Commit: 87ac0f889 Execute taskpool note closeout writes.
- [x] Slice 36: Add Kanban DB closeout write-through executor: consume unexpired kanban authorization, reject DB symlink/missing-table/missing-task drift, mutate only `tasks.status` to done, read back status.
- [x] Slice 37: Scoped commit for Kanban DB closeout executor if verification passes. Commit: 5c359c1b6 Execute Kanban DB closeout writes.
- [x] Slice 38: Add digest-bound rollback executor for taskpool/Kanban: consume verified write event by request digest, restore recorded previous_status, read back, and remain idempotent.
- [x] Slice 39: Scoped commit for rollback executor if verification passes. Commit: 1255142ea Rollback taskpool and Kanban closeouts.
- [x] Slice 40: Add outbound outbox enqueue executor: consume verified write/rollback event, append pending Lark/Telegram notification intent with payload hash/summary only, require target allowlist and no secret-like payload keys, and do not send.
- [x] Slice 41: Scoped commit for outbound outbox executor if verification passes. Commit: 50e9dbe82 Enqueue governed outbound outbox items.
- [x] Slice 42: Add outbound delivery authorization projection: consume pending outbox item, require elevated actor approval, exact outbox_id/payload_hash/target/channel, TTL, readback, and do not send.
- [x] Slice 43: Scoped commit for outbound delivery authorization if verification passes. Commit: 08be51b97 Project outbound delivery authorizations.
- [x] Slice 44: Add Telegram sender dry-run executor: consume delivery authorization, require channel=telegram and sender_mode=dry_run, verify outbox/payload hash, record platform payload hash/summary only, and do not read token or send.
- [x] Slice 45: Scoped commit for Telegram sender dry-run if verification passes. Commit: 7a2bd2ded Dry run Telegram outbound delivery.
- [x] Slice 46: Add Lark sender dry-run executor: consume delivery authorization, require channel=lark and sender_mode=dry_run, verify outbox/payload hash, record platform payload hash/summary only, and do not read token or send.
- [x] Slice 47: Scoped commit for Lark sender dry-run if verification passes. Commit: 422752530 Dry run Lark outbound delivery.
- [x] Slice 48: Add real sender preflight: consume Telegram/Lark dry-run, require exact delivery/outbox/payload/platform hash, fresh dry-run, token source reference, and sender capability still disabled; do not read token or send.
- [x] Slice 49: Scoped commit for real sender preflight if verification passes. Commit: 50177d0d6 Preflight real outbound senders.
- [x] Slice 50: Add real sender execution authorization: consume real sender preflight, require elevated second approval, exact preflight/channel/target/payload/platform hash/token source, requested sender capability, TTL, and evidence; do not read token or send.
- [x] Slice 51: Scoped commit for real sender execution authorization if verification passes. Commit: 46c237a27 Authorize real outbound sender execution.
- [x] Slice 52: Add Telegram real sender executor: consume real sender execution authorization, require exact authorization/preflight/outbox/payload/platform hash/token source, read env token, call Telegram sendMessage, record platform message id, and never store token value.
- [x] Slice 53: Scoped commit for Telegram real sender executor if verification passes. Commit: this scoped commit.
- [ ] Slice 54: Add Lark real sender executor with equivalent authorization/token/API/message-id governance.
- [ ] Boundary: stop before external agent tools/file edits/shell commands, broad reopen, or unauthenticated sender execution unless a narrower executor-specific slice authorizes that capability.

## Decisions

- Report-only first: this captures architecture coverage and gap priorities without granting new authority.
- Canonical ledger remains Hermes Control Tower / taskpool by fact domain; worker/model outputs are proposals/evidence only.
- Parallelism/fork-join, model routing, and worker adapters should be expressed as contracts before execution.
- The first approved runtime boundary is ledger-only start: it may append a durable run record and support idempotent retry, but it still must not invoke external executors, mutate lifecycle, write adapters, or persist learning.
- The second approved runtime boundary is a governed builtin read-only executor: it may append a completed run result after verifying a started run, but external agent process invocation remains disabled until a narrower executor-specific slice exists.
- The third approved runtime boundary is external process probing only: allowlisted agent binaries may be invoked with `--version` and their receipt appended, but no task prompt is sent and no external worker is allowed to act on project/task state yet.
- The fourth approved runtime boundary is external prompt dry-run only: an allowlisted agent may receive a prompt with tools disabled and permission mode constrained; stdout/stderr are stored as an artifact while the ledger stores only pointer/hash/summary. The agent still cannot edit files, run shell tools, mutate lifecycle, or write adapters.
- The fifth approved runtime boundary is external readonly context packet: an allowlisted agent may receive a prompt containing only a context artifact URI/hash, while inline context and write/tool/lifecycle authority remain rejected. Ledger stores context pointer/hash and output artifact pointer/hash/summary only.
- The sixth approved runtime boundary is external proposal artifact intake: an external worker proposal may be recorded only as a review_proposed artifact with evidence-backed claims and failure classification. It is still not a fact, cannot mark done, and requires later certification/human gate.
- The seventh approved runtime boundary is multi-model certification promotion: a certification artifact may be recorded only if it binds to the recorded proposal, has at least two reviewers, quorum >= 2, truth gate enabled, reviewer/claim evidence, and lifecycle_target=certification_passed_for_review. It still cannot mark done or mutate lifecycle.
- The eighth approved runtime boundary is human/elevated done authorization projection: an elevated human/admin may approve target_status=done only against the exact certification artifact hash and unexpired TTL, but this projection still keeps fact mutation and write-through disabled.
- The ninth approved runtime boundary is ledger-local canonical fact mutation: a valid unexpired done authorization may be consumed to append canonical_lifecycle_status=done as a durable fact in the local ledger. External adapter write-through remains disabled and requires a separate slice.
- The tenth approved runtime boundary is external adapter write intent projection: a canonical done fact may be mapped to an allowlisted adapter target only as an intent with readiness/readback/rollback plans and evidence. It still cannot perform external write-through.
- The eleventh approved runtime boundary is external adapter write authorization projection: an elevated actor may approve a recorded write intent only if adapter/ref/status/canonical digest/intent digest are unchanged and TTL is valid. It still records authorization only and does not execute write-through.
- The twelfth approved runtime boundary is local-file adapter write-through execution: an unexpired write authorization may update a local adapter_state_path target status to done, then immediately read it back and record verified or mismatch. This is a harness executor only; real taskpool/Kanban/Lark/Telegram writes still require separate adapter-specific slices.
- The thirteenth approved runtime boundary is taskpool note closeout write-through: an unexpired taskpool write authorization may mutate exactly one safe task markdown note frontmatter status from the expected previous status to done, append an audit marker, and read it back. Kanban DB, Lark, Telegram, and arbitrary taskpool lifecycle operations remain disabled.
- The fourteenth approved runtime boundary is Kanban DB closeout write-through: an unexpired kanban write authorization may mutate exactly one SQLite `tasks.status` cell from the expected previous status to done, then read it back. Lark, Telegram, arbitrary SQL, schema mutation, and arbitrary Kanban operations remain disabled.
- The fifteenth approved runtime boundary is digest-bound taskpool/Kanban rollback: a verified closeout write event may be consumed by exact write request digest to restore only its recorded previous_status, with current-state check and readback. Broad reopen, Lark/Telegram writes, and arbitrary rollback targets remain disabled.
- The sixteenth approved runtime boundary is outbound outbox enqueue only: a verified write/rollback event may produce a pending Lark/Telegram outbox item with target allowlist, no secret-like payload keys, payload hash/summary only, and readback. It does not call platform APIs or send messages.
- The seventeenth approved runtime boundary is outbound delivery authorization projection: an elevated actor may approve a pending outbox item for exactly one channel/target/payload hash under TTL after outbox readback. It still does not call Lark/Telegram APIs or send messages.
- The eighteenth approved runtime boundary is Telegram sender dry-run only: an authorized Telegram delivery may validate a platform payload preview and record its hash/summary without reading tokens or sending messages. Real Telegram API execution remains disabled.
- The nineteenth approved runtime boundary is Lark sender dry-run only: an authorized Lark delivery may validate a text message payload preview and record its hash/summary without reading tokens or sending messages. Real Lark API execution remains disabled.
- The twentieth approved runtime boundary is real sender preflight only: a fresh Telegram/Lark dry-run may be bound to exact delivery/outbox/payload/platform hashes plus a token-source reference while sender capability remains disabled. It does not read tokens or send messages.
- The twenty-first approved runtime boundary is real sender execution authorization only: an elevated second approval may grant a short-lived future execution capability against an exact preflight/channel/target/payload/platform-hash/token-source tuple. It still does not read tokens or send messages.
- The twenty-second approved runtime boundary is Telegram real sender execution: an execution-authorized Telegram payload may read an env-token reference, call sendMessage, and record the platform message id, while preserving exact hash binding and never storing the token value.

## Files Changed

- docs/private/agent-architecture-coverage-plan-20260608.md
- hermes_cli/event_governed_agents.py
- tests/hermes_cli/test_event_governed_agents.py
