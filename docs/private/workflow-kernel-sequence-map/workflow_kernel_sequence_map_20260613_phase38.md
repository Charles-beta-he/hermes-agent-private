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
- Phase 50: Guarded Single External Send — layer=`external dispatch smoke`, gate=`single send only; no unattended dispatch`
- Phase 51: Pack Registry / Control Tower Projection — layer=`operator registry`, gate=`UI/API adoption remains separate gate`
- Phase 52: Control Tower UI/API Projection — layer=`control tower productization`, gate=`durable runtime API and unattended dispatch remain separate gates`
- Phase 53: Durable Control Tower Runtime/API Integration — layer=`live control tower snapshot`, gate=`operator projection only; no lifecycle mutation`
- Phase 54: Unattended Dispatch Policy Gate — layer=`dispatch policy`, gate=`unattended_dispatch_enabled=false; dry-run preview only`
- Phase 55: Unattended Dry-run Scheduler — layer=`dry-run scheduler`, gate=`no cron mutation; no external send`
- Phase 56: Control Tower Policy Editor / Approval UI — layer=`policy editor`, gate=`no policy activation; no unattended send`
- Phase 57: Dry-run Cron Rollout — layer=`scheduler rollout`, gate=`no external send; rollback backup required`
- Phase 58: Guarded Send Runner Activation Packet — layer=`send runner activation boundary`, gate=`manual runner not active; no send executed`
- Phase 59: Guarded Manual-send Runner Implementation — layer=`manual send runner`, gate=`no send unless --execute and packet validation pass`
- Phase 60: One-shot Guarded Send Activation — layer=`external dispatch execution`, gate=`unattended dispatch remains disabled; future sends require fresh packet/manual gate`
- Phase 61: Post-send Correlation and Compensation Watch — layer=`post-send audit`, gate=`no new message; automatic compensation disabled`
- Phase 62: Unattended Dispatch Enablement Packet — layer=`enablement packet`, gate=`unattended_dispatch_enabled=false; no cron/apply mutation`
- Phase 63: Explicit Unattended Enablement Decision Record — layer=`decision record`, gate=`selected_option=A_KEEP_MANUAL_ONLY; no apply mutation`
- Phase 64: Safe-default Manual-only Closure — layer=`closure`, gate=`repeated/unattended dispatch closed without new authorization`
- Phase 65: Manual-only Closure Monitoring Projection — layer=`closure monitoring`, gate=`no send; no cron mutation; no apply`
- Phase 66: Future Unattended Apply Reopen Packet — layer=`future reopen packet`, gate=`reopen_apply_enabled=false; selected_option=null`
- Phase 67: Single-point Reopen Implementation Choice — layer=`single implementation point choice`, gate=`choice=A_NO_REOPEN_KEEP_MANUAL_ONLY; no apply`
- Phase 68: Manual-only Governance Archive — layer=`archive`, gate=`archive/index only; no mutation`
- Phase 69: Periodic Closure Health Report — layer=`stage report`, gate=`stage_report_only=true; no publication`
- Phase 70: Governance Archive Index Publication Packet — layer=`publication packet`, gate=`publication_apply_enabled=false; private default`
- Phase 71: Private Archive Retention Watch — layer=`retention watch`, gate=`private archive retained; publication apply disabled`
- Phase 72: Publication Apply Exact-target Gate — layer=`exact-target gate`, gate=`exact target/payload required; apply disabled`
- Phase 73: Exact-target Apply Dry-run Compiler — layer=`dry-run compiler`, gate=`would_publish=false; publication apply disabled`
- Phase 74: Publication Apply Authorization Packet — layer=`authorization packet`, gate=`recommended_default=A_KEEP_PRIVATE_NO_PUBLICATION; apply disabled`
- Phase 75: Private Export Artifact Compiler — layer=`private export`, gate=`private local only; public_share_ready=false`
- Phase 76: Publication Apply Implementation Choice — layer=`implementation choice`, gate=`choice=A_KEEP_PRIVATE_NO_APPLY; no exact target`
- Phase 77: Private Export Retention Watch — layer=`export retention watch`, gate=`drift=[]; publication apply disabled`
- Phase 78: Publication Apply Reopen Packet — layer=`reopen packet`, gate=`recommended_default=A_NO_REOPEN_KEEP_PRIVATE; apply disabled`

## Next ladder
- Phase 79: Publication Closure Health Report — track=`PUBLICATION_CLOSURE_HEALTH_REPORT`, authority=`read-only health report over publication/no-apply invariants`
- Phase 80: Publication Governance Archive Update — track=`PUBLICATION_GOVERNANCE_ARCHIVE_UPDATE`, authority=`read-only archive update; no publication`

## Closed boundaries
- trigger cron jobs solely to create evidence
- credentialed model generation smoke
- external message delivery without exact target/payload/mode
- unattended external dispatch without policy gate
- broad scheduler rewrite beyond opted-in jobs
- credential or secret read/write
