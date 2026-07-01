# Hermes edge activation preflight landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: hermes-edge-activation-preflight-20260701
status: implemented_read_only_fail_closed_preflight

## Why this exists

After authority discovery, the next safe step is not to execute a runtime/device/external boundary immediately. It is to consume the discovered artifacts and prove whether a target has a fully bound `execute=true` activation packet with rollback/readback/receipt fields.

This preflight prevents a test fixture or partial `execute=true` JSON from being mistaken for real authority.

## Landed CLI

```bash
python -m agent.reliability_harness edge-activation-preflight --discovery <discovery.json> --target-id <target> --json
```

Output schema:

```text
hermes_edge_activation_preflight_v1
```

Pass requires:
- discovery schema is `hermes_edge_authority_discovery_v1`
- discovery has `opening_ready=true`
- target id appears in discovered target candidates
- an execute=true packet exists with:
  - schema `hermes_edge_activation_packet_v1`
  - exact matching target id
  - rollback.kill_switch_required=true
  - rollback.restore_path_required=true
  - readback.required=true
  - readback.post_action_status_required=true
  - receipt_path present

Even pass remains read-only: `runtime_effect=read_only_preflight`, `execute=false`.

## Live preflight evidence

Command:

```bash
./venv/bin/python -m agent.reliability_harness edge-authority-discover --scan-dir /tmp --scan-dir /Users/charles/.hermes/hermes-agent/docs/private --json > /tmp/hermes-edge-authority-discovery-preflight.json
./venv/bin/python -m agent.reliability_harness edge-activation-preflight --discovery /tmp/hermes-edge-authority-discovery-preflight.json --target-id local-mac-fixture --json
```

Readback:

```text
schema: hermes_edge_activation_preflight_v1
runtime_effect: read_only_preflight
target_id: local-mac-fixture
execute: false
status: blocked
reason: discovery_not_opening_ready
blockers: exact_runtime_activation_packet, exact_runtime_activation_packet_execute_true, fix_benchmark_receipt_or_closed_gates
execute_true_packets: /tmp/hermes-edge-kill-switch-20260701/activation-execute-true.json
exit=1
```

Interpretation: existing artifacts include a test `execute=true` packet, but discovery is not opening-ready and blockers remain. Therefore the real edge activation boundary is correctly blocked.

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_preflight_blocks_discovery_without_valid_execute_packet tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_preflight_passes_only_fully_bound_discovery -q
# failed: invalid choice 'edge-activation-preflight'
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_preflight_blocks_discovery_without_valid_execute_packet tests/agent/test_reliability_harness.py::test_module_cli_edge_activation_preflight_passes_only_fully_bound_discovery -q
# 3 passed in 0.18s
```

Regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 70 passed in 1.55s
```

Syntax/diff check:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py
# success
```

## Boundary statement

This is a read-only preflight. It does not create authority, set execute=true, start/stop a runtime, control a device, call MCP, external-dispatch, publish/deploy/push, perform destructive actions, enter credentials, or mutate taskpool/mission/cron/gateway.
