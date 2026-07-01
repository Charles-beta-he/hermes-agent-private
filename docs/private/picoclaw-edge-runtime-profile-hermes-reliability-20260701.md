# PicoClaw edge-runtime pattern → Hermes reliability harness landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: picoclaw-edge-runtime-profile-hermes-reliability-20260701
status: implemented_local_default_off
source_media: `/Users/charles/.hermes/docs/private/wechat-sph-AtYM5um9fZ/`
source_repo: `https://github.com/sipeed/picoclaw`

## Objective & cost

Success criteria:
- Absorb the verified PicoClaw pattern as a Hermes-owned edge-runtime contract, not as a runtime dependency.
- Extend the offline Agent Reliability Harness manifest with a tiny/local worker profile and edge benchmark lifecycle commands.
- Keep live gateway, taskpool, memory, cron, MCP write, external dispatch, device control, publish/deploy, destructive, and credential gates closed.
- Verify with RED/GREEN tests, CLI readback, trajectory readback, and focused regression.

Cost function:
- Maximize reusable edge-agent structure for Hermes control-plane / worker separation.
- Minimize blast radius, runtime coupling, uncontrolled device side effects, and marketing-claim adoption.

## Situation model

Evidence from media/repo:
- The WeChat preview and cover identify `PicoClaw` as a lightweight local AI Agent / local automation lead.
- The public page is QR/App gated; full video playback was not available from the browser preview.
- Direct GitHub verification identified `sipeed/picoclaw`, a Go/MIT repo with README claims around low memory, fast boot, low-cost hardware, MCP support, vision pipeline, WebUI launcher, Android/Termux, and multi-architecture deployment.
- README warns the project is early rapid development, not production-ready before v1.0, and recent builds may use 10–20MB RAM.

Hermes target:
- Existing module: `agent/reliability_harness.py`.
- Existing tests: `tests/agent/test_reliability_harness.py`.
- Existing lifecycle manifest from the Google agents-cli absorption slice.

## Candidate paths

A. Install or integrate PicoClaw into Hermes.
- Rejected: early-stage external runtime, device/gateway/MCP risk, not necessary for Hermes core strengthening.

B. Land a local/default-off edge-runtime profile contract in the offline harness.
- Selected: captures the transferable architecture with low blast radius and deterministic readback.

C. Docs-only analysis.
- Rejected: user asked to continue strengthening; safe executable slice exists.

## Landed change

Code:
- Added `EdgeRuntimeProfileContract` to `agent/reliability_harness.py`.
- Extended `LifecycleContractRegistry` with `edge_runtime_profiles` and `edge_profile(profile_id)` readback.
- Added `edge_runtime` skill pack.
- Added lifecycle commands:
  - `edge-profile`: emits a local edge-worker profile/readback contract while execution/device gates remain closed.
  - `edge-benchmark`: records local-only footprint/boot/readback measurements for a candidate edge worker.
- Added `edge_runtime_profile_ids` to each eval trajectory's compact lifecycle contract.
- Extended plain `capabilities` output to show edge runtime profiles.

Profile:
- `local_edge_worker`
  - device classes: local Mac, Linux SBC, Android Termux, KVM/camera gateway.
  - resource-budget fields: `memory_rss_mb`, `boot_ms`, `network`.
  - allowed by default: read-only profile readback, local benchmark receipt, fixture command simulation, health/kill-switch dry run.
  - forbidden by default: external dispatch, credential entry, device control, MCP write authority, publish/deploy/push, destructive operation.
  - verifies: `memory_rss_mb`, `boot_ms`, `profile_id`, `closed_side_effect_gates`.

## Verification evidence

RED tests:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_eval_harness_trajectory_includes_edge_runtime_profile_ids -q
# 2 failed: edge_runtime skill/profile and edge_runtime_profile_ids were missing
```

GREEN targeted tests:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py::test_lifecycle_contract_registry_models_skill_packs_and_commands tests/agent/test_reliability_harness.py::test_eval_harness_trajectory_includes_edge_runtime_profile_ids -q
# 2 passed in 0.08s
```

Focused broader regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 58 passed in 0.89s
```

Syntax/diff checks:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md docs/private/picoclaw-edge-runtime-profile-hermes-reliability-20260701.md
# success
```

CLI capabilities readback:

```bash
./venv/bin/python -m agent.reliability_harness capabilities --json
# schema: hermes_agent_lifecycle_contract_v1
# skill_packs include: edge_runtime
# commands include: edge-profile, edge-benchmark
# edge_runtime_profiles: local_edge_worker
# forbidden_by_default: external dispatch, credential entry, device control, MCP write authority, publish/deploy/push, destructive operation
```

CLI run + trajectory readback:

```bash
./venv/bin/python -m agent.reliability_harness run --runs-dir /tmp/hermes-edge-profile-smoke-20260701 --case direct-basic-answer --response-text OK --json
# passed True score 1.0
# trajectory.lifecycle_contract.edge_runtime_profile_ids ['local_edge_worker']
```

## Boundary statement

This landing is local/default-off and readback-only. It does not install PicoClaw, does not clone/run its code inside Hermes, does not enable an edge worker, does not register MCP servers, does not control devices, does not dispatch externally, and does not publish/deploy.
