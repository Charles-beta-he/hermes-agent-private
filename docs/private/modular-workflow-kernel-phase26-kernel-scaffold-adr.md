# Modular Workflow Kernel Phase 26 ADR: Kernel Package Scaffold

## Status

Accepted — Phase 26 creates the first `workflow_kernel` package scaffold and moves shared JSON artifact IO behind imported modules while keeping existing scripts as compatibility CLIs. Runtime behavior is unchanged.

## Context

Phase 25 selected incremental strangler migration to an Event-governed Modular Workflow Kernel. The first safe slice is not a runtime switch; it is a package scaffold plus small shared helper extraction with parity tests.

## Decision

Add:

```text
workflow_kernel/__init__.py
workflow_kernel/io.py
```

`workflow_kernel.__init__` exposes the Phase 26 safety contract:

```text
PHASE = "26"
RUNTIME_SWITCH_ENABLED = False
CAPABILITIES includes kernel_scaffold_compat_only
```

`workflow_kernel.io` provides canonical JSON artifact helpers:

```text
canonical_json_bytes(payload)
write_json_artifact(path, payload) -> {path, sha256}
read_json_artifact(path, missing_ok=False)
```

Patch existing compatibility CLIs to consume the helper:

```text
scripts/workflow_architecture_migration_evaluation.py
scripts/cron_provider_health_gate.py
```

## TDD Evidence

RED test first:

```text
tests/workflow_kernel/test_kernel_scaffold.py
```

Initial run failed as expected:

```text
ModuleNotFoundError: No module named 'workflow_kernel'
assert 'workflow_kernel.io' in migration script failed
```

GREEN verification:

```text
12 tests passed, 0 failed
```

## Safety Boundaries

Phase 26 does not authorize:

- runtime switch;
- cron wrapper rollout;
- cron mutation;
- external dispatch expansion;
- autorun enablement;
- evidence directory consolidation/deletion;
- taskpool lifecycle mutation beyond evidence fields.

## Consequences

Benefits:

- Creates a real importable package boundary for later migration.
- Starts reducing script-local IO duplication.
- Keeps script CLIs stable and backward-compatible.
- Provides TDD proof that scaffold exists and safety flags are explicit.

Tradeoffs:

- Only a small extraction; most Phase17-24 behavior still lives in scripts.
- Regenerated Phase24/Phase25 JSON reports now use the shared canonical JSON helper, changing artifact hashes but not report semantics.

## Next Phase

Phase 27 should extract contracts/ledger helpers behind `workflow_kernel` modules with parity tests for Phase17-24 behavior while preserving all script CLI entrypoints.
