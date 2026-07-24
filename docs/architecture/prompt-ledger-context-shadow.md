# P0 Prompt Ledger and Context Lifecycle Shadow

Status: experimental shadow implementation; production projection not authorized
Baseline: `f59d0cba2`
Branch: `codex/hermes-token-context-p0-shadow`
Worktree: `/Users/charles/.hermes/worktrees/hermes-token-context-p0-shadow`

## Goal

Measure every provider request by prompt component and compute a deterministic counterfactual context projection for old tool and multimodal results without changing the payload sent to the provider.

## Non-goals

- No production context mutation.
- No deletion or rewriting of existing messages or databases.
- No checkpoint skipping, model routing, deployment, restart, commit, or push.
- No claim of token or cost savings from character estimates alone.

## Source facts and boundaries

- Prompt caching requires a byte-stable historical prefix; shadow logic must not mutate messages or tool schemas.
- Multimodal tool results bypass ordinary `maybe_persist_tool_result()` handling in `agent/tool_executor.py`.
- Session persistence already strips image parts in the normal incremental flush, but `archive_and_compact()` accepts arbitrary compacted content and therefore remains a separate persistence boundary.
- SQL `length(content)` is not an acceptable source for NUL-prefixed JSON/multimodal payload size.
- Existing provider behavior remains authoritative; ledger and projection failures must fail open to the unchanged request.

## P0 rules

1. Feature configuration lives under `context_projection` in `config.yaml` and defaults to disabled.
2. `mode: shadow` records the original request component sizes and the proposed projection; it never substitutes projected messages into provider kwargs.
3. All content measurements are computed from decoded Python values with deterministic JSON encoding. Chat Completions `messages` and Responses API `input`/`function_call_output` shapes are classified explicitly.
4. Multimodal content is eligible only when it is an older tool result, so the current request has already had one complete opportunity to consume it.
5. Old tool results are represented only in the in-memory counterfactual projection with a bounded envelope containing tool identity, content hash, original size, redacted preview, and reload/reference metadata. The durable ledger records only bounded candidate metadata; it does not persist the envelope preview or reload path.
6. A persisted text result is recoverable only when its reference is an absolute, non-symlink regular file and its bytes match the recorded source SHA-256. Missing artifacts, hash mismatch, inline-only media, unsupported content, serialization failures, or ledger write failures preserve the full request.
7. Ledgers must not contain raw secret-like values, raw media, full raw tool output, reload paths, previews, or raw session/provider/model/tool/call identifiers. Externally sourced identifiers—including the session-derived ledger filename—are persisted only as domain-separated SHA-256 values. Durable records otherwise contain sizes, counts, internal bounded blocker/kind labels, content/source hashes, and provider usage. Per-request candidate records are capped at 100 while total/truncated counts remain explicit.
8. Ledger directories are traversed and created descriptor-relatively from the filesystem root with `O_NOFOLLOW` on every component. The final ledger file is opened with `O_NONBLOCK` and must be a private, current-user-owned regular file with exactly one hard link; FIFOs, symlinked ancestors, and hard-linked targets fail open without delaying or changing provider dispatch. `flock` acquisition uses `LOCK_NB` with a 50 ms bound; lock contention therefore drops only the best-effort ledger record instead of blocking provider dispatch indefinitely.

## Negative cases

- Latest multimodal tool result is never marked eligible.
- User-supplied image content is never retired by tool-result rules.
- Projection does not mutate nested message content.
- Invalid or missing config means disabled.
- Ledger sink failure cannot prevent or alter a provider call.
- NUL-prefixed serialized content is measured from decoded values, not SQLite string length.
- A provider exception propagates unchanged and the ledger wrapper must not dispatch the provider a second time.
- Invalid typed bounds, symlink artifacts, relative artifact paths, and source-hash mismatch all preserve the full request.

## Known limitations and promotion blockers

- Character counts and decoded media bytes are not provider-token, latency, cost, or accuracy measurements.
- The real top-session replay may legitimately show zero reduction when historical tool results lack recoverable references; that is fail-closed evidence, not an optimization result. Replay opens SQLite with `mode=ro` plus `query_only`; its unchanged check is explicitly limited to the main database file's device/inode/size/mtime/ctime tuple and does not claim a byte hash or WAL/SHM sidecar identity.
- Remote media URLs are treated as unrecoverable in P0 because availability, immutability, and integrity are not certified. Local media references are eligible only when every path component is non-symlink and the regular file is hashed into the counterfactual reload reference; production retirement still needs a durable rehydration and integrity contract.
- Ledger retention/rotation and deletion policy are not implemented. Do not enable long-running collection until that policy and bounded storage enforcement exist.
- Shadow projection builds a deep copy and may hash referenced artifacts. Per-artifact hashing is capped at 16 MiB and larger files fail closed, but runtime overhead within that bound has not been benchmarked.
- No production provider-payload mutation, resume-time rehydration, or end-to-end accuracy comparison has been implemented or authorized.

## Evidence requirements

- RED/GREEN unit tests for measurement, non-mutation, first-consumption protection, bounded redaction, and fail-open writes.
- Request-path test proving provider kwargs are byte-equivalent with shadow enabled.
- Offline replay over copied/decoded real-session shapes; no writes to the source database.
- Exact diff and focused test output.
- Independent review bound to the exact diff/tree.

## Rollback

The feature is disabled by default. Remove the isolated worktree to discard all implementation changes; existing worktrees and runtime state remain untouched.
