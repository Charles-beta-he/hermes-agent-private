# Provider-routing cost/quality evidence gate

Task: `2026-06-02-为-hermes-provider-routing-增加成本-质量策略证据门`

## Scope

This tranche adds a narrow documentation/schema-validation gate for provider-routing cost/quality governance claims. It intentionally does not change live model selection, provider defaults, or taskpool closeout rules by itself. The goal is to prevent vague “we optimized routing for cost/quality” claims from being treated as evidence when the underlying task/report never declared the policy, budget, refusal list, or observable route outcome.

## Current-state inventory before this change

Existing Hermes/OpenRouter control-plane surfaces already expose several low-level routing knobs and observability signals:

- `provider_routing` config and propagation: `website/docs/user-guide/features/provider-routing.md`, `cli.py`, `gateway/run.py`, `cron/scheduler.py`, `agent/chat_completion_helpers.py`
  - current fields: `sort`, `only`, `ignore`, `order`, `require_parameters`, `data_collection`
- quality-adjacent model gate: `openrouter.min_coding_score`
  - currently forwarded in `cli.py`, `gateway/run.py`, `cron/scheduler.py`, and `agent/chat_completion_helpers.py`
- routed-provider observability:
  - OpenRouter response cache summary in `run_agent.py` records `route_provider`, `route_model`, `status`, and invalidation reason without persisting prompt bodies
  - `/usage` surfaces the last observed route in `gateway/run.py`
- budget/quota observability:
  - `agent/account_usage.py` fetches provider usage windows/remaining quota snapshots

Gap before this change: there was no repo-local `cost_quality_policy` evidence schema, no fail-closed classification for `policy_missing` / `unknown` / `unsupported`, and no fixture showing how taskpool evidence should record budget/refusal/data-retention constraints alongside the actual selected route.

## Minimal evidence-bundle schema

A governed provider-routing claim should carry a dry-run or runtime evidence bundle shaped like:

```json
{
  "provider": "openrouter",
  "cost_quality_policy": {
    "mode": "quality-first | balanced | cost-first",
    "budget": {
      "max_usd": 1.5,
      "scope": "task"
    },
    "refuse_providers": ["Together"],
    "refuse_models": ["openrouter/experimental-preview"],
    "require_zdr": true,
    "dlp_tier": "strict | standard | none | unknown",
    "data_retention": "deny | allow | unknown"
  },
  "routing_preferences_snapshot": {
    "sort": "price | throughput | latency",
    "only": [],
    "ignore": ["Together"],
    "order": [],
    "require_parameters": true,
    "data_collection": "deny",
    "openrouter_min_coding_score": 0.72
  },
  "routing_outcome": {
    "status": "policy_applied | policy_missing | unknown | unsupported",
    "selected_provider": "anthropic",
    "selected_model": "claude-sonnet-4",
    "selection_reason": "Why this route remained in-policy",
    "observed_via": "openrouter_headers | config_only | none",
    "unsupported_fields": [],
    "missing_fields": []
  }
}
```

## Fail-closed rules

1. Missing `cost_quality_policy` => classify `policy_missing`; do not claim routing was governed.
2. Policy exists but no observable routed provider/model => classify `unknown`; do not claim governance success.
3. Requested policy uses unsupported fields or cannot be expressed on the provider surface => classify `unsupported`; keep the unsupported field list explicit.
4. Only `policy_applied` may pass, and it must include:
   - declared policy mode;
   - budget object/bound;
   - provider/model refusal lists (empty allowed, absent not allowed);
   - ZDR/DLP/data-retention constraints;
   - routing preference snapshot;
   - observable selected provider/model plus selection reason.

## Replay examples

### Apply successfully

Fixture: `tests/fixtures/provider_routing_cost_quality_policy_applied.json`

Expected result: `policy_applied` with observable route evidence.

### Fail closed when policy is missing

Fixture: `tests/fixtures/provider_routing_cost_quality_policy_missing.json`

Expected result: `policy_missing`; selected provider/model stay null so the bundle cannot pretend the default route was governed.

### Fail closed when policy exists but the route is still unknown

The validator test includes an inline dry-run where config and budget are present but there are no observable OpenRouter headers. Expected result: `unknown`, not `policy_applied`.

## Implementation artifacts in this tranche

- Validator: `scripts/provider_routing_cost_quality_evidence_validator.py`
- Regression tests: `tests/test_provider_routing_cost_quality_evidence_validator.py`
- Replay fixtures: `tests/fixtures/provider_routing_cost_quality_policy_applied.json`, `tests/fixtures/provider_routing_cost_quality_policy_missing.json`

## Non-goals / anti-drift

- Do not treat the external Auto Router news item as implementation proof.
- Do not hardcode a single provider/model as the preferred route.
- Do not silently map `policy_missing`, `unknown`, or `unsupported` to “governed enough”.
- Do not widen this into runtime enforcement before the schema/fixture layer is accepted.
