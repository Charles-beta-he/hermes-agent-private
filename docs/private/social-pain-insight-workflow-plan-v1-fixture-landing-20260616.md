# social-pain-insight -> workflow_plan_v1 Fixture Landing

Date: 2026-06-16

## Scope

The user clarified that developer, stablecoin, and browser-automation tracks can be executed sequentially and are not a single mutually exclusive implementation decision. This slice opens the safe Hermes-core path first.

## Landed code slice

File: `hermes_cli/goals.py`

Added:

- `compile_workflow_plan_v1(goal_spec, pain_clusters=...)`
- `get_social_pain_insight_fixture(category)`
- built-in fixture categories: `developer_agent`, `browser_automation`, `stablecoin_payment`
- `_stable_node_id(...)`
- `_requires_human_gate(...)`

The compiler turns `goal_spec_v1` plus public-source pain clusters into an inspectable `workflow_plan_v1` dry-run plan. The built-in fixtures provide the first local bridge from the three approved public-source dry-runs into Hermes' workflow-plan contract.

Hard safety defaults:

- `dry_run=true`
- `execution_enabled=false`
- every generated node has `execute=false`
- no taskpool/mission/cron writes
- no worker dispatch
- no external side effects
- human-gate detection for boundaries such as `human_gate`, `no_bypass`, `captcha`, `cloudflare`, `login`, `2fa`, `write`, `signing`, `custody`, `payment`, `deploy`, `secret`

## Test

File: `tests/hermes_cli/test_goals.py`

Added:

- `test_compile_workflow_plan_v1_keeps_social_pain_fixtures_dry_run_and_evidence_bound`

RED observed before implementation:

```text
ImportError: cannot import name 'compile_workflow_plan_v1' from 'hermes_cli.goals'
```

GREEN observed after implementation:

```text
1 passed
```

## Why this slice

This connects the social-pain-insight reports to Hermes' existing `/goal` control-plane work without crossing into external scraping, task dispatch, browser execution, wallet/payment actions, or production deployment.

The compiler is intentionally a contract/projection slice, not an executor.

## Inputs already available

- `/Users/charles/.hermes/docs/private/social-pain-insight-parallel-dryrun-20260616.md`
- `/Users/charles/Desktop/folder/developer_agent_ai_coding_pain_insights_public_dry_run.md`
- `/Users/charles/Desktop/folder/browser_automation_public_pain_insight_dryrun.md`
- `/Users/charles/Desktop/folder/stablecoin_wallet_payment_public_pain_dryrun.md`

## Remaining sequential slices

1. Developer fixtures: persist developer cost/context/reliability/security clusters as a reusable fixture file and wire a CLI/dry-run readback.
2. Browser automation: map anti-bot/flaky/auth/prompt-injection clusters into browser-harness safety/eval fixture; still no bypass.
3. Stablecoin: generate read-only non-custodial preflight template; still no signing/custody/financial advice.
4. Re-run taskpool selfcheck and close/transition only the completed slice with proof.
