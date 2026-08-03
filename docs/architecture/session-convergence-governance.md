# Session Convergence and Lifecycle Governance

Status: implementation specification
Scope: Hermes Agent core runtime and local configuration

## Objective

Keep legitimate iterative execution, relay, compaction, and subagent fan-out while bounding the complete execution graph, detecting non-progress, finalizing session state on every terminal path, and reclaiming provider subprocesses deterministically.

## Source facts and boundaries

Observed local evidence before implementation:

- A long-running session made 868 model calls, reached the per-turn iteration ceiling 23 times, emitted 26 relay markers, and still produced at least 13 attributable commits.
- Prompt-cache reuse was healthy; excess cost came primarily from too many calls and repeated certification, not cache failure.
- `ConversationTurnContext` resets `agent.iteration_budget` on every root turn.
- Auto-relay has a per-agent relay counter, but a fresh/root turn can start a new budget epoch.
- `delegate_task` creates an independent child iteration budget and copies the parent's `auto_relay_enabled` and `max_relay_count`, multiplying the parent budget by fan-out and child relays.
- `AIAgent.close()` finalizes the SQLite session row but does not close `_codex_session`; `CodexAppServerClient.close()` can terminate its direct child but cannot help if it is never called.
- Some historical SQLite rows retained `ended_at IS NULL` after useful activity had stopped. This is lifecycle evidence, not proof that those sessions were still executing.
- The referenced X post is verifiably titled “Graph Engineering with Claude: 14-Step roadmap from 0 to graph architect (Full Course)” and contains a roughly 39-minute video. Until its full content can be independently extracted, it is inspiration only, not an authoritative requirement or evidence source.

## Non-goals

- Do not disable expected loops, relay, compaction, or delegation globally.
- Do not weaken exact-tree certification, independent review, fail-closed security gates, or immutable successor rules.
- Do not equate cache-read tokens with uncached billable input.
- Do not kill unrelated OS processes or clean historical orphan processes as part of source-code tests.
- Do not add external taskpool, scheduler, message, deploy, publish, or payment side effects.
- Do not make transient tool/test RED results count as progress without durable evidence.

## Root-cause model

### RC1 — local counters were mistaken for graph budgets

`max_iterations` bounds one turn. `max_relay_count` bounds one in-memory relay chain. Neither bounds the complete parent/child/continuation execution graph. Root turns, compression transitions, and independently-budgeted children create new counter domains.

Prevention invariant: every model call in a governed execution graph must consume one monotonic cumulative budget shared by parent and delegated children; root-turn reset must never refund it.

### RC2 — child policy inheritance is expansive

Children inherit auto-relay and receive fresh iteration budgets. Fan-out therefore multiplies both calls and continuation opportunities.

Prevention invariant: child auto-relay is disabled by default and may only be enabled by explicit delegation configuration; children share the parent cumulative budget unless an explicit isolated scope is configured.

### RC3 — progress was measured by activity, not novelty

Tool calls, packet rewrites, old candidate hashes, repeated test output, and repeated review decisions all look active. The runtime had no bounded seen-set or consecutive no-novelty breaker.

Prevention invariant: progress is a bounded, canonical digest over durable evidence classes. Repetition does not reset the stall counter. Expected transient RED results remain observable but do not imply convergence.

### RC4 — teardown ownership was incomplete

The Codex app-server session is lazily owned by `AIAgent`, but `AIAgent.close()` did not close it. Lifecycle finalization and provider teardown therefore had different owners and guarantees.

Prevention invariant: the object that owns a provider session closes it idempotently before finalizing its database row; cleanup failures are logged and do not skip later cleanup steps.

### RC5 — terminal reasons were not uniformly observable

A first-reason-wins database API exists, but callers could omit close/finalization or silently swallow teardown errors, leaving rows that look active and making root-cause analysis ambiguous.

Prevention invariant: every governed terminal outcome emits a structured reason; `close()` remains idempotent and first-reason-wins; persistence failure is observable without changing user-visible success.

## Chosen path

1. Add a thread-safe cumulative graph budget, distinct from the existing per-turn budget.
2. Initialize it once per agent graph and share it with delegated children.
3. Add explicit config controls with conservative compatibility defaults and an operator-enabled production profile.
4. Stop auto-relay when the graph budget is exhausted; never reset this budget in `ConversationTurnContext`.
5. Add a bounded novelty tracker and a configurable consecutive-stall breaker. Only stable evidence classes reset it.
6. Disable child auto-relay by default; allow explicit opt-in and an explicit child relay cap.
7. Close Codex app-server sessions in both soft client release and hard agent close paths, with idempotent readback tests.
8. Keep SQLite first-reason-wins semantics and add tests for close, compression handoff, duplicate close, and cleanup failure.
9. Emit structured events/logs for graph budget, stall breaker, child relay policy, lifecycle end reason, and subprocess teardown.

## Business rules

- Per-turn iteration budget remains unchanged and resets each root turn.
- Cumulative graph budget is monotonic and never reset by root turns, auto-relay, or compaction.
- `0` disables cumulative graph admission for backward compatibility. All-history stall tracking is also disabled unless a positive cumulative cap is configured, preserving bounded memory.
- A model call must acquire both budgets. If either cannot be acquired, no call is made.
- Durable graph reservation commit is a pre-dispatch gate. A rejected or exceptional commit retains the owner-bound receipt and blocks provider dispatch; only a confirmed commit consumes local refund authority.
- A refunded non-call attempt refunds every budget it consumed.
- Governed delegated children always consume the same durable graph capacity. Each child uses a receipt-isolated budget handle over the shared ledger.
- `delegation.share_graph_budget: false` is retained only as a compatibility input and is ignored with a warning for governed graphs; descendants cannot mint independent capacity.
- Child auto-relay defaults to false even when the parent has it enabled.
- Explicit child relay opt-in must have its own non-expansive cap.
- Full-history novelty fingerprints have an explicit hard capacity (`graph_fingerprint_capacity`, or a derived maximum of the diagnostic history size and positive graph-call cap). Exact hashes are never evicted: a new fingerprint at capacity halts fail-closed with `graph_history_capacity_halt`; only recent diagnostic details use the separate bounded LRU.
- Stall detection is advisory until its configured threshold is reached; threshold exhaustion terminates the turn with a structured reason and suppresses auto-relay.
- Provider teardown is idempotent and bounded; POSIX Codex app-servers start in a dedicated process group captured at spawn, and close signals the owned group `TERM → bounded wait → KILL` so nested shell/tool descendants do not survive a leader-only teardown. Every normally constructed live client is registered in a module-level strong-reference ownership set immediately after spawn; confirmed close removes it. Failed or unconfirmed shutdown is additionally marked pending and retried before the next Codex spawn, while process exit closes the union of live and pending owners. The session adapter propagates the client's confirmed-close boolean, and turn-failure/retirement paths use the same retry-aware ownership hook instead of dropping the session unconditionally. A host-level `SIGKILL` or power loss cannot execute Python cleanup; preventing survivors across that boundary belongs to the external supervisor/cgroup/job-object. Hermes never scans or broadly kills historical Codex processes.
- Session end reason is first-write-wins.

## Negative cases

- A new root turn must not restore cumulative graph budget.
- Auto-relay must not run after graph-budget or stall exhaustion.
- Three children must not each receive a fresh graph budget when shared scope is enabled.
- A parent's relay setting must not silently enable relay for a child.
- Repeated identical hashes, test summaries, or review votes must not count as new progress.
- Changing only timestamps, volatile request/trace identifiers (including snake_case/camelCase aliases), packet formatting, or one or more random directory segments under an ephemeral temp root—including pytest, Node test-runner, Jest, and Vitest directory forms—whether in tool arguments or results, must not count as durable novelty; material path leaves and non-temporary business paths remain distinct.
- `AIAgent.close()` called twice must not signal or kill twice.
- A Codex close exception must not prevent HTTP client cleanup or SQLite finalization.
- Compression ownership handoff must not end the successor session.

## Compatibility and rollback

- New controls are configuration-gated.
- Defaults preserve the existing per-turn limit while making child relay non-expansive.
- Operators can set `convergence.max_graph_iterations: 0` to disable cumulative admission; this also disables all-history stall tracking. Per-turn warnings and hard-stop controls remain independently configurable.
- Durable graph accounting uses the versioned SQLite tables `execution_graph_budgets`, `execution_graph_reservations`, and `session_execution_graphs`. Every reservation persists a per-handle `owner_id`; commit/refund requires the receipt, graph, owner, and reserved state to match, so leaked receipt IDs cannot be reconciled by siblings. Generic schema reconciliation adds `owner_id` to legacy tables with the fail-closed empty default: historical ownerless reserved rows remain charged and cannot be claimed by a new handle. The ledger survives agent-object reconstruction and process resume; reopening a graph may tighten but never expand its original cap.
- Rollback is configuration-first: set cumulative and graph-stall limits to `0`. The additive SQLite tables can remain dormant; removing them is not required for behavioral rollback.
- Every source change is scoped and revertible as an independent commit.

## Acceptance evidence

A+ closure requires all of the following fresh evidence:

1. RED-first unit tests for graph budget, root-turn reset immunity, child sharing, child relay default, stall breaker, and Codex teardown.
2. Focused tests pass after implementation.
3. Existing auto-relay, delegation, session lifecycle, and Codex app-server test suites pass.
4. Full relevant test shard and static/compile checks pass.
5. Fault injection proves cleanup continues when Codex close raises.
6. Runtime readback proves configured limits are loaded and no new provider subprocess remains after a bounded test agent closes.
7. Independent architecture and adversarial reviews return no unresolved P0/P1.
8. A Converge matrix maps every rule and negative case above to code plus test/log/readback evidence.
9. Git status, diff, commit hashes, and final tests are read back from the exact tree.

A+ must not be claimed if any item is absent. UI/uIMax acceptance is not applicable because this scope has no UI change.
