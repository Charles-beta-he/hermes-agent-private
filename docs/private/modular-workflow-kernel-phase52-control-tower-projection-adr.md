# Modular Workflow Kernel Phase 52 ADR: Control Tower UI/API Projection

## Status

Accepted — Phase52 creates a read-only Control Tower UI/API projection after the Phase50 guarded Telegram send and Phase51 pack registry projection.

## Decision

Add:

- `scripts/workflow_control_tower_projection.py`
- `tests/scripts/test_workflow_control_tower_projection.py`
- `docs/private/workflow-kernel-control-tower-projection/`

The projection emits:

- JSON API payload (`workflow-control-tower.v1`);
- static HTML dashboard artifact;
- Markdown operator report.

## Evidence

```text
status=CONTROL_TOWER_UI_API_PROJECTION_READY
last_external_send.message_id=4261
browser_readback_title=Workflow Kernel Control Tower Projection
```

Focused tests:

```text
tests/scripts/test_workflow_control_tower_projection.py: 3 passed
```

## Boundary

This phase is still projection-only:

```text
no live server started
no runtime mutation
no unattended dispatch enablement
no additional external send
```

## Next larger divergence

After Phase52, the next work is a material product/policy divergence:

1. **Phase53 Durable Control Tower runtime/API integration** — choose whether to integrate into Hermes dashboard, a static route, a local API endpoint, or another operator surface.
2. **Phase54 Unattended dispatch policy gate** — define target allowlist, rate limit, duplicate policy, correction path, and monitoring before any unattended sends.

Stop here for human direction.
