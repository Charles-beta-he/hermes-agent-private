# Modular Workflow Kernel Phase 37 ADR: Runtime Enablement Human Authorization Packet

## Status

Accepted — the workflow kernel now emits an explicit human authorization packet for crossing the runtime/scheduler enablement boundary.

## Context

Phase 36 produced a readiness summary with all checks passing and the global runtime switch still disabled. The next meaningful work could cross authority boundaries:

- enabling `workflow_kernel.RUNTIME_SWITCH_ENABLED`;
- scheduler-native takeover;
- cron job mutation;
- deliberate cron triggering;
- credentialed model smoke;
- external message dispatch.

The user authorized continued safe advancement until a meaningful human choice is required. This is that boundary: the remaining paths have materially different authority, blast radius, and rollback profiles.

## Decision

Add:

```text
scripts/workflow_runtime_enablement_packet.py
```

It compiles a decision packet with options:

```text
A_READ_ONLY_CONTINUE
B_SINGLE_WRAPPER_RUNTIME_ENABLE_DRY_RUN
C_SINGLE_WRAPPER_SCHEDULER_NATIVE_ROLLOUT
D_WAIT_FOR_NATURAL_RECEIPTS
```

The safe default remains:

```text
A_READ_ONLY_CONTINUE
```

The packet is evidence only. It does not enable runtime, mutate cron, trigger jobs, call models, read credentials, or send messages.

## Evidence

Verification:

```text
./venv/bin/python -m py_compile \
  scripts/workflow_runtime_enablement_packet.py \
  scripts/workflow_runtime_readiness_summary.py

./scripts/run_tests.sh \
  tests/scripts/test_workflow_runtime_enablement_packet.py \
  tests/scripts/test_workflow_runtime_readiness_summary.py
```

Readback:

```text
8 tests passed, 0 failed
status=RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY
recommended=A_READ_ONLY_CONTINUE
```

## Boundary

Closed without explicit human authorization:

- enable workflow kernel runtime switch;
- mutate scheduler / cron definitions;
- trigger cron jobs just to create evidence;
- scheduler-native takeover;
- credentialed model generation smoke;
- external delivery.

## Consequence

This phase stops at a compact intervention packet instead of silently downgrading or crossing a permission boundary. It preserves all safe progress and makes the next decision auditable.
