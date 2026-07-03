# Hermes self-evolution proposal doctor

Status: default-off local capability, proposal-only.

## Purpose

This slice gives Hermes a safe first self-evolution boundary for Raven/EverOS-style upstream evidence. It does not enable Raven/EverOS as Hermes runtime. It projects local benchmark evidence into a Hermes-governed proposal packet and optional local receipt.

## Command

```bash
hermes doctor --self-evolution --json
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

## Current candidate pack

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-candidate-pack.json`

The candidate pack includes an `experience_atom` in `promoted_candidate` status and a dry-run skill diff preview with `write_enabled=false`.

## Current patch packet and fixture receipt

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-patch-packet.json`

`/Users/charles/.hermes/workflows/projects/hermes-agent-os-control-plane/04-evidence/self-evolution-fixture-apply-receipt.json`

The patch packet is `mode=fixture_only`; real target writes remain disabled. The fixture apply receipt proves the generated patch applies to a temporary copy of the Hermes skill while `real_target_touched=false`.

## Verification

```bash
./venv/bin/python -m pytest tests/hermes_cli/test_self_evolution_doctor.py -q
./venv/bin/python -m py_compile hermes_cli/self_evolution_doctor.py hermes_cli/doctor.py hermes_cli/subcommands/doctor.py
hermes doctor --self-evolution --self-evolution-evidence /Users/charles/wechat_video_analysis/project_absorption/raven-everos-absorption-ssot/evidence/upstream-self-evolution-benchmark.json --json
```
