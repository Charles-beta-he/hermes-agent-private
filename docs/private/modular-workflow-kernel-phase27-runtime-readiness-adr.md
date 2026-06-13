# Modular Workflow Kernel Phase 27 ADR: Runtime Switch Readiness

## Status

Accepted — the Workflow Kernel is now runtime-switchable in compatibility-shim mode, pending explicit enablement. The default runtime switch remains disabled.

## Context

Phase 26 created the `workflow_kernel` package scaffold and shared JSON artifact IO while keeping scripts as compatibility CLIs. The user's target is to continue until the system can be switched to the new runtime architecture.

The safe interpretation of “runtime switchable” is:

```text
A verified kernel runtime readiness entrypoint exists.
It can compile and write readiness evidence.
It exposes compatibility CLI targets and required tests.
It keeps default runtime switch disabled.
Actual runtime switch/cron wrapper rollout still requires a separate explicit enable gate.
```

## Decision

Add kernel modules:

```text
workflow_kernel/contracts.py
workflow_kernel/ledger.py
workflow_kernel/runtime.py
```

Add CLI:

```text
scripts/workflow_kernel_runtime_readiness.py
```

The runtime readiness report returns:

```text
status: RUNTIME_SWITCH_READY_PENDING_EXPLICIT_ENABLE
switch_mode: compatibility_shim
runtime_switch_enabled_by_default: false
explicit_enable_required: true
```

## Contract and Ledger Boundaries

`workflow_kernel.contracts` provides:

- secret-redacting contract packets;
- stable canonical payload digests;
- evidence refs.

`workflow_kernel.ledger` provides:

- append-only JSONL events;
- deterministic idempotency by event type + idempotency key + payload;
- safe readback.

`workflow_kernel.runtime` provides:

- compatibility CLI inventory;
- required test inventory;
- blocked capability inventory;
- readiness packet.

## Blocked Capabilities

Still closed:

```text
cron_mutation
external_dispatch_expansion
autorun_enablement
config_migration
```

## Evidence

RED test first:

```text
tests/workflow_kernel/test_runtime_switch_readiness.py
```

Initial failure:

```text
ModuleNotFoundError: workflow_kernel.contracts / ledger / runtime
missing scripts/workflow_kernel_runtime_readiness.py
```

GREEN:

```text
16 focused tests passed, 0 failed
```

Report artifact:

```text
docs/private/workflow-kernel-runtime-readiness/workflow_kernel_runtime_readiness_20260613_phase27.json
docs/private/workflow-kernel-runtime-readiness/workflow_kernel_runtime_readiness_20260613_phase27.md
```

## Consequences

Benefits:

- New architecture has a verified runtime readiness surface.
- Switch can be evaluated through kernel code, not prose.
- Existing scripts remain compatibility CLIs.
- Sensitive fields are redacted before contract digest/artifact storage.

Tradeoffs:

- This is not yet an enabled runtime switch.
- First actual wrapper rollout still requires explicit enablement and one-slice migration.

## Next Boundary

The system is now **runtime-switch ready, pending explicit enable**.

The next phase may enable a single compatibility wrapper, but must remain one-slice and reversible:

1. explicit enable flag or config gate;
2. one wrapper target only;
3. read readiness packet first;
4. preserve fallback to old script;
5. no external dispatch expansion or global cron mutation.
