# Hermes edge real activation — local runtime fixture — 2026-07-01

schema: hermes_real_activation_landing_v1
id: hermes-edge-real-activation-local-fixture-20260701
status: implemented_and_smoked_local_runtime_fixture

## Objective

Land the first real edge activation executor boundary while preserving safety constraints. The exact target is the existing discovered local runtime fixture:

```text
profile: local_edge_worker
target: local-mac-fixture
target kind: local_runtime_fixture
```

This is a real local runtime-fixture activation because it writes and reads back a local fixture state file, writes an activation receipt, and supports rollback. It is not a physical device activation and not an external adapter activation.

## Included authority fields

The implemented path includes all required fields:

```text
exact runtime/device/external target: local-mac-fixture (local runtime fixture)
fully bound execute=true activation packet: hermes_edge_activation_packet_v1
rollback/restore path: local-fixture-state:disabled
receipt path: packet.receipt_path -> hermes_edge_runtime_activation_receipt_v1
post-action readback command/source: edge-runtime-readback:local-mac-fixture
human/operator acknowledgement: hashed into packet as text_sha256
separate executor boundary: edge-runtime-activate after edge-activation-preflight pass
```

## New CLI commands

```bash
python -m agent.reliability_harness edge-activation-authorize \
  --target-id local-mac-fixture \
  --runs-dir <dir> \
  --rollback-restore-path local-fixture-state:disabled \
  --post-action-readback edge-runtime-readback:local-mac-fixture \
  --human-ack 'human/operator acknowledgement: approve execute=true local runtime fixture activation for local-mac-fixture' \
  --json
```

Writes a fully bound execute=true authorization packet. It does not mutate runtime state.

```bash
python -m agent.reliability_harness edge-runtime-activate \
  --preflight <preflight.json> \
  --activation-packet <execute-true-packet.json> \
  --runs-dir <dir> \
  --json
```

Consumes a passing preflight and execute=true packet, writes local fixture state to active, writes activation receipt, and immediately reads back status.

```bash
python -m agent.reliability_harness edge-runtime-rollback \
  --activation-receipt <activation-receipt.json> \
  --json
```

Restores local fixture state to disabled and writes rollback receipt/readback.

## Live smoke evidence

Command sequence ran under:

```text
/tmp/hermes-edge-real-activation-20260701
```

Readback:

```json
{
  "packet_path": "/tmp/hermes-edge-real-activation-20260701/edge-activation-authorize-local-mac-fixture-07efce7229840785.json",
  "discovery_opening_ready": true,
  "preflight_status": "pass",
  "activation_receipt": "/tmp/hermes-edge-real-activation-20260701/edge-runtime-activation-receipt-local-mac-fixture-20260701T123005Z.json",
  "activation_status_after": "active",
  "activation_runtime_effect": "local_runtime_fixture_state_write",
  "activation_execute": true,
  "rollback_receipt": "/tmp/hermes-edge-real-activation-20260701/edge-runtime-rollback-receipt-20260701T123005Z.json",
  "rollback_status_after": "disabled",
  "rollback_runtime_effect": "local_runtime_fixture_state_write",
  "closed_gates": {
    "device_control": "closed",
    "external_dispatch": "closed",
    "mcp_write_authority": "closed",
    "publish_deploy_push": "closed",
    "destructive_operation": "closed",
    "credential_entry": "closed"
  }
}
```

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_real_activation_local_fixture_roundtrip tests/agent/test_reliability_harness.py::test_module_cli_edge_runtime_activate_rejects_failed_preflight -q
# failed: invalid choice edge-activation-authorize / edge-runtime-activate
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_module_cli_edge_real_activation_local_fixture_roundtrip tests/agent/test_reliability_harness.py::test_module_cli_edge_runtime_activate_rejects_failed_preflight -q
# 3 passed in 0.39s
```

Regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 73 passed in 2.48s
```

Additional hardening evidence after independent verifier suggestion:

```text
edge-runtime-activate rejects packet.receipt_path outside runs_dir before writing fixture state.
receipt_path_under_runs_dir=true in final smoke.
```

Syntax/diff check:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py
# success
```

## Boundary statement

Opened boundary:

```text
local runtime fixture state write for exact target local-mac-fixture
```

Still closed:

```text
physical device control
external dispatch
MCP write authority
publish/deploy/push
destructive operation
credential entry
taskpool/mission/cron/gateway mutation
payment/broad fan-out
```

Rollback was executed in the live smoke and read back `disabled`.
