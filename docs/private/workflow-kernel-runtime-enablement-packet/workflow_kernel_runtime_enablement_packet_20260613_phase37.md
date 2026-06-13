# Workflow Kernel Runtime Enablement Packet — Phase 37

Status: **RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY**
Decision required: `True`

## Decision question
下一步是否授权跨越 runtime/scheduler enablement 边界，还是继续只读/等待自然 receipt？

## Options
### A_READ_ONLY_CONTINUE: 继续只读/可逆架构层
- Authority: none beyond current bounded continuation
- Side effects: local docs/tests/taskpool evidence only
- Blast radius: lowest
- Rollback: git revert scoped commits; no runtime state mutation
- Recommended default: `True`

### B_SINGLE_WRAPPER_RUNTIME_ENABLE_DRY_RUN: 授权单 wrapper runtime enable dry-run / shadow projection
- Authority: explicit human authorization for runtime-switch-adjacent dry-run
- Side effects: local runtime/config shadow artifact only; no cron mutation
- Blast radius: low-medium
- Rollback: delete/ignore shadow artifact; git revert scoped code if needed
- Recommended default: `False`

### C_SINGLE_WRAPPER_SCHEDULER_NATIVE_ROLLOUT: 授权单 wrapper scheduler-native 接管
- Authority: explicit human authorization to mutate scheduler/runtime boundary
- Side effects: may modify scheduler/runtime execution path for exactly one opted-in job
- Blast radius: medium
- Rollback: restore previous scheduler path/flag; verify cron jobs.json and wrapper logs
- Recommended default: `False`

### D_WAIT_FOR_NATURAL_RECEIPTS: 等待真实 scheduled runs 自然产生 receipt-backed full correlation
- Authority: none
- Side effects: none now; future scheduled wrappers append receipts
- Blast radius: lowest but time-dependent
- Rollback: not needed; append-only receipts are audit evidence
- Recommended default: `False`

## Closed boundaries without explicit authorization
- enable workflow_kernel.RUNTIME_SWITCH_ENABLED
- mutate ~/.hermes/cron/jobs.json
- trigger cron jobs solely to create evidence
- scheduler-native takeover for any job
- model generation smoke using credentials
- external message delivery
