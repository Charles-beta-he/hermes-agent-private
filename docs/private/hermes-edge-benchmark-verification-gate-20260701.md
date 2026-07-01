# Hermes edge benchmark verification gate landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: hermes-edge-benchmark-verification-gate-20260701
status: implemented_read_only_default_off
parent_context: `hermes-edge-benchmark-dry-run-receipt-20260701`
source_profile: `local_edge_worker`

## Objective & cost

Success criteria:
- Add a read-only verifier for `hermes_edge_benchmark_receipt_v1` receipts.
- Classify closed-gate benchmark receipts as `pass` while still keeping `promotion_ready=false` until a later exact runtime activation authority packet exists.
- Classify malformed/open-gate receipts as `blocked` with machine-readable failed checks.
- Keep all side-effect gates closed: no worker start, no device control, no MCP write, no external dispatch, no taskpool/cron/gateway mutation, no publish/deploy/push, no destructive operation, no credential entry.

Cost function:
- Maximize promotion-gate clarity and future automation value.
- Minimize blast radius: read-only verification of a local receipt only.

## Situation model

Already landed:
- `local_edge_worker` edge runtime profile contract.
- `edge-benchmark` CLI that writes local receipt files with memory/boot and closed-gate readback.

Gap before this slice:
- Receipts were evidence, but there was no deterministic verifier to fail closed on open side-effect gates or invalid schemas.

## Candidate paths

A. Promote benchmark receipt directly to runtime activation.
- Rejected: would silently cross worker/device/executor authority.

B. Add read-only verifier and keep promotion closed.
- Selected: creates an executable safety gate without enabling runtime.

C. Docs-only checklist.
- Rejected: safe executable local verification exists.

## Landed change

Code:
- Added `verify_edge_benchmark_receipt(receipt_path)`.
- Added CLI:
  ```bash
  python -m agent.reliability_harness edge-verify --receipt <receipt.json> --json
  ```
- Added `edge-verify` lifecycle command contract to `LifecycleContractRegistry`.

Verification schema:
- `schema`: `hermes_edge_benchmark_verification_v1`
- `status`: `pass` or `blocked`
- `profile_id`
- `runtime_effect`: `read_only_verification`
- `promotion_ready`: always `false` in this slice
- `next_authority_required`: `exact_runtime_activation_packet` when checks pass
- `checks`: machine-readable check list with `name`, `status`, `evidence`

Checks:
- receipt schema is `hermes_edge_benchmark_receipt_v1`
- profile is known
- runtime effect is local-write receipt only
- memory and boot measurements are present
- memory target <= 64MB
- boot target <= 1000ms
- all side-effect gates are closed:
  - device_control
  - external_dispatch
  - mcp_write_authority
  - publish_deploy_push
  - destructive_operation
  - credential_entry

Tests:
- `test_module_cli_edge_verify_accepts_closed_gate_receipt`
- `test_module_cli_edge_verify_blocks_open_side_effect_gate`
- updated lifecycle manifest command list to include `edge-verify`

## Verification evidence

RED:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_module_cli_edge_verify_accepts_closed_gate_receipt tests/agent/test_reliability_harness.py::test_module_cli_edge_verify_blocks_open_side_effect_gate -q
# failed: invalid choice 'edge-verify'
```

GREEN targeted:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_module_cli_edge_verify_accepts_closed_gate_receipt tests/agent/test_reliability_harness.py::test_module_cli_edge_verify_blocks_open_side_effect_gate -q
# 3 passed in 0.22s
```

Focused broader regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 61 passed in 1.04s
```

Syntax/diff checks:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md docs/private/hermes-edge-benchmark-verification-gate-20260701.md
# success
```

CLI pass smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-benchmark --runs-dir /tmp/hermes-edge-verify-20260701 --profile local_edge_worker --json
./venv/bin/python -m agent.reliability_harness edge-verify --receipt /tmp/hermes-edge-verify-20260701/edge-benchmark-local_edge_worker-20260701T083534Z.json --json
```

Readback:

```text
schema: hermes_edge_benchmark_verification_v1
status: pass
promotion_ready: False
next_authority_required: exact_runtime_activation_packet
checks: 13, all pass
observed memory_rss_mb: 23.578
observed boot_ms: 4.042
```

CLI blocked smoke:

```bash
./venv/bin/python -m agent.reliability_harness edge-verify --receipt /tmp/hermes-edge-verify-20260701-bad.json --json
# exit=1
```

Readback:

```text
status: blocked
next_authority_required: fix_benchmark_receipt_or_closed_gates
gate_external_dispatch_closed: fail, evidence=open
```

## Boundary statement

This verifier is read-only. A `pass` result means only that the local dry-run receipt is structurally valid, meets observed budget targets, and proves neighboring high-risk gates remain closed. It does not authorize or perform runtime activation. Any real edge worker requires a later exact target device/profile, runtime activation packet, rollback/kill-switch, receipt, and post-action readback.
