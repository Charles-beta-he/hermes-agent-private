# Modular Workflow Kernel Phase 50 ADR: Guarded Single External Send

## Status

Accepted — Phase50 executed exactly one explicitly selected Telegram home smoke-test send after the user chose option 1.

## Decision

Use the standalone Hermes delivery CLI instead of the in-process `send_message` tool because the tool runtime reported a missing Telegram dependency. The CLI path is an existing Hermes-supported delivery surface and returned a structured JSON receipt.

Message:

```text
Workflow Kernel Phase50 guarded send smoke test: external dispatch path verified.
```

Target:

```text
adapter=telegram
target=telegram home channel
mode=guarded_single_send
```

## Evidence

```text
success=true
platform=telegram
chat_id=5524172309
message_id=4261
mirrored=true
```

Receipt artifact:

```text
docs/private/workflow-kernel-guarded-external-send/workflow_kernel_guarded_external_send_20260613_phase50.json
```

Focused test:

```text
tests/scripts/test_workflow_guarded_external_send_receipt.py: 3 passed
```

## Boundary

This was a single explicit external side effect only:

```text
no repeated send
no unattended dispatch enablement
no credential read/write
no scheduler mutation
no Lark/GitHub mutation
```

A delivered Telegram message cannot be destructively rolled back through Hermes. The correction path is a compensating message to the same target if needed.
