# Modular Workflow Kernel Phase 49 ADR: Workflow Pack / Agent Pack Manifests

## Status

Accepted — Phase49 productizes the proven Phase17-48 workflow-kernel and external-dispatch-governance chain as reusable pack manifests without changing runtime behavior.

## Decision

Add schema/projection-only artifacts:

- `schemas/workflow_pack_manifest.schema.json`
- `schemas/agent_pack_manifest.schema.json`
- `scripts/workflow_pack_manifest_compiler.py`
- `tests/scripts/test_workflow_pack_manifest_compiler.py`

The compiler emits:

- `workflow_kernel_external_dispatch_governance.workflow_pack.json`
- `workflow_kernel_operator.agent_pack.json`
- `workflow_kernel_pack_manifests_20260613_phase49.json`

## Evidence

```text
status=WORKFLOW_AGENT_PACK_MANIFESTS_READY
workflow_pack=workflow_kernel_external_dispatch_governance
agent_pack=agent.workflow_kernel_operator
modules=4
validation_errors=[]
```

Focused tests:

```text
tests/scripts/test_workflow_pack_manifest_compiler.py: 4 passed
```

## Boundary

This phase is schema/projection-only:

```text
no cron mutation
no model generation
no credential read/write
no external message send
no unattended dispatch enablement
```

Phase50 guarded external send still requires an exact adapter, target, payload, mode, idempotency key, and rollback/correction path. If no such target is provided, continue to Phase51 pack registry/control-tower projection.
