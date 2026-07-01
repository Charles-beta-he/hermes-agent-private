# Hermes edge benchmark dry-run receipt landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: hermes-edge-benchmark-dry-run-receipt-20260701
status: implemented_local_default_off
parent_context: `picoclaw-edge-runtime-profile-hermes-reliability-20260701`
source_profile: `local_edge_worker`

## Objective & cost

Success criteria:
- Turn the previously landed edge-runtime profile contract into an executable local dry-run receipt surface.
- Add a CLI that writes a benchmark/readiness receipt without starting an edge worker, controlling devices, calling MCP, dispatching externally, or deploying.
- Persist measurable fields that future edge-worker promotion gates can compare: memory RSS, boot/startup elapsed time, profile id, and closed side-effect gates.
- Verify with test-first RED/GREEN, full focused regression, py_compile, diff check, and real CLI smoke.

Cost function:
- Maximize runtime-readiness evidence and future promotion-gate usefulness.
- Minimize blast radius: local file write only, no live scheduler/taskpool/gateway/device side effects.

## Situation model

Already landed:
- `LifecycleContractRegistry` includes `edge_runtime` and `local_edge_worker`.
- `capabilities --json` readback exposes `edge-profile` and `edge-benchmark` command contracts.
- Eval trajectories include `edge_runtime_profile_ids`.

Gap before this slice:
- `edge-benchmark` existed only as a contract entry; it did not have an executable CLI receipt surface.

## Candidate paths

A. Run PicoClaw or a real edge worker benchmark.
- Rejected: crosses external runtime/device/MCP/executor boundaries.

B. Add a Hermes-owned local dry-run receipt command.
- Selected: produces real executable evidence while keeping neighboring gates closed.

C. Docs-only update.
- Rejected: user approved continued strengthening and safe executable work exists.

## Landed change

Code:
- Added `_memory_rss_mb()` measurement helper.
- Added `write_edge_benchmark_receipt(runs_dir, profile_id)`.
- Added CLI subcommand:
  ```bash
  python -m agent.reliability_harness edge-benchmark --runs-dir <dir> --profile local_edge_worker --json
  ```
- The command writes `edge-benchmark-local_edge_worker-<timestamp>.json` under the selected runs dir.

Receipt schema:
- `schema`: `hermes_edge_benchmark_receipt_v1`
- `profile_id`
- `ts`
- `runtime_effect`: `local_write_receipt_only`
- `measurements.memory_rss_mb`
- `measurements.boot_ms`
- `measurements.python`
- `measurements.platform`
- closed gates:
  - device_control
  - external_dispatch
  - mcp_write_authority
  - publish_deploy_push
  - destructive_operation
  - credential_entry
- `resource_budget`
- `allowed_capabilities`
- `forbidden_by_default`
- `authority_boundary`

Tests:
- Added `test_module_cli_edge_benchmark_writes_local_receipt`.
- Test asserts CLI success, receipt file existence, schema/profile/runtime effect, measurements, and closed side-effect gates.

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_benchmark_writes_local_receipt -q
# failed: invalid choice 'edge-benchmark' (choose from 'run', 'list', 'capabilities')
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_benchmark_writes_local_receipt -q
# 1 passed in 0.16s
```

Focused broader regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 59 passed in 0.97s
```

Syntax/diff checks:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md docs/private/hermes-edge-benchmark-dry-run-receipt-20260701.md
# success
```

CLI smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-benchmark --runs-dir /tmp/hermes-edge-benchmark-receipt-20260701 --profile local_edge_worker --json
# {"ok": true, "profile_id": "local_edge_worker", "receipt_path": "/tmp/hermes-edge-benchmark-receipt-20260701/edge-benchmark-local_edge_worker-20260701T082212Z.json"}
```

Receipt readback:

```text
schema: hermes_edge_benchmark_receipt_v1
profile_id: local_edge_worker
runtime_effect: local_write_receipt_only
measurements.memory_rss_mb >= 0: True
measurements.boot_ms >= 0: True
gates: device_control=closed, external_dispatch=closed, mcp_write_authority=closed, publish_deploy_push=closed, destructive_operation=closed, credential_entry=closed
```

## Boundary statement

This is a local dry-run receipt surface only. It does not install/run PicoClaw, does not start a worker, does not mutate taskpool/mission/cron/gateway, does not call MCP, does not control devices, does not dispatch externally, and does not publish/deploy/push. A future real edge-worker activation still requires exact target device, authority packet, rollback/kill switch, receipt, and readback.
