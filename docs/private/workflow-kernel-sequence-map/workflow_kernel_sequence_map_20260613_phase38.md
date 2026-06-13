# Workflow Kernel Sequence Map — Phase 38

Status: **WORKFLOW_SEQUENCE_MAP_READY**
Safe default track: `EXTERNAL_DISPATCH_GOVERNED_EXPANSION`

## Phase order
- Phase 17: External Apply Requirements — layer=`contract`, gate=`real_apply_enabled=false`
- Phase 18: Local External Apply Outbox — layer=`local outbox`, gate=`external_side_effects_enabled=false`
- Phase 19: Single Explicit External Dispatch Smoke — layer=`external apply smoke`, gate=`no unattended sends`
- Phase 20: Governed Notification Policy — layer=`policy`, gate=`unattended_send_enabled=false`
- Phase 21: Guarded Manual-send Runner — layer=`manual dispatch`, gate=`no unattended dispatch`
- Phase 22: Cron Provider Degradation Triage — layer=`diagnostics`, gate=`no cron mutation/rerun`
- Phase 23: Provider Network Probe Ledger — layer=`network evidence`, gate=`no LLM prompt/credentials`
- Phase 24: Cron Provider Health Gate — layer=`decision compiler`, gate=`no scheduler mutation`
- Phase 25: Architecture Migration Evaluation — layer=`migration strategy`, gate=`no big-bang rewrite`
- Phase 26: Kernel Scaffold — layer=`runtime scaffold`, gate=`RUNTIME_SWITCH_ENABLED=false`
- Phase 27: Runtime Switch Readiness — layer=`switch readiness`, gate=`explicit enable required`
- Phase 28: Daily-full Wrapper Gate Rollout — layer=`single wrapper opt-in`, gate=`no cron/jobs.json edit`
- Phase 29: Every-4h Wrapper Exclusion Hint — layer=`second wrapper opt-in`, gate=`no generation during verification`
- Phase 30: Xiaomi Probe Calibration — layer=`probe correctness`, gate=`edge reachability only, not credential validity`
- Phase 31: Cron Wrapper Kernel Shim — layer=`kernel shim`, gate=`global switch remains false`
- Phase 32: Scheduler Runtime Projection — layer=`scheduler readback`, gate=`no cron execution`
- Phase 33: Scheduled-run Outcome Correlation — layer=`runtime evidence`, gate=`partial until both wrappers run`
- Phase 34: Wrapper Run Receipt Ledger — layer=`audit receipts`, gate=`verification uses override ledger`
- Phase 35: Receipt-aware Correlation — layer=`correlation upgrade`, gate=`do not trigger cron to create receipts`
- Phase 36: Runtime Readiness Summary — layer=`operator summary`, gate=`summary is not enablement authority`
- Phase 37: Runtime Enablement Authorization Packet — layer=`human gate`, gate=`B/C require explicit authorization`
- Phase 38: Sequence Map and Ordering — layer=`workflow map`, gate=`do not cross enablement boundary`
- Phase 39: Enablement Runbook and Rollback Drill — layer=`operator runbook`, gate=`do not execute runbook as authorization`
- Phase 40: Receipt/Projection Drift Watcher — layer=`drift monitoring`, gate=`do not trigger cron solely for receipts`
- Phase 41: Single-wrapper Runtime-enable Shadow Dry-run — layer=`runtime-adjacent dry-run`, gate=`no cron mutation, no credentialed generation`
- Phase 42: Single-wrapper Scheduler-native Rollout — layer=`scheduler-native single job`, gate=`one job script field only; rollback backup required`
- Phase 43: Daily-wrapper Receipt Closure — layer=`receipt closure`, gate=`plan-only receipt; no credentialed generation required for proof`
- Phase 44: Daily-wrapper Scheduler-native Rollout — layer=`scheduler-native second opted-in job`, gate=`daily job script field only; rollback backup required`
- Phase 45: Runtime Switch Auto-enabled — layer=`runtime switch`, gate=`future external dispatch/broad scheduler rewrites still scoped`
- Phase 46: Post-auto-enable Scheduled Observation — layer=`runtime observation`, gate=`do not trigger cron solely to create post-projection evidence`
- Phase 47: External Dispatch Expansion Candidate Packet — layer=`external dispatch governance`, gate=`no external send without exact target/payload/mode`
- Phase 48: External Dispatch Policy Gate Local Outbox — layer=`external dispatch policy gate`, gate=`external adapters still require exact target/payload/mode`
- Phase 49: Workflow Pack / Agent Pack Schema — layer=`pack productization`, gate=`runtime adoption and external send remain separate gates`
- Phase 51: Pack Registry / Control Tower Projection — layer=`operator registry`, gate=`UI/API adoption and external send remain separate gates`

## Next ladder
- Phase 50: Guarded Single External Send — track=`GUARDED_EXTERNAL_SEND`, authority=`exact adapter/target/payload/mode/idempotency/rollback`
- Phase 52: Control Tower UI/API Projection — track=`CONTROL_TOWER_PRODUCTIZATION`, authority=`larger product direction choice`

## Closed boundaries
- trigger cron jobs solely to create evidence
- credentialed model generation smoke
- external message delivery without exact target/payload/mode
- unattended external dispatch without policy gate
- broad scheduler rewrite beyond opted-in jobs
- credential or secret read/write
