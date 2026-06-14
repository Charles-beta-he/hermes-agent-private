# Modular Workflow Kernel Phase 53 ADR: Live Control Tower Runtime/API Integration

## Status

Accepted — Phase53 integrates the Workflow Kernel projection into the existing Hermes Control Tower dashboard plugin and live `/api/plugins/control-tower/snapshot` surface.

## Decision

Use the existing Control Tower plugin/API rather than creating another dashboard server.

Changes:

- `hermes_cli/control_tower.py` now exposes `workflow_kernel_control_tower` in the read-only snapshot.
- `plugins/control-tower/dashboard/dist/index.js` renders a Workflow Kernel panel.
- `plugins/control-tower/dashboard/dist/style.css` styles the panel.

## Evidence

```text
workflow_kernel_control_tower.status=ready
latest_guarded_send.message_id=4261
unattended_dispatch_policy.status=not_compiled before Phase54
```

Focused tests:

```text
tests/hermes_cli/test_control_tower_snapshot.py
tests/plugins/test_control_tower_dashboard_plugin.py
```

## Boundary

The integration is read-only:

```text
no task lifecycle mutation
no cron mutation
no additional external send
no unattended dispatch enablement
```
