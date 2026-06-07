# Hermes memory/local-brain recall proof gate

Date: 2026-06-07
Task: 为 Hermes local-brain/memory 建立可回放的记忆检索质量与漂移 proof gate

## Purpose

The gate in `agent/recall_proof_gate.py` makes memory/local-brain retrieval quality replayable and rejectable. It consumes JSON fixtures instead of live memory state, so future changes to memory, local-brain, or provider recall can be compared against deterministic expected accept/reject outcomes.

## Fixture classes covered

The replay fixture at `tests/fixtures/recall_proof_gate/sample_cases.json` covers these memory/local-brain recall classes:

1. `durable_user_memory`: same-scope durable memory with provenance, confidence, and freshness.
2. `local_brain_task_note`: task-note boundary evidence from the local brain, including rejection of unrelated project task facts.
3. `mempalace_provider_replay`: MemPalace provider replay with a durable artifact anchor.
4. `walrus_memory_provider_replay`: Walrus Memory provider replay missing durable provenance.
5. `session_search_replay`: session recall that is reference-only and cross-scope.
6. `drift_sensitive_stale_fact`: branch/status facts that are drift-sensitive but lack freshness metadata.

## Evidence adopted

- `memory:mem_fresh` was adopted because it is same-scope (`project:hermes-agent`), has `source_path` provenance, has confidence `0.92`, and has fresh `last_verified_at` metadata.
- `local_brain_task:brain_boundary` was adopted because it is a same-scope task-note boundary with a concrete task-note path, confidence `0.88`, and current freshness metadata. It is used only as boundary evidence, not as implementation proof.
- `mempalace:mempalace_trace` was adopted because it includes a MemPalace artifact path, same-scope project metadata, confidence `0.87`, and fresh verification metadata.

## Evidence rejected

- `local_brain_task:brain_unrelated_project` was rejected because its scope is `project:uvisa-app-2.0`, not `project:hermes-agent`.
- `walrus_memory:walrus_orphan` was rejected because it has no durable provenance anchor (`source_path`, `session_id`/`message_id`, or `artifact_path`).
- `session_search:session_reference_only` was rejected because it is both cross-scope and marked `reference_only`, so it cannot be used as decision-grade proof.
- `memory:branch_fact_without_ttl` was rejected because branch/PR/status text is drift-sensitive and lacks `last_verified_at` freshness metadata.

## Non-goals and red-line preservation

- The gate does not weaken taskpool proof, permission, mission-envelope, lifecycle, or closeout gates.
- The gate does not treat synthesized envelopes, model agreement, or provider summaries as implementation proof.
- The gate does not fetch or mutate live memory; it only evaluates replay fixture packets.

## Verification commands

Focused tests:

```bash
cd /Users/charles/.hermes/hermes-agent
./venv/bin/python -m pytest tests/agent/test_recall_proof_gate.py
```

Equivalent JSON gate command:

```bash
cd /Users/charles/.hermes/hermes-agent
./venv/bin/python -m agent.recall_proof_gate tests/fixtures/recall_proof_gate/sample_cases.json --now 2026-06-07T00:00:00Z --output /tmp/agent-team-2026-06-07---hermes-loca-6c6ac974dffa/recall_proof_gate_report.json
```

The JSON report exposes `gate_active: true`, fixture class count, source-kind admit/reject counts, per-candidate failures, and final `summary.verdict`.
