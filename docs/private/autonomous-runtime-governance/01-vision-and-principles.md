# Vision and Principles

## Vision

Hermes should evolve from a collection of tools, schedules, dashboards, taskpool scripts, and gateway adapters into an evidence-governed autonomous runtime.

The long-term product behavior is not "run more things automatically". It is:

```text
Advance user and system intents safely until a terminal state is reached, using evidence, authority, runtime gates, and bounded compensation.
```

## Design principles

1. All automation starts from an `IntentRecord`.
2. Every completion claim must bind to `EvidenceNode` records.
3. Every external side effect must have `AuthorityGrant`, `idempotency_key`, receipt, and readback.
4. `CapabilityGraph` says what can be done; `AuthorityGate` says whether it may be done; `EvidenceGraph` says whether it was done.
5. Agent/worker/model outputs are proposals or evidence candidates; they do not own lifecycle authority by role name.
6. Model consensus is not observed proof.
7. Control Tower/dashboard/gateway/taskpool are projection surfaces unless the relevant domain explicitly defines them as an SSOT.
8. Startup self-check is only a readiness gate; dynamic runtime assurance is the real long-term self-check.
9. Compensation is not retry; it is minimal correction chosen from state and evidence.
10. At-most-once remains a safety primitive; observability is restored through ledger/reconciliation, not blind replay.
11. Every automated flow must terminate as completed, compensated, skipped, expired, dead_letter, rejected, or manual_required.
12. The system goal is not "never fail" but "fail observably, classifyably, recoverably, and without uncontrolled side effects".

## Theory-to-design constraints

| Theory / source | Design constraint in Hermes |
|---|---|
| Cybernetics: Wiener, Ashby, Stafford Beer | Hermes must be modeled as feedback control: sensors, controller, actuators, feedback, boundary. |
| Autonomic Computing / MAPE-K: Kephart & Chess, IBM blueprint | Every autonomous capability must declare Monitor, Analyze, Plan, Execute, Knowledge surfaces. |
| Runtime Verification: Havelund & Rosu, Leucker & Schallhart | Critical properties must be monitorable at runtime: no duplicate side effects, eventual terminal state, bounded response. |
| Sagas: Garcia-Molina & Salem | Long-running side-effect workflows require compensating actions, not global transactions or blind retries. |
| Transaction processing: Gray & Reuter | Durable records must bracket execution; crash windows must be reconcilable. |
| DDIA: Kleppmann | Exactly-once is achieved by idempotency, logs, and readback, not wishful retry semantics. |
| SRE: Google | Liveness, readiness, startup, and business success are separate signals. |
| STAMP/STPA: Leveson | Safety failures come from bad control structures; authority gates are safety controls. |
| Kubernetes controllers/operators | Desired state vs observed state divergence is the work object, not an exceptional afterthought. |

## Product requirement implication

Automation-product PRDs must include:

- user outcome;
- autonomy boundary;
- trigger/intent sources;
- state model;
- evidence contract;
- authority and side-effect class;
- runtime assurance properties;
- compensation policy;
- observability/operator surface;
- metrics and ratchet path.
