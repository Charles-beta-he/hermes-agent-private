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
- `hermes_cli.complex_task_harness` plus `hermes complex-task-harness status|checkpoint|config` for local readback, continuation receipts, and opt-in config readback
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
```

## Boundary still closed

- No push/PR.
- No model/tool schema expansion.
- No cron/taskpool lifecycle writes.
- No external dispatch.
- No destructive operation.
- No credential/payment path.
- No runtime mutation authority: `runtime_mutation_authorized=false`.
- No mark-done authority: `allowed_to_mark_done=false`.
- No full Hermes core replacement claim yet; this is a local advisory runtime scaffold with ledger/checkpoint evidence.

## Remaining slices

1. Persist `hermes_complex_task_verifier_packet_v1` packets into the local complex-task harness ledger, not only in the in-memory gate decision.
2. Add a `hermes complex-task-harness verifier` or `replay` readback command for verifier packets.
3. Add regression tests proving a real enabled-session final-response gate emits/persists verifier packets for complex completion claims.
4. Decide whether to push/PR `f01070375974f73e1c95e0cae7a805b1891ddc18` plus the post-enable doc update; requires explicit remote/branch authority.
5. Keep external dispatch, taskpool lifecycle writes, cron/worker automation, destructive operations, credentials/payments, and `runtime_mutation_authorized=true` closed until separately authorized with exact rollback/readback.
