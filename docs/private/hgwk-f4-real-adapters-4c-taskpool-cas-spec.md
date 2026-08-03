# HGWK F4 Slice 4C — canonical taskpool CAS/fence precursor spec

Status: frozen implementation spec
Date: 2026-08-03
Parent SSOT: `docs/private/hgwk-f4-real-adapters-ssot.md`
Review precondition: `/Users/charles/.hermes/runs/hgwk-f4-real-adapters-20260802/4c-degraded-pre-review.md`

## 1. Goal

Provide a production-shaped but unintegrated canonical Markdown task-note transition primitive with optimistic CAS, monotonic fencing, exact idempotent retry, descriptor-pinned persistence, and deterministic receipts. Prove the primitive only against isolated temporary task roots.

## 2. Non-goals and closed authority

4C does not:

- modify or import the live brain vault;
- integrate with `brain_task_orchestrator.py`, dispatchers, workers, schedulers, cron, dashboards, event hooks, runtime claim scripts, adapters, models, network services, or external systems;
- implement `force`, `done`, `certified`, `archived`, stale-lease reconciliation, lease renewal/heartbeat, task creation, derived-view refresh, or completion authority;
- commit, push, deploy, send, or execute a real task.

## 3. Public API

Module: `workflow_kernel.taskpool_cas`

```python
transition_task_note(
    *,
    tasks_root: Path,
    task_id: str,
    expected_status: str,
    expected_revision: int,
    expected_fence: int | None,
    to_status: str,
    actor: str,
    run_id: str,
    lease_expires_at: str | None,
    reason: str,
    evidence: Sequence[str],
    idempotency_key: str,
) -> dict[str, object]
```

All inputs are validated before canonical mutation. Errors use `TaskpoolCASError(code, message)` with stable codes. Invalid `tasks_root` runtime shapes, path coercions, embedded NUL, or any UTF-16 surrogate path content return `invalid_tasks_root`; they do not leak native path-conversion/open exceptions or pass through platform surrogate-escape behavior. Every accepted time is an exact canonical UTC scalar `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`; alternate separators, offsets, controls, and line separators are rejected before rendering. Current UTC time is implementation-owned; callers cannot supply or backdate it. Tests may monkeypatch only the private clock seam.

Caller-provided reason/evidence strings are exact built-in strings, UTF-8 scalar-only, contain no surrounding whitespace or control/line-separator characters, and are bounded before normalization to 4,096 bytes each; no trimming or semantic normalization occurs. Evidence is an exact built-in list or tuple with at most 64 items. Built-in subclasses and other runtime shapes are rejected before their overridable iteration/length/hash methods can execute, and invalid inputs return stable domain errors rather than leaking Python exceptions.

## 4. Supported lifecycle graph

Only these transitions are legal:

- `pending -> claimed`
- `retry -> claimed`
- `claimed -> running`
- `claimed -> pending`
- `claimed -> retry`
- `claimed -> blocked`
- `claimed -> expired`
- `running -> review`
- `running -> retry`
- `running -> blocked`
- `running -> expired`

`retry` and `blocked` require a non-empty reason. `review` requires at least one structured evidence item using the canonical prefixes `command:`, `file:`, `search:`, `brainctl:`, `artifact:`, `url:`, or `proof:`. Reason and evidence items are bounded UTF-8 scalar strings containing only printable characters; line separators and terminal-control characters are rejected. No force override exists.

## 5. Revision and fencing semantics

Owned frontmatter fields:

- `taskpool_revision`
- `taskpool_fence`
- `taskpool_claimed_by`
- `taskpool_run_id`
- `taskpool_claim_expires_at`
- `taskpool_idempotency_v1`

Legacy bootstrap is allowed only when all owned fields are absent: current revision and fence are both zero. If only one of revision/fence is present, either is malformed/negative, or any other owned field exists without both counters, the note is invalid.

Every successful non-replay transition writes:

- `new_revision = current_revision + 1`
- `new_fence = current_fence + 1`

Because legacy bootstrap starts both counters at zero and every transition increments both once, canonical revision and fence values—and every stored receipt's previous/new counter pairs—must remain equal. Counters are bounded to unsigned 63-bit values before integer serialization/conversion. Divergence or overflow is corrupt metadata and fails closed with a domain error rather than a native conversion exception.

A transition from `claimed` or `running` requires:

- `expected_fence == current_fence`;
- actor equals `taskpool_claimed_by`;
- run ID equals `taskpool_run_id`;
- the recorded UTC lease is valid and strictly later than `now`.

A transition into `claimed` requires non-empty actor and run ID plus a valid UTC lease strictly later than `now`, and records them. `claimed -> running` retains them. Any transition out of claim-bound states clears all three claim fields.

## 6. Idempotency contract

The canonical note contains a bounded one-line canonical JSON object in `taskpool_idempotency_v1`.

- Key: validated idempotency key.
- Value: canonical request digest and deterministic receipt.
- Exact-key/exact-digest retry returns the stored receipt with `idempotent_replay=true`, even if later transitions advanced the task.
- Exact-key/different-digest returns `idempotency_conflict`.
- Maximum entries: 64.
- Maximum serialized object size: 65,536 UTF-8 bytes.
- No pruning or silent eviction. Capacity failure occurs before mutation.

The idempotency JSON rejects duplicate object keys, non-finite constants, noncanonical JSON bytes, excessive recursion, and values that cannot be rendered as canonical UTF-8 JSON. Parser/renderer depth and conversion failures are projected to stable metadata errors. Entry and receipt objects have exact key sets. Every stored receipt is revalidated before use: legal transition, task/key/digest bindings, actor/run tokens, lockstep bounded counters, UTC occurrence/lease semantics, exact lease disposition, nonzero normalized post-write digest, and `idempotent_replay=false`. The history must contain exactly one gap-free status-linked receipt per canonical revision. Revision one cannot originate from a claim-bound state because a metadata-free legacy `claimed` or `running` note is invalid and therefore cannot lawfully establish claim authority. Receipt occurrence times must be nondecreasing. Consecutive receipts crossing a claim-bound source state must retain the prior claim actor and run ID, must occur strictly before the prior claim lease expires, and a transition that remains claim-bound must retain the exact prior lease. The history tip must match current status/claim metadata, and the tip's normalized digest must bind the complete current task bytes. On exact replay, request-owned receipt fields are additionally equal to the incoming request before the replay flag is projected; target policy is re-applied to require retry/blocked reason, prefixed review evidence, claimed-only request lease, and exact claimed lease binding. A supplied expected fence must equal the stored previous fence, and claim-bound replays require that fence.

The request digest binds every caller-controlled semantic request field, including expected status/revision/fence, target status, actor, run ID, lease, reason, ordered evidence, task ID, and idempotency key. Generated occurrence time is stored in the receipt but is not a request field.

## 7. Canonical Markdown contract

- UTF-8 only.
- Required opening and closing frontmatter delimiters.
- `type: task` and a supported scalar `status` are required exactly once.
- Owned fields must each occur at most once as canonical unindented plain top-level keys. YAML-equivalent spellings using quoted/escaped keys, whitespace before the colon, scalar tags/anchors, explicit-key syntax, aliases, or merge-key indirection are rejected rather than ignored.
- Owned scalar values and the idempotency JSON must parse strictly.
- Unknown frontmatter and body bytes are preserved exactly.
- A successful transition updates/inserts only owned fields plus `status` and appends one deterministic audit line to the body.
- Duplicate/ambiguous owned fields, malformed delimiters, NUL, CR-only structure, YAML line separators U+0085/U+2028/U+2029 in frontmatter, or invalid JSON fail closed.

## 8. Filesystem and transaction contract

1. `tasks_root` must already exist and must contain no `..` path component. Its exact supplied absolute path spelling is traversed without resolving platform aliases such as Darwin `/tmp -> /private/tmp`; any symlink component therefore fails closed. Its final directory must be owned by the effective user and must not be group/world writable.
2. Open the complete root chain without following symlinks; pin root dev/inode and acquire an exclusive POSIX flock on the root descriptor. Root-descriptor identity-read failures return stable `unsafe_task_root`.
3. Re-open the complete named root chain with no-follow semantics and re-verify the pinned root dev/inode after lock acquisition and at every later root barrier; an intermediate symlink alias fails closed even if it resolves back to the pinned directory.
4. Open exact `<task_id>.md` descriptor-relative with `O_NOFOLLOW`; require a regular single-link file owned by the effective user, reject group/world-writable modes, and require equality between descriptor and named leaf dev/inode. Any post-open identity-read failure closes the acquired descriptor and returns stable `unsafe_task_path` without replacing the primary error. This closes in-place mutation by a different UID that could otherwise preserve pathname identity and bypass advisory locking.
5. Read and validate from the descriptor, then perform idempotency/CAS/fence/policy checks. Canonical descriptor I/O failure before replacement returns stable `read_failed`, not a native exception.
6. Create a private random temporary leaf descriptor-relative with `O_EXCL|O_NOFOLLOW` and creation mode `0600`, write fully while it remains private, align and verify the temporary group to the canonical task group regardless of setgid, then `fchmod` and verify the complete canonical mode (including setuid/setgid/sticky bits) after writing so umask, inherited directory group, and write-time special-bit clearing cannot alter mode or group access semantics. If exact mode/group cannot be established, fail before replacement.
7. Before replacement, verify pinned root identity and that the named canonical leaf still equals the originally opened leaf.
8. Replace descriptor-relative, fsync the directory, reopen/read back by descriptor, and verify expected digest, revision, fence, status, canonical mode/group, and root identity.
9. Pre-replace failure leaves canonical bytes unchanged. Cleanup and descriptor/lock finalization are best-effort and cannot override the transaction's primary success/failure classification, including post-attempt `durability_unknown`. Temporary cleanup removes only a leaf whose descriptor and named identity still match and has one link; when the initial post-create `fstat` failed, cleanup retries identity acquisition from the still-open descriptor before applying the same guarded comparison.
10. The commit becomes uncertain immediately before invoking replacement: if `replace` raises, it may already have installed the candidate and therefore returns `durability_unknown`, never a pre-commit classification. Any domain, OS, ordinary exception, or process-control `BaseException` raised after that point that prevents confirmed directory durability/readback/identity also returns `durability_unknown`. Before replacement, process-control exceptions retain their normal propagation semantics. No automatic rollback or second execution is allowed.
11. Windows or missing no-follow/directory-descriptor/flock semantics return `platform_unsupported`.

Threat-model boundary: the primitive rejects writable-by-other-user namespaces and detects scheduled root/task replacement at its identity barriers. It does not claim protection from an actively hostile process running as the same effective UID between the final pathname identity check and Darwin pathname-based `replace`/cleanup; such deployment requires a separate UID/authority boundary. 4C remains isolated and unintegrated, so this limitation grants no live-taskpool admission.

## 9. Stable result

A receipt binds:

- schema version;
- task ID;
- idempotency key and request digest;
- from/to status;
- previous/new revision;
- previous/new fence;
- actor and run ID;
- occurred-at and lease expiration;
- canonical normalized post-write SHA-256 (computed over the committed note bytes after replacing only this receipt field with the sentinel `sha256:` plus 64 zeroes, avoiding an impossible self-referential raw-file hash);
- `idempotent_replay`.

## 10. Acceptance evidence

Required before certification:

1. Focused unit and negative tests pass.
2. Spawn-based multiprocess races prove one winner and stale/conflict losers with one final canonical state.
3. Deterministic root/task replacement schedules prove fail-closed behavior and no attacker-path mutation.
4. Injected write/fsync/replace/readback failures distinguish pre-commit unchanged state from post-commit `durability_unknown`.
5. An isolated real-filesystem smoke transitions a temporary note and reads back the exact receipt/state.
6. Full `tests/workflow_kernel` passes.
7. Existing TypeScript workflow-contract tests, schema/typecheck, semantic generator/parity, and 4A authority imports remain green.
8. `git diff --check` passes.
9. Exact changed-file manifest and hashes are frozen.
10. A fresh independent reviewer reads the frozen live bytes and returns no P0/P1 findings. If external review remains unavailable, 4C must remain implemented-but-uncertified.
