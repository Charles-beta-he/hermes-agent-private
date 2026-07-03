# Hermes self-evolution proposal doctor

Status: default-off local capability, proposal-only.

## Purpose

This slice gives Hermes a safe first self-evolution boundary for Raven/EverOS-style upstream evidence. It does not enable Raven/EverOS as Hermes runtime. It projects local benchmark evidence into a Hermes-governed proposal packet and optional local receipt.

## Command

```bash
hermes doctor --self-evolution \
  --self-evolution-stage-manifest-output <stage-manifest.json> \
  --json
hermes doctor --self-evolution \
  --self-evolution-evidence <upstream-self-evolution-benchmark.json> \
  --self-evolution-output <proposal-receipt.json> \
  --json
hermes doctor --self-evolution \
  --self-evolution-evidence <upstream-self-evolution-benchmark.json> \
  --self-evolution-candidate-output <candidate-pack.json> \
  --json
hermes doctor --self-evolution \
  --self-evolution-candidate-input <candidate-pack.json> \
  --self-evolution-patch-packet-output <patch-packet.json> \
  --self-evolution-fixture-skill-dir <temporary-skill-fixture-dir> \
  --json
hermes doctor --self-evolution \
  --self-evolution-patch-packet-input <patch-packet.json> \
  --self-evolution-target-skill-dir <exact-target-skill-dir> \
  --self-evolution-fixture-receipt <fixture-apply-receipt.json> \
  --self-evolution-promotion-gate-output <promotion-gate.json> \
  --json
hermes doctor --self-evolution \
  --self-evolution-patch-packet-input <patch-packet.json> \
  --self-evolution-promotion-gate-input <promotion-gate.json> \
  --self-evolution-target-skill-dir <exact-target-skill-dir> \
  --self-evolution-real-apply-receipt-output <apply-receipt.json> \
  --execute-real-skill-write \
  --json
```

## Safety contract

The report and receipt explicitly keep these closed:

- runtime_mutation=false
- external_dispatch=false
- daemon_started=false
- cron_created=false
- credential_read=false
- direct_memory_write=false
- direct_skill_write=false
- auto_promote=false

Promotion to memory or skills requires a separate exact gate.

## Proven capability level from current Raven/EverOS evidence

Current local evidence supports:

- real user memory roundtrip: proven
- agent case pipeline: proven
- agent skill SKILL.md: not proven

Therefore the current capability level is:

`memory_roundtrip_and_agent_case_proven`

## Current receipt

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-proposal-receipt.json`

## Stage manifest / capability registry

`hermes_cli.self_evolution.manifest` exposes a read-only stage manifest for downstream dashboard/taskpool/experience-ratchet projections. It lists the six current stages:

1. `proposal`
2. `candidate_pack`
3. `patch_packet`
4. `fixture_apply`
5. `promotion_gate`
6. `real_skill_apply`

The manifest keeps the same closed boundaries as the doctor report (`direct_skill_write=false`, `direct_memory_write=false`, `external_dispatch=false`, `runtime_mutation=false`) and marks `real_skill_apply.default_enabled=false` so downstream consumers cannot treat the existence of the harness as permission to write.

Smoke command:

```bash
hermes doctor --self-evolution --self-evolution-stage-manifest-output /tmp/hermes-self-evolution-stage-manifest.json --json
```

## Current candidate pack

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-candidate-pack.json`

The candidate pack includes an `experience_atom` in `promoted_candidate` status and a dry-run skill diff preview with `write_enabled=false`.

## Current patch packet and fixture receipt

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-patch-packet.json`

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-fixture-apply-receipt.json`

The patch packet is `mode=fixture_only`; real target writes remain disabled. The fixture apply receipt proves the generated patch applies to a temporary copy of the Hermes skill while `real_target_touched=false`.

## Current promotion gate and temp real-apply receipts

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-promotion-gate-cli.json`

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-real-apply-blocked-receipt.json`

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-real-apply-cli-temp-receipt.json`

The promotion gate binds exact patch packet, target skill path, target hash, fixture receipt, rollback backup, and post-action readback. The blocked receipt proves missing `--execute-real-skill-write` keeps `real_target_touched=false`. The temp apply receipt proves the same real-apply harness can create a backup and append to a temporary exact target when the gate and execute flag are both present.

## Modular landing

The implementation is modularized under `hermes_cli/self_evolution/`:

- `proposal.py` — evidence projection, proposal receipt, human-readable doctor output.
- `candidate.py` — experience-ratchet candidate pack and dry-run skill diff.
- `patch_packet.py` — fixture-only patch packet verification and fixture apply.
- `promotion_gate.py` — exact promotion gate and default-off real-skill apply harness.
- `common.py` — shared constants, hashes, and closed execution-boundary helper.
- `manifest.py` — read-only stage manifest/capability registry for downstream projections.
- `__init__.py` — reusable public API for future safe local harnesses.

`hermes_cli/self_evolution_doctor.py` remains as a compatibility wrapper that re-exports the modular API so older tests, scripts, and `doctor.py` imports keep working.

## Verification

```bash
./venv/bin/python -m pytest tests/hermes_cli/test_self_evolution_doctor.py -q
./venv/bin/python -m pytest tests/hermes_cli/test_self_evolution_doctor.py tests/hermes_cli/test_external_data_doctor.py -q
./venv/bin/python -m py_compile hermes_cli/self_evolution/*.py hermes_cli/self_evolution_doctor.py hermes_cli/doctor.py
./venv/bin/hermes doctor --self-evolution --json
```
