# Hermes Event-Governed Agent Control Plane

Status: safe foundation landed as report-only contracts.
Scope: Hermes Agent self-architecture only — Control Tower/taskpool/brain workers, gateway/cron ingress, multi-model certification, persistent learning surfaces, and external side-effect governance.
Anti-condition: ordinary Q&A, read-only inspection, simple calculations, and unrelated app project work should not inherit this heavy flow unless explicitly adopted.

## Decision

Hermes should not evolve toward a universal autonomous super-agent. It should use narrow-permission agents/workers governed by events, a canonical ledger/SSOT, evidence gates, and explicit human confirmation for dangerous actions.

## Pipeline

```text
User / CLI / Gateway / Cron / Webhook
        ↓
Event Router / Collector
        ↓
Control Tower / Orchestrator
        ↓
Policy / Danger Gate ── Human Gate
        ↓
Bounded Worker / Tools
        ↓
Evidence Packet
        ↓
Verifier / Multimodel Certifier
        ↓
Ledger API / SSOT Guard
        ↓
Taskpool / Brain / Memory / Skills / Session / Cron Registry
        ↓
Projection Builder / Dashboard / Gateway Report
```

## Invariants

1. Workers submit evidence/proposals; they do not directly mark tasks done/archive/reopen.
2. Model consensus is advisory; it is not a fact or an evidence substitute.
3. Gateway/dashboard/report/projection surfaces do not directly mutate lifecycle.
4. Memory and skill writes default to proposal-only unless explicitly approved.
5. Cron/webhook/untrusted ingress must not inherit high-trust CLI permissions.
6. Dangerous actions require policy/danger gate, explicit confirmation, action preview, audit event, and rollback/escape path.
7. SSOT wins over projection; projections must be disposable and replayable.

## Fast / Standard / Guarded

- Fast: read-only, no persistence, no external side effects.
- Standard: scoped work plus evidence packet and verifier verdict.
- Guarded: task lifecycle changes, memory/skill/cron/provider changes, external send/write/deploy/delete; requires policy/human gate.

## Evidence levels

- E0: claim only; never sufficient for done.
- E1: static evidence.
- E2: execution evidence.
- E3: external receipt or human acceptance.
- E4: sustained operational evidence.

Default done requires E2. External side effects require E3. Long-running automation maturity requires E4.

## First landed implementation

- `hermes_cli/event_governed_agents.py` provides deterministic, side-effect-free, report-only contracts.
- `tests/hermes_cli/test_event_governed_agents.py` proves worker lifecycle mutation is blocked, memory/skill persistent writes are guarded, untrusted ingress cannot mutate lifecycle, and the report CLI emits JSON.

The first implementation intentionally does not execute tools, mutate tasks, write memory/skills, send messages, or alter cron/provider config.

## Future implementation route

1. Wire event envelopes into Control Tower observations.
2. Add worker lease/heartbeat APIs if not already represented by the taskpool/brain lane.
3. Convert report-only policy verdicts into fail-closed middleware for privileged actions.
4. Add human gate command digest and approval TTL for guarded paths.
5. Keep projection/dashboard/gateway read-only until the Ledger API provides guarded write-through.
