# Hermes edge activation packet closed-gate landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: hermes-edge-activation-packet-closed-gate-20260701
status: implemented_local_packet_only
parent_context: `hermes-edge-benchmark-verification-gate-20260701`
source_profile: `local_edge_worker`

## Objective & cost

Success criteria:
- Continue the edge-runtime progression while explicitly keeping runtime/device/external gates closed.
- Add a generator for a non-executing runtime activation authority packet template.
- Require a passing `hermes_edge_benchmark_verification_v1` before writing the packet.
- If verification is blocked or malformed, fail closed and do not write an activation packet.

Cost function:
- Maximize future handoff clarity for a human/exact-target activation decision.
- Minimize blast radius: local packet write only, `execute=false`, no runtime activation.

## Situation model

Already landed:
- `local_edge_worker` edge runtime profile.
- `edge-benchmark` writes local dry-run receipts.
- `edge-verify` validates receipts and keeps `promotion_ready=false`.

Gap before this slice:
- There was no structured AuthorityGrant-style packet describing the exact target, rollback, kill-switch, readback, and still-closed gates for a future activation decision.

## Candidate paths

A. Activate a worker after `edge-verify` passes.
- Rejected: user explicitly said to keep closed gates; activation would cross runtime/device/executor boundary.

B. Generate a non-executing authority packet template with `execute=false`.
- Selected: advances governance/readiness without opening runtime gates.

C. Stop at verifier.
- Rejected: safe local reversible packet work exists and improves the next human-gate boundary.

## Landed change

Code:
- Added `write_edge_activation_packet(verification_path, runs_dir, target_id)`.
- Added CLI:
  ```bash
  python -m agent.reliability_harness edge-activation-packet --verification <verification.json> --target-id <target> --runs-dir <dir> --json
  ```
- Added lifecycle command contract: `edge-activation-packet`.

Packet schema:
- `schema`: `hermes_edge_activation_packet_v1`
- `status`: `authorization_required`
- `execute`: `false`
- `runtime_effect`: `local_write_packet_only`
- `profile_id`
- `target.id`
- `target.must_be_replaced_before_execution`: `true`
- `preconditions.verification_status`: `pass`
- `rollback.kill_switch_required`: `true`
- `rollback.rollback_receipt_required`: `true`
- `rollback.restore_path_required`: `true`
- `readback.required`: `true`
- `gates_remain_closed` for device, external, MCP write, publish/deploy/push, destructive, credential
- `next_authority_required`: `exact_runtime_activation_packet_execute_true`

Blocked preflight schema:
- `schema`: `hermes_edge_activation_packet_preflight_v1`
- `status`: `blocked`
- `runtime_effect`: `read_only_preflight`
- `reason`: `verification_not_passed` or `verification_not_parseable`

Tests:
- `test_module_cli_edge_activation_packet_writes_non_executing_authority_packet`
- `test_module_cli_edge_activation_packet_blocks_failed_verification`
- lifecycle manifest command list includes `edge-activation-packet`

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_packet_writes_non_executing_authority_packet tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_packet_blocks_failed_verification -q
# failed: invalid choice 'edge-activation-packet'
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_packet_writes_non_executing_authority_packet tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_packet_blocks_failed_verification -q
# 3 passed in 0.25s
```

Focused broader regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 63 passed in 1.30s
```

Syntax/diff checks:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md docs/private/hermes-edge-activation-packet-closed-gate-20260701.md
# success
```

CLI packet smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-activation-packet --verification /tmp/hermes-edge-activation-packet-20260701.verify.json --target-id local-mac-fixture --runs-dir /tmp/hermes-edge-activation-packet-20260701 --json
```

Readback:

```text
schema: hermes_edge_activation_packet_v1
status: authorization_required
execute: False
runtime_effect: local_write_packet_only
target.id: local-mac-fixture
rollback.kill_switch_required: True
readback.required: True
gates: device_control=closed, external_dispatch=closed, mcp_write_authority=closed, publish_deploy_push=closed, destructive_operation=closed, credential_entry=closed
```

CLI blocked smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-activation-packet --verification /tmp/hermes-edge-activation-packet-20260701.blocked-verification.json --target-id local-mac-fixture --runs-dir /tmp/hermes-edge-activation-packet-20260701 --json
# exit=1
```

Readback:

```text
schema: hermes_edge_activation_packet_preflight_v1
status: blocked
runtime_effect: read_only_preflight
reason: verification_not_passed
failed_checks: gate_external_dispatch_closed fail evidence=open
```

## Boundary statement

This slice writes only a local non-executing authority packet template. It does not start a worker, does not control devices, does not call MCP, does not mutate taskpool/mission/cron/gateway, does not external-dispatch, does not publish/deploy/push, does not perform destructive actions, and does not enter credentials. A future real activation still requires a fresh exact AuthorityGrant with target device/profile, `execute=true`, rollback/kill-switch, receipt, and post-action readback.
