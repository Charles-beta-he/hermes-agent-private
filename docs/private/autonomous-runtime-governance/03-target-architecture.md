# Target Architecture — ERG Runtime

ERG Runtime = Evidence-governed Reconciliation Runtime.

## High-level flow

```text
Intent Sources
  user / cron / gateway / taskpool / mission / webhook / health / edge
        ↓
Intent Normalizer
  IntentRecord + risk + authority + idempotency + expected terminal states
        ↓
Capability Graph Router
  CapabilityNode + AgentWrapper + tool/skill/worker candidates + degradation status
        ↓
Policy & Authority Gate
  allowed / blocked / human_required / expired / advisory_only
        ↓
Execution Lease
  local tool / worker / cron job / adapter / external process / no-op advisory route
        ↓
Evidence DAG
  file / command / test / API / message receipt / screenshot / model review / human decision
        ↓
Reconciliation Engine
  desired state vs observed state vs evidence state vs authority state
        ↓
Projection Surfaces
  Control Tower / dashboard / taskpool / mission board / gateway / cron status / docs
        ↓
Closure, Compensation, Ratchet
  completed / compensated / skipped / expired / dead_letter / manual_required / reusable learning
```

## Architectural split

### Product/control-plane vision

Agent OS is the product/control-plane target: capability routing, context graph, user-visible control surface, and authority-gated agent operation.

### Runtime kernel

Workflow Kernel becomes the ERG Runtime Kernel implementation: contracts, ledger, policy, adapters, runtime, cron gates, and reconciliation primitives.

### Operator surface

Control Tower is the operator cockpit over ERG Runtime. It should show progressability, blockers, evidence debt, human decisions, worker/cron/gateway health, and next safe action. It must not become a competing fact source.

### Subsystem adapters

Cron, gateway, taskpool, mission, workers, model routing, and external dispatch are adapters into the unified model. They keep their domain stores, but expose Intent/Evidence/Gate/Closure projections.

## Non-goals

- No big-bang rewrite.
- No global autorun just because the architecture exists.
- No external write/publish/destructive/payment action without exact scoped authorization.
- No model-only closure.
- No migration that deletes historical evidence before a manifest-backed archive phase.
