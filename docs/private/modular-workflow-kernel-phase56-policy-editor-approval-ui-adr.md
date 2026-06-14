# Modular Workflow Kernel Phase 56 ADR: Control Tower Policy Editor / Approval UI

## Status

Accepted — Phase56 exposes Workflow Kernel dispatch-policy review in Control Tower as an approval-ledger-only UI/API surface.

## Decision

Add/extend:

- `hermes_cli/control_tower.py` with policy editor projection and local decision ledger.
- `plugins/control-tower/dashboard/plugin_api.py` with `POST /workflow-kernel/policy-editor/decision`.
- `plugins/control-tower/dashboard/dist/index.js` with Workflow Kernel policy editor actions.
- tests for snapshot, plugin UI, and API route.

Allowed decisions:

```text
draft
approve_dry_run_only
reject
request_changes
```

Unknown decisions are recorded as blocked and still do not enable dispatch.

## Evidence

```text
status=POLICY_EDITOR_APPROVAL_UI_READY
decision=approve_dry_run_only
unattended_dispatch_enabled=false
external_side_effects_executed=false
cron_state_mutated=false
```

## Boundary

The UI/API records local approval intent only. It does not mutate the Phase54 policy, install cron, activate a guarded send runner, or send messages.
