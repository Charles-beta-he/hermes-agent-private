# Modular Workflow Kernel Phase 51 ADR: Pack Registry / Control Tower Projection

## Status

Accepted — Phase51 projects the Phase49 Workflow Pack and Agent Pack manifests into a read-only operator registry/control-tower view.

## Decision

Add:

- `scripts/workflow_pack_registry_projection.py`
- `tests/scripts/test_workflow_pack_registry_projection.py`
- `docs/private/workflow-kernel-pack-registry/`

The projection summarizes:

- workflow pack id/version;
- agent pack id/version;
- module rows and risk levels;
- human gates;
- side-effect flags;
- required fields for any future guarded external send.

## Evidence

```text
status=PACK_REGISTRY_PROJECTION_READY
workflow_pack=workflow_kernel_external_dispatch_governance
agent_pack=agent.workflow_kernel_operator
module_count=4
blockers=[]
```

Focused tests:

```text
tests/scripts/test_workflow_pack_registry_projection.py: 3 passed
```

## Boundary

This phase is read-only projection:

```text
no cron mutation
no task lifecycle mutation
no model generation
no credential read/write
no external message send
no unattended dispatch enablement
```

## Larger divergence now reached

After Phase51, the next step is no longer an obvious safe continuation. There are two materially different product paths:

1. **Phase50 Guarded Single External Send** — requires exact adapter, target, payload, mode, idempotency key, rollback/correction path, and post-send readback.
2. **Phase52 Control Tower UI/API Projection** — starts productizing the operator registry into dashboard/API surfaces.

This is the requested “较大分歧选项需要人工介入” boundary.
