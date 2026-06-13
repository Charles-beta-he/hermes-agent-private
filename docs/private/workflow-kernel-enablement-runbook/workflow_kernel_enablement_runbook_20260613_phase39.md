# Workflow Kernel Enablement Runbook — Phase 39

Status: **ENABLEMENT_RUNBOOK_READY**
Safe default track: `A_READ_ONLY_CONTINUE`

## Preflight commands
### git-status
```bash
git -C /Users/charles/.hermes/hermes-agent status --short --branch
```
Expected: identify unrelated dirty files before any scoped change

### runtime-summary
```bash
cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_runtime_readiness_summary.py --write-report --json
```
Expected: RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE and blocking_checks=[]

### enablement-packet
```bash
cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_runtime_enablement_packet.py --write-report --json
```
Expected: RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY

### sequence-map
```bash
cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_sequence_map.py --write-report --json
```
Expected: WORKFLOW_SEQUENCE_MAP_READY

## Dry-run commands
### wrapper-plan
```bash
HERMES_MULTI_MODEL_SMOKE_EVERY4H_PLAN_ONLY=1 HERMES_CRON_WRAPPER_RECEIPT_LEDGER=/tmp/phase39_receipts.jsonl python3 /Users/charles/.hermes/scripts/multi-model-smoke-every4h-wrapper.py
```
Expected: kernel_shim=true, runtime_mode=plan_only, cron_state_mutated=false

### correlation-readback
```bash
cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_cron_run_outcome_correlation.py --write-report --json
```
Expected: REAL_RUN_CORRELATION_PARTIAL_READY or RECEIPT_CORRELATION_PARTIAL_READY

### focused-tests
```bash
cd /Users/charles/.hermes/hermes-agent && ./scripts/run_tests.sh tests/scripts/test_workflow_runtime_enablement_packet.py tests/scripts/test_workflow_runtime_readiness_summary.py tests/scripts/test_workflow_sequence_map.py
```
Expected: all tests pass

## Rollback drill
### Step 1: Capture pre-change state
```bash
git -C /Users/charles/.hermes/hermes-agent status --short --branch && python3 -m json.tool /Users/charles/.hermes/cron/jobs.json >/tmp/phase39_jobs_before.json
```
Verify: jobs file parses; git state known

### Step 2: If B shadow artifact misbehaves
```bash
rm -f /tmp/phase39_receipts.jsonl /tmp/phase39_shadow_runtime*.json
```
Verify: only temp shadow artifacts removed

### Step 3: If scoped code change misbehaves
```bash
git -C /Users/charles/.hermes/hermes-agent revert --no-edit <scoped_commit>
```
Verify: focused tests pass after revert

### Step 4: If cron was mutated under explicit C
```bash
cp /tmp/phase39_jobs_before.json /Users/charles/.hermes/cron/jobs.json && python3 -m json.tool /Users/charles/.hermes/cron/jobs.json >/dev/null
```
Verify: cron jobs json restored and parses

### Step 5: Post-rollback readback
```bash
cd /Users/charles/.hermes/hermes-agent && ./venv/bin/python scripts/workflow_runtime_readiness_summary.py --write-report --json
```
Verify: runtime_switch_enabled_by_default=false and no unexpected blocking checks

## Go / No-go gates
- GO only if runtime summary remains READY and blocking_checks=[]
- NO-GO if cron/jobs.json has unexpected dirty changes
- NO-GO if provider probe/gate artifacts are stale
- NO-GO if explicit B/C authorization is absent for runtime/scheduler boundary crossing
