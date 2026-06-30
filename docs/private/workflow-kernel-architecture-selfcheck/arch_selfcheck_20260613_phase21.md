> Autonomous Runtime Governance disposition:
> - Current role: source-selfcheck
> - Canonical architecture entrypoint: `/Users/charles/.hermes/hermes-agent/docs/private/autonomous-runtime-governance/00-index.md`
> - Canonical landing: `12-verification-and-acceptance.md`
> - Do not extend this file with new target architecture. Keep it as source/evidence/history; land new target-model changes in `docs/private/autonomous-runtime-governance/` and register them in `11-adr-index.md` or `13-local-brain-absorption.md`.

# Hermes Architecture Self-Check — Phase 21

Status: **DEGRADED_NOT_BLOCKED**
Overall score: **6.8/10**

## Component Scores
- **interactive runtime / tools**: 8/10 — hermes doctor core env/packages/tools mostly OK; send_message TG smoke test worked; required toolsets available except optional web/browser-cdp APIs.
- **taskpool control plane**: 7/10 — selfcheck=PASS, proof-check=PASS, task_count=351; warnings remain for edge node.
- **mission control plane**: 7/10 — mission doctor checks all ok, open_decisions=0, tick budget=60 max / board ticks_used=49; cron job itself last_status=error.
- **cron/scheduled automation**: 5/10 — diagnostics cron-job-health failed: 9 failed jobs; class provider_generation dominates; one delivery proxy failure.
- **external dispatch governance**: 7/10 — Phase19 real TG send receipt exists; Phase20 policy dry-run/allowlist/idempotency/rate-limit ledger exists; unattended sends disabled.
- **self-diagnostics**: 7/10 — diagnostics runner score=86.7 with degradation warning; modular runner detected cron health issue and taskpool schema PASS.
- **repository hygiene**: 6/10 — ## private/main...origin/private/main [ahead 47]; scoped phase changes clean after commits, but unrelated dirty files remain.

## Findings
- **P1 P1-cron-provider-generation-errors**: diagnostics cron-job-health failed: 9 cron jobs last_status=error; diagnostic score=86.7, degradation={'degraded': True, 'level': 'WARNING', 'drop': 12.0}  
  Impact: mission/health/report jobs may skip or emit stale local-only results; not blocking interactive core.  
  Next: next slice: classify provider_generation vs script/network, add cron quarantine/auto-resume packet; do not blanket restart.
- **P1 P1-upstream-ratchet-network-fetch**: hermes-upstream-ratchet-review failed on git fetch upstream main: LibreSSL SSL_connect SSL_ERROR_SYSCALL github.com:443  
  Impact: upstream drift review blind spot; local commits still scoped, but upstream delta monitoring degraded.  
  Next: add no-fetch fallback report and network/proxy diagnostic rather than treating as code failure.
- **P2 P2-config-schema-outdated**: hermes doctor: Config version outdated (v23 → v28)  
  Impact: new settings unavailable; current runtime still works.  
  Next: run hermes doctor --fix only after reviewing config diff/backup.
- **P2 P2-ui-tui-npm-audit**: hermes doctor: ui-tui workspace deps has 2 high npm vulnerabilities  
  Impact: UI/TUI package risk; not active control-plane blocker.  
  Next: run npm audit fix --workspace ui-tui in scoped branch and test ui-tui.
- **P2 P2-edge-node-lease-policy**: taskpool selfcheck PASS with warnings: edge node warning: charlesdeMacBook-Pro: heartbeat stale: 1091735s; edge node warning: charlesdeMacBook-Pro: brain_url missing; node is standalone; edge node warning: charlesdeMacBook-Pro: dangerous capabilities present: run_command; require explicit lease policy  
  Impact: edge node heartbeat/capability policy is stale; core taskpool schema still PASS.  
  Next: refresh/register edge node heartbeat and require explicit lease policy for run_command capability.
- **P2 P2-autorun-kill-switch**: eligibility-check PASS but eligible=false: reasons=['autorun_disabled', 'no_dispatchable_task'], kill_switch=True  
  Impact: safe default blocks autonomous task execution; intended unless mission wants worker throughput.  
  Next: only open per-task allowlist/lease, not global autorun.

## Positive Evidence
- No nested .hermes source leak found.
- Hermes doctor core runtime OK
- taskpool selfcheck PASS
- taskpool proof-check PASS
- mission doctor OK
- TG dispatch governance policy landed with unattended sends disabled

## Next Phase
Cron/provider degradation triage and quarantine gate: Only P1 operational degradation is scheduled automation failures; root cause split is provider/network vs script vs delivery.

## Safety Boundaries
- no external sends
- no global autorun enable
- no config migration without backup/diff
- no npm audit fix without scoped tests
