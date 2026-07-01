# Hermes edge authority discovery landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: hermes-edge-authority-discovery-20260701
status: implemented_read_only_discovery

## Why this exists

User correction: the agent should scan existing content for exact target / packet / rollback / readback candidates before asking the user to provide them.

This slice adds that scanner. It does not open runtime/device/external boundaries.

## Landed CLI

```bash
python -m agent.reliability_harness edge-authority-discover --scan-dir <dir> [--scan-dir <dir> ...] --json
```

Output schema:

```text
hermes_edge_authority_discovery_v1
```

It reports:
- discovered edge schemas
- discovered profiles
- discovered target candidates
- discovered execute=true packets
- blockers
- recommendation

## Live scan evidence

Command:

```bash
./venv/bin/python -m agent.reliability_harness edge-authority-discover --scan-dir /tmp --scan-dir /Users/charles/.hermes/hermes-agent/docs/private --json
```

Readback:

```text
hermes_edge_authority_discovery_v1 False remain_closed_until_execute_true_packet_and_exact_runtime_readback_exist
profiles ['hermes-daemonkey-absorption-v0', 'local_edge_worker']
targets ['2026-06-21-build-shared-typescript-offline-evidence-core', '2026-06-28-为-2026-06-26-修复做梦系统重复审计无状态推进selector-cooldown-blocked-标记与智能路由-gate-细化-framing-收敛', 'local-mac-fixture']
execute_true_packets ['/tmp/hermes-edge-kill-switch-20260701/activation-execute-true.json']
blockers ['exact_runtime_activation_packet', 'exact_runtime_activation_packet_execute_true', 'fix_benchmark_receipt_or_closed_gates']
schemas ['hermes_edge_activation_packet_preflight_v1', 'hermes_edge_activation_packet_v1', 'hermes_edge_benchmark_receipt_v1', 'hermes_edge_benchmark_verification_v1', 'hermes_edge_kill_switch_dry_run_receipt_v1', 'hermes_edge_kill_switch_preflight_v1', 'hermes_edge_open_boundary_checklist_v1', 'hermes_edge_status_readback_v1']
```

## Interpretation

The scanner did find existing candidates:
- profile: `local_edge_worker`
- target candidate: `local-mac-fixture`
- closed ladder schemas
- one `execute=true` JSON in `/tmp`

But the `execute=true` artifact is a blocked-test fixture, not a valid opening authority packet. The discovery result remains `opening_ready=false` because blockers remain:
- `exact_runtime_activation_packet`
- `exact_runtime_activation_packet_execute_true`
- `fix_benchmark_receipt_or_closed_gates`

Therefore the correct behavior is to keep the boundary closed until an execute=true packet is paired with exact runtime readback, rollback/restore path, and valid status artifacts.

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_authority_discover_scans_existing_artifacts -q
# failed: invalid choice 'edge-authority-discover'
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_module_cli_edge_authority_discover_scans_existing_artifacts -q
# 2 passed in 0.15s
```

Regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 68 passed in 1.49s
```

Syntax/diff check:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py
# success
```

## Boundary statement

This scanner is read-only. It does not create authority, mutate runtime state, control devices, call MCP, external-dispatch, publish/deploy/push, perform destructive actions, enter credentials, or set `execute=true`.
