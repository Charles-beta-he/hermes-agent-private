# Hermes Norm Governance

Hermes uses norm governance to keep concurrent sessions from diverging on durable rules or overwriting each other’s work.

## Guarantees

1. Durable norms live in a profile-scoped SQLite source of truth, not in a single chat context.
2. Sessions consume `NormSnapshot` objects with explicit revisions and hashes.
3. Norm writes use compare-and-swap (CAS): stale writers fail closed.
4. Shared-resource writes can acquire TTL leases before mutating files, skills, memory, config, task rows, or brain sections.
5. Task fusion is explicit through `FusionRecord`; evidence and acceptance criteria are merged, but there is only one active writer.
6. Evidence remains append-only; conclusions and tasks may move forward only after a truth gate checks current revisions, write conflicts, and evidence coverage.

## Stores

Default database:

```text
$HERMES_HOME/state/norm_governance.db
```

Tables:

- `norms`: versioned durable rules keyed by canonical norm key.
- `norm_events`: append-only norm change events for invalidation/reload.
- `leases`: active writer ownership for canonical resource keys.
- `fusion_records`: JSON fusion records for duplicate/overlapping task work.

## Canonical resource keys

Use `kind:identifier#fragment`:

- `file:/Users/charles/.hermes/scripts/consult_models.py`
- `skill:autonomous-ai-agents/hermes-agent`
- `memory:user:communication-preferences`
- `config:quorum.yaml#multi_brain.resident_planners`
- `kanban:task:<task_id>`
- `brain:project:uvisa#acceptance`

## CLI

All commands emit JSON:

```bash
hermes norms upsert global:evidence "Evidence first" --expected-revision 0
hermes norms snapshot --scope global > /tmp/norm-snapshot.json
hermes norms stale /tmp/norm-snapshot.json
hermes norms lease acquire file:/repo/a.py --owner-session-id session-a --ttl 300
hermes norms lease active
hermes norms fusion create \
  --parent-task-id task-a \
  --parent-task-id task-b \
  --write-sets-json '[{"session_id":"s1","task_id":"task-a","resources":["file:/repo/a.py"]}]' \
  --norm-revisions-json '{"global:evidence":1}' \
  --evidence-refs-json '[]' \
  --acceptance "tests pass" \
  --active-writer session-a
```

## Integration policy

Before any high-risk durable write, callers should:

1. Build or load the current `NormSnapshot`.
2. Declare a `WriteSet` of canonical resource keys.
3. Acquire leases for replace/delete/high-risk patch operations.
4. Write with CAS against the base revision/hash.
5. Release leases after verification or let short TTLs expire.
6. If another session owns the resource, create or join a `FusionRecord` instead of overwriting.

## What counts as high risk

- Modifying skills, memory, global config, task status, brain canonical docs, or Hermes scripts.
- Replacing/deleting files.
- Marking tasks done.
- Mutating resources also claimed by another session or background worker.

## Failure mode

Fail closed. If a snapshot is stale, a lease is active, or CAS detects drift, the writer must rebase or fuse tasks instead of overwriting latest state.
