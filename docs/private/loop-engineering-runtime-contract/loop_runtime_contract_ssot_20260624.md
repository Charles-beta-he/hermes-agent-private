# Loop Engineering → Hermes LoopRuntimeContract SSOT (2026-06-24)

Status: local dry-run contract landed; runtime executor disabled.

## Objective

Turn the Loop Engineering orange-book audit into a Hermes-owned project-level contract boundary. The target is not another report: every future Hermes long-running loop should be expressible as a `LoopRuntimeContract` with explicit moves, parts, evaluator, budget, persistence, scheduler, and human gate.

## Source evidence

- PDF: `/Users/charles/Downloads/Loop-Engineering橙皮书-v260615.pdf`
- Extracted text: `/tmp/loop_engineering_orange_book.txt`
- Local Hermes version observed: `Hermes Agent v0.16.0 (2026.6.5)`
- Local repo: `/Users/charles/.hermes/hermes-agent`
- Contract schema: `schemas/loop_runtime_contract.schema.json`
- Doctor: `scripts/loop_runtime_contract_doctor.py`
- Tests: `tests/scripts/test_loop_runtime_contract_doctor.py`

## Contract boundary

This slice is deliberately fail-closed:

- no scheduler is enabled;
- no worker is spawned;
- no taskpool or mission state is written;
- no external adapter is called;
- no credential is read;
- no external side effect is performed.

The only allowed side effect is writing local evidence receipts under `docs/private/loop-runtime-contract-dry-runs/` when the operator passes `--write`.

## Required Loop moves

Every promoted contract must declare all five moves:

1. `discovery` — what source finds work and what output it produces.
2. `handoff` — how work is isolated and handed to an executor.
3. `verification` — which independent evaluator can veto.
4. `persistence` — where state/evidence lives outside the context window.
5. `scheduling` — how the next iteration is triggered, or why it is disabled.

## Required Loop parts

Every promoted contract must declare all six parts:

1. `automations`
2. `worktrees`
3. `skills`
4. `connectors`
5. `sub_agents`
6. `memory`

## Hard gates

The doctor rejects a contract when:

- the evaluator is not independent from the generator;
- the evaluator cannot veto;
- evaluator evidence refs are missing;
- a write-authority loop does not use an isolated workspace;
- budget limits are missing or non-positive;
- `max_subagents` exceeds the default safe cap;
- human-gate fields are missing;
- scheduler is enabled in this dry-run boundary;
- external side effects are requested.

## Promotion path

This SSOT is the safe first slice. A future runtime promotion requires a separate human-gated authorization packet containing:

- exact executor binding;
- affected repo/profile/taskpool/mission scope;
- budget and retry ceilings;
- rollback and kill switch;
- independent evaluator command;
- proof that the contract doctor passes;
- proof that runtime writes are shadow/staging first.

Until that packet is approved, `LoopRuntimeContract` remains a validator and evidence shape, not an active executor.

## Verification commands

```bash
cd /Users/charles/.hermes/hermes-agent
./venv/bin/python -m pytest -o addopts='' tests/scripts/test_loop_runtime_contract_doctor.py -q
./venv/bin/python -m py_compile scripts/loop_runtime_contract_doctor.py tests/scripts/test_loop_runtime_contract_doctor.py
./venv/bin/python scripts/loop_runtime_contract_doctor.py --json
```
