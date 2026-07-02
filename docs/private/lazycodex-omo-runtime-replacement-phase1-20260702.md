# LazyCodex/OmO → Hermes runtime replacement phase 1

Date: 2026-07-02
Worktree: `/Users/charles/.hermes/runs/hermes-lazycodex-runtime-kernel`
Branch: `lazycodex-runtime-kernel-20260702`

## Objective model

User intent: continue LazyCodex/OmO learning until Hermes core/runtime replacement.

Interpretation: replace Hermes' prompt-only, implicit complex-task handling with a Hermes-native, evidence-bound runtime kernel. Do not import LazyCodex/OmO code or turn Hermes into Codex/OpenCode. Transfer relationships: iceberg scan → SSOT/evidence gate → default-off runtime scaffold → later verified execution gates.

## Success criteria for this slice

1. Add a deterministic iceberg complexity scanner to Hermes runtime primitives.
2. Keep default behavior off and cache-safe.
3. Classify LazyCodex/OmO + Hermes core/runtime replacement as complex.
4. Keep trivial LazyCodex explanation requests lightweight.
5. Wire the scan into the existing conductor intent-router path only when `conductor.complex_task_harness.enabled` is explicitly true.
6. Add local continuation/checkpoint and config readback without turning the harness on by default.
7. Preserve no-side-effect and no-runtime-mutation authority by construction.

## Implemented

- `agent.conductor_runtime.IcebergComplexityScanner`
- `default_conductor_config()["conductor"]["complex_task_harness"]`, default-off
- `_run_conductor_intent_router(...)` attaches `iceberg_complexity_scan` only when the harness gate is enabled
- `MainloopVerificationGate.evaluate_turn(..., complexity_scan=...)` promotes complex-scan completion claims to T3, adds the `complex_task_harness` surface, and emits a local `hermes_complex_task_verifier_packet_v1`
- `_run_mainloop_verification_gate(...)` forwards the last conductor iceberg scan into final-response verification
- `agent.conductor_runtime.ComplexTaskHarnessLedger`, an append-only local JSONL receipt ledger for scan/evidence/checkpoint continuation
- `_run_conductor_intent_router(...)` records a local receipt only when `complex_task_harness.ledger_enabled` is explicitly true
- `hermes_cli.complex_task_harness` plus `hermes complex-task-harness status|checkpoint|config|verifier` for local readback, continuation receipts, opt-in config readback, and persisted verifier packet replay
- `conductor_defaults_enabled(...)` treats `complex_task_harness.enabled` / `ledger_enabled` as explicit opt-in while defaults remain closed
- Tests in `tests/agent/test_conductor_runtime.py` and `tests/hermes_cli/test_complex_task_harness.py`

## Verification evidence

Commands run from worktree:

```bash
/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_conductor_runtime.py -q
# 28 passed in 0.29s

/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/hermes_cli/test_complex_task_harness.py tests/agent/test_conductor_runtime.py::test_complex_task_harness_ledger_records_checkpoint_receipt -q
# 3 passed in 0.15s

/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_conductor_runtime.py tests/agent/test_system_prompt.py tests/agent/test_system_prompt_restore.py -q
# 44 passed, 3 warnings in 2.91s

/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_conductor_runtime.py tests/hermes_cli/test_complex_task_harness.py tests/agent/test_system_prompt.py tests/agent/test_system_prompt_restore.py -q
# 53 passed, 3 warnings in 3.09s

/Users/charles/.hermes/hermes-agent/venv/bin/python -m ruff check agent/conductor_runtime.py agent/conversation_loop.py hermes_cli/complex_task_harness.py hermes_cli/subcommands/complex_task_harness.py hermes_cli/main.py hermes_cli/profiles.py tests/agent/test_conductor_runtime.py tests/hermes_cli/test_complex_task_harness.py
# All checks passed!

/Users/charles/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main complex-task-harness status --ledger "$LEDGER" --json
# returned ok=true, mode=local_readback_only, event_count=1, allowed_to_mark_done=false

/Users/charles/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main complex-task-harness checkpoint --ledger "$LEDGER" --scan-id "$SCAN_ID" --objective 'continue runtime replacement' --completed-evidence 'pytest:3 passed' --pending-action 'run full regression' --rollback 'remove temp ledger' --json
# returned ok=true, mode=local_receipt_only, receipt.event_type=checkpoint, allowed_to_mark_done=false

/Users/charles/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main complex-task-harness config --json
# returned ok=true, mode=config_readback_only, complex_task_harness.enabled=false, complex_task_harness.ledger_enabled=false, allowed_to_mark_done=false
```

## Main repo landing / local enablement readback

This slice was later landed into the main Hermes repo and enabled only for the local profile.

- Main repo commit: `f01070375974f73e1c95e0cae7a805b1891ddc18` (`feat(agent): enable complex task harness scaffold`)
- Local config path: `/Users/charles/.hermes/config.yaml`
- Enabled local flags:
  - `conductor.complex_task_harness.enabled = true`
  - `conductor.complex_task_harness.ledger_enabled = true`
  - `conductor.complex_task_harness.runtime_mutation_authorized = false`
  - `conductor.complex_task_harness.mode = iceberg_scan_advisory`
  - `conductor.complex_task_harness.output_schema = hermes_iceberg_complexity_scan_v1`
- Local ledger: `/Users/charles/.hermes/state/complex-task-harness/ledger.jsonl`
- Live smoke recorded scan/checkpoint under `complex-scan-a8cbdc761677591e` with `allowed_to_mark_done=false` and `external_side_effects_enabled=false`.
- Follow-up slice persisted `hermes_complex_task_verifier_packet_v1` packets into the local ledger and added `hermes complex-task-harness verifier` readback.
- `runtime_mutation_authorized` was enabled only after explicit user authority; scan readback now records `runtime_mutation_authorized=true` and `side_effect_ceiling=local_runtime_mutation_with_receipt`; mark-done authority remains false.
- Follow-up adapter slice added `hermes complex-task-harness taskpool-lifecycle` and `external-dispatch` exact adapter commands. The taskpool adapter requires scan/task/status/evidence/readback and defaults to receipt-only unless `--execute` runs the fixed brain transition command. The external adapter requires platform/target/payload digest/readback, stores only digest/preview, rejects hash drift/secrets, and only executes the `local_harness` boundary without real platform send.

Post-enable verification from main repo:

```bash
/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_conductor_runtime.py tests/hermes_cli/test_complex_task_harness.py tests/agent/test_system_prompt.py tests/agent/test_system_prompt_restore.py -q
# 54 passed, 3 warnings in 2.46s

/Users/charles/.hermes/hermes-agent/venv/bin/python -m ruff check agent/conductor_runtime.py agent/conversation_loop.py hermes_cli/complex_task_harness.py hermes_cli/subcommands/complex_task_harness.py hermes_cli/main.py hermes_cli/profiles.py tests/agent/test_conductor_runtime.py tests/hermes_cli/test_complex_task_harness.py
# All checks passed!

hermes complex-task-harness config --json
# returned ok=true, complex_task_harness.enabled=true, complex_task_harness.ledger_enabled=true, runtime_mutation_authorized=false, allowed_to_mark_done=false

hermes complex-task-harness status --limit 10 --json
# returned ok=true, event_count=2, latest_scan_id=complex-scan-a8cbdc761677591e, allowed_to_mark_done=false

hermes complex-task-harness verifier --json
# returns persisted verifier_packet events with mode=verifier_packet_readback_only and allowed_to_mark_done=false
```

Follow-up local verification:

```bash
/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_conductor_runtime.py::test_conversation_loop_persists_verifier_packet_when_complex_completion_claim_is_gated tests/hermes_cli/test_complex_task_harness.py -q
# verifier-persistence focused tests passed

/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/agent/test_conductor_runtime.py tests/hermes_cli/test_complex_task_harness.py tests/agent/test_system_prompt.py tests/agent/test_system_prompt_restore.py -q
# 57 passed, 3 warnings in 3.32s

/Users/charles/.hermes/hermes-agent/venv/bin/python -m pytest tests/hermes_cli/test_complex_task_harness.py -q
# exact adapter tests passed for taskpool lifecycle receipt, evidence rejection, external digest-bound outbox, hash mismatch rejection
```

Adapter smoke readback:

```bash
hermes complex-task-harness taskpool-lifecycle --scan-id complex-scan-eda306bda874af10 --task-id taskpool-adapter-smoke-no-write --from-status review --to-status done --evidence-ref proof:complex-task-harness-adapter-smoke --reason 'exact taskpool lifecycle adapter smoke without canonical transition' --readback-ref 'command:hermes complex-task-harness status --json' --json
# recorded event_type=taskpool_lifecycle_adapter, execute_requested=false, allowed_to_mark_done=false

hermes complex-task-harness external-dispatch --scan-id complex-scan-eda306bda874af10 --platform local_harness --target local://complex-task-harness/smoke --payload 'local harness smoke' --payload-sha256 sha256:8e56844b774193e7440332a547518d2592f692988e806cbfdc092be1c4be22a9 --readback-ref 'command:hermes complex-task-harness status --json' --execute --json
# recorded event_type=external_dispatch_adapter, send_executed=true, platform_message_id=local-harness-dispatch-8e56844b774193e7, allowed_to_mark_done=false
```

## Boundary still closed

- No push/PR.
- No model/tool schema expansion.
- No cron/taskpool lifecycle writes.
- No external dispatch.
- No destructive operation.
- No credential/payment path.
- No runtime mark-done authority: `allowed_to_mark_done=false`.
- Runtime mutation authority is explicitly enabled in local config only after user authorization; external/taskpool/destructive/credential gates still require exact target receipts.
- No full Hermes core replacement claim yet; this is a local advisory runtime scaffold with ledger/checkpoint/verifier evidence.

## Remaining slices

1. For a real taskpool lifecycle write, provide/use a specific canonical task id whose current status matches `--from-status`; then run `taskpool-lifecycle --execute` and read back `brain_task_orchestrator.py list --include-done --json`.
2. For a real platform external dispatch, add a platform-specific sender boundary that consumes an existing `external_dispatch_adapter` receipt and requires exact channel/target/token-source reference/post-send readback; current generic adapter intentionally blocks real non-`local_harness` sends.
3. No `allowed_to_mark_done=true` until independent verifier evidence and taskpool proof gate agree on a specific task/scan id.
