# Core Domain Model

This document defines the canonical objects for the autonomous runtime. Existing subsystem-specific objects should map into these rather than inventing parallel concepts.

## IntentRecord

```yaml
id: string
source: user | cron | gateway | taskpool | mission | webhook | health | edge | system
source_ref: string
objective: string
expected_effect: string
risk_class: low | medium | high | critical
side_effect_expectation:
  requested: []
  prohibited: []
authority_scope: string
ttl: datetime | null
idempotency_key: string
desired_terminal_states: []
context_refs: []
```

## CapabilityNode

```yaml
id: string
kind: atomic_module | composite_module | workflow | adapter | gate | artifact_template | tool | skill | worker | cron_job | mission | project
responsibility: string
consumes: []
produces: []
preconditions: []
depends_on: []
subnodes: []
side_effects: S0 | S1 | S2 | S3 | S4 | S5 | S6 | S7
evidence_requirements: []
gates: []
closure_states: []
cannot_prove: []
```

## AgentWrapper

```yaml
id: string
wraps: []
role: router | planner | executor | reviewer | verifier | monitor | publisher | arbiter
authority:
  level: read_only | local_write | external_read | external_write_requires_gate | publish_requires_gate | destructive_requires_gate | human_only
  allowed_side_effects: []
policy:
  requires_evidence: true
  requires_human_gate: false
  requires_multimodel_review: false
  max_budget: {}
cannot_do: []
produces: []
```

## AuthorityGrant

```yaml
id: string
intent_id: string
granted_by: user | policy | elevated_actor | system_default
scope: string
allowed_side_effects: []
exact_target: string | null
payload_hash: string | null
rollback_or_escape: string | null
expires_at: datetime | null
status: active | consumed | expired | revoked
```

## ExecutionLease

```yaml
id: string
intent_id: string
capability_node_id: string
executor: string
lease_scope: read_only | local_write | external_read | external_write | publish | destructive
started_at: datetime
heartbeat_at: datetime | null
ttl_seconds: int | null
status: claimed | running | completed | stale | released | failed
```

## EvidenceNode

```yaml
id: string
kind: command_output | file_artifact | api_response | message_receipt | test_result | screenshot | model_review | human_decision | ledger_entry
supports_claims: []
source_ref: string
trust_level: observed | derived | model_generated | human_asserted
observed_at: datetime
expires_at: datetime | null
verification_status: passed | failed | stale | unverified
sensitivity: public | private | secret_ref_only
```

## GateDecision

```yaml
id: string
intent_id: string
gate_type: policy | evidence | authority | safety | certification | quota | provider_health | runtime_assurance
inputs: []
required_evidence: []
result: pass | warn | fail | blocked | expired | advisory
reason: string
reviewer_votes: []
created_at: datetime
expires_at: datetime | null
```

## ProjectionRecord

```yaml
id: string
intent_id: string
target_surface: control_tower | dashboard | taskpool | mission | gateway | cron_status | docs | local_file
projected_state: string
receipt: string | null
readback_status: passed | failed | stale | not_required
```

## CompensationRecord

```yaml
id: string
intent_id: string
reason: string
action: mark_only | resend_existing_artifact | retry_once | rollback | dead_letter | manual_intervention | skip
idempotency_key: string
max_attempts: int
attempt: int
result: completed | blocked | failed | skipped
receipt: string | null
```

## ClosureRecord

```yaml
intent_id: string
status: completed | compensated | skipped | expired | dead_letter | manual_required | rejected | partial
claims: []
evidence_refs: []
projection_refs: []
residual_risks: []
next_boundary: none | authority | credential | external_side_effect | destructive | evidence_insufficient | certification
```
