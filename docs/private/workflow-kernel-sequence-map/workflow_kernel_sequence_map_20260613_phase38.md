# Workflow Kernel Sequence Map — Phase 38

Status: **WORKFLOW_SEQUENCE_MAP_READY**
Safe default track: `A_READ_ONLY_CONTINUE`

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

## Next ladder
- Phase 39: Enablement Runbook and Rollback Drill — track=`A_READ_ONLY_CONTINUE`, authority=`none`
- Phase 40: Receipt/Projection Drift Watcher — track=`A_READ_ONLY_CONTINUE`, authority=`none`
- Phase 41: Single-wrapper Runtime-enable Dry-run — track=`B_SINGLE_WRAPPER_RUNTIME_ENABLE_DRY_RUN`, authority=`explicit B`
- Phase 42: Single-wrapper Scheduler-native Rollout — track=`C_SINGLE_WRAPPER_SCHEDULER_NATIVE_ROLLOUT`, authority=`explicit C`

## Closed boundaries
- enable workflow_kernel.RUNTIME_SWITCH_ENABLED
- mutate ~/.hermes/cron/jobs.json
- trigger cron jobs solely to create evidence
- scheduler-native takeover
- credentialed model generation smoke
- external message delivery
