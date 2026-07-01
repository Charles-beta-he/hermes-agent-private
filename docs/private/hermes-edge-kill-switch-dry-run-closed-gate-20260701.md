# Hermes edge kill-switch dry-run closed-gate landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: hermes-edge-kill-switch-dry-run-closed-gate-20260701
status: implemented_local_receipt_only
parent_context: `hermes-edge-activation-packet-closed-gate-20260701`
source_profile: `local_edge_worker`

## Objective & cost

Success criteria:
- Advance the closed-boundary/openability ladder by proving a future edge activation packet has a kill-switch/rollback/readback shape.
- Add a dry-run command that writes a disabled/rollback receipt only when the activation packet is non-executing (`execute=false`).
- Fail closed if the activation packet is malformed or `execute=true`.
- Keep all runtime/device/external gates closed.

Cost function:
- Maximize future activation safety and rollback clarity.
- Minimize blast radius: local receipt write only, no worker contact, no runtime disable, no device/MCP/external side effect.

## Situation model

Already landed:
- `edge-benchmark` local receipt.
- `edge-verify` read-only verifier.
- `edge-activation-packet` non-executing authority packet with `execute=false`.

Gap before this slice:
- The activation packet required rollback/kill-switch/readback fields, but there was no executable dry-run receipt proving the disable/readback path shape.

## Candidate paths

A. Implement real kill switch / disable runtime.
- Rejected: would cross runtime/device/executor boundary.

B. Add local dry-run receipt that accepts only `execute=false` activation packets.
- Selected: proves the closed rollback path while keeping all effects local.

C. Docs-only rollback checklist.
- Rejected: safe executable local receipt work exists.

## Landed change

Code:
- Added `write_edge_kill_switch_dry_run(activation_packet_path, runs_dir)`.
- Added CLI:
  ```bash
  python -m agent.reliability_harness edge-kill-switch-dry-run --activation-packet <packet.json> --runs-dir <dir> --json
  ```
- Added lifecycle command contract: `edge-kill-switch-dry-run`.

Receipt schema:
- `schema`: `hermes_edge_kill_switch_dry_run_receipt_v1`
- `status`: `pass`
- `execute`: `false`
- `runtime_effect`: `local_write_receipt_only`
- `status_after_dry_run`: `disabled`
- `rollback_verified`: `true`
- `readback.simulated_status`: `disabled`
- `gates_remain_closed`: device/external/MCP/publish/destructive/credential remain closed

Blocked preflight schema:
- `schema`: `hermes_edge_kill_switch_preflight_v1`
- `status`: `blocked`
- `runtime_effect`: `read_only_preflight`
- `reason`: `activation_packet_not_parseable`, `invalid_activation_packet_schema`, or `execute_true_not_allowed_for_dry_run`

Tests:
- `test_module_cli_edge_kill_switch_dry_run_writes_disabled_receipt`
- `test_module_cli_edge_kill_switch_dry_run_blocks_execute_true_packet`
- lifecycle manifest command list includes `edge-kill-switch-dry-run`

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_kill_switch_dry_run_writes_disabled_receipt tests/agent/test_reliability_harness.py::test_module_cli_edge_kill_switch_dry_run_blocks_execute_true_packet -q
# failed: invalid choice 'edge-kill-switch-dry-run'
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_module_cli_edge_kill_switch_dry_run_writes_disabled_receipt tests/agent/test_reliability_harness.py::test_module_cli_edge_kill_switch_dry_run_blocks_execute_true_packet -q
# 3 passed in 0.19s
```

Focused broader regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 65 passed in 1.28s
```

Syntax/diff checks:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md docs/private/hermes-edge-kill-switch-dry-run-closed-gate-20260701.md
# success
```

CLI dry-run smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-kill-switch-dry-run --activation-packet /tmp/hermes-edge-kill-switch-20260701/activation.json --runs-dir /tmp/hermes-edge-kill-switch-20260701 --json
```

Readback:

```text
schema: hermes_edge_kill_switch_dry_run_receipt_v1
status: pass
execute: False
runtime_effect: local_write_receipt_only
status_after_dry_run: disabled
rollback_verified: True
readback.simulated_status: disabled
gates: device_control=closed, external_dispatch=closed, mcp_write_authority=closed, publish_deploy_push=closed, destructive_operation=closed, credential_entry=closed
```

CLI blocked smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-kill-switch-dry-run --activation-packet /tmp/hermes-edge-kill-switch-20260701/activation-execute-true.json --runs-dir /tmp/hermes-edge-kill-switch-20260701 --json
# exit=1
```

Readback:

```text
schema: hermes_edge_kill_switch_preflight_v1
status: blocked
runtime_effect: read_only_preflight
reason: execute_true_not_allowed_for_dry_run
```

## Boundary statement

This slice writes only a local kill-switch dry-run receipt. It does not start, stop, disable, or contact any real worker/runtime/device. It does not call MCP, mutate taskpool/mission/cron/gateway, external-dispatch, publish/deploy/push, perform destructive actions, or enter credentials. Any real kill switch or runtime disable still requires a fresh exact AuthorityGrant with target, rollback/restore path, receipt, and post-action readback.
