# Google agents-cli pattern → Hermes reliability harness landing — 2026-07-01

schema: hermes_self_optimization_landing_v1
id: google-agents-cli-pattern-hermes-reliability-20260701
status: implemented_local_default_off
source_media: `/Users/charles/.hermes/docs/private/douyin-google-agents-cli-7657413716816094578/`
source_repo: `https://github.com/google/agents-cli`

## Objective & cost

Success criteria:
- Absorb the verified `agents-cli` pattern as a Hermes-owned local/default-off contract, not as a Google Cloud dependency.
- Add a stable skill-pack + lifecycle-command manifest to the offline Agent Reliability Harness.
- Keep live gateway, taskpool, memory, cron, external dispatch, publish/deploy, and destructive gates closed.
- Verify with focused tests and CLI readback.

Cost function:
- Maximize reusable reliability structure and future eval/agent routing value.
- Minimize prompt bloat, core-tool footprint, cloud/provider coupling, and side-effect blast radius.

## Situation model

Evidence from media/repo:
- The video frames and GitHub README both classify `google/agents-cli` as a CLI + skills layer for existing coding agents, not a new coding agent.
- The useful pattern is `Skill Packs + Commands + Eval/Deploy/Observe lifecycle`.
- Google Cloud deploy/publish remains external/provider-specific and should not become a Hermes default behavior.

Hermes target:
- Existing module: `agent/reliability_harness.py`.
- Existing tests: `tests/agent/test_reliability_harness.py`, `tests/agent/test_reliability_harness_distribution.py`.
- Existing skill: `agent-eval-dynamic-workflow-harness`.

## Candidate paths

A. Install or wire Google `agents-cli` into Hermes.
- Rejected: provider-specific, external/cloud side effects, not needed for Hermes core reliability.

B. Add local/default-off lifecycle contract to the offline harness.
- Selected: preserves the transferable pattern with low blast radius and testable readback.

C. Docs-only analysis.
- Rejected: user asked to strengthen Hermes and proceed; safe executable slice exists.

## Landed change

Code:
- Added `SkillPackContract`, `LifecycleCommandContract`, and `LifecycleContractRegistry` to `agent/reliability_harness.py`.
- Added `python -m agent.reliability_harness capabilities --json` CLI readback.
- Added compact lifecycle contract data into each eval trajectory under `lifecycle_contract`.
- Made `CaseRegistry.list_cases()` ignore adjacent non-`EvalCase` JSON benchmark fixtures instead of failing the list CLI.

Lifecycle skill packs:
- `workflow`
- `implementation`
- `evaluation`
- `delivery`
- `observability`
- `governance`
- `experience_ratchet`

Lifecycle commands:
- `intake`
- `scaffold`
- `implement`
- `eval-generate`
- `eval-grade`
- `deploy-proposal`
- `observe`
- `ratchet`

Skill/docs:
- Updated bundled repo skill `skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md`.
- Updated active profile skill `agent-eval-dynamic-workflow-harness` with the new `capabilities` CLI smoke.

## Verification evidence

Focused regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py -q
# 14 passed in 0.27s
```

Broader related harness/trajectory regression:

```bash
./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
# 57 passed in 0.88s
```

CLI contract readback:

```bash
./venv/bin/python -m agent.reliability_harness capabilities --json
# schema: hermes_agent_lifecycle_contract_v1
# skill_packs: workflow, implementation, evaluation, delivery, observability, governance, experience_ratchet
# commands: intake, scaffold, implement, eval-generate, eval-grade, deploy-proposal, observe, ratchet
```

CLI run + trajectory readback:

```bash
./venv/bin/python -m agent.reliability_harness run --runs-dir /tmp/hermes-eval-cli-smoke-20260701 --case direct-basic-answer --response-text OK --json
# passed True score 1.0
# trajectory.lifecycle_contract.schema hermes_agent_lifecycle_contract_v1
```

Syntax/checks:

```bash
./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py
# success

git diff --check -- agent/reliability_harness.py tests/agent/test_reliability_harness.py skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md docs/private/google-agents-cli-pattern-hermes-reliability-20260701.md
# success
```

## Boundary statement

This landing is local/default-off and readback-only. It does not install Google `agents-cli`, does not call Google Cloud, does not mutate live gateway/taskpool/memory/cron, and does not authorize external deploy/publish. The next safe slice is to run the broader related regression and, if clean, commit only the scoped harness/skill/docs changes while leaving pre-existing unrelated dirty files untouched.
