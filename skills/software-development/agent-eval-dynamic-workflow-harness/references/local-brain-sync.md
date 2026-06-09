# Local-brain sync for Agent Reliability Harness landings

Use this reference when a Hermes Agent self-architecture change creates a new durable structure, not just a one-off code patch.

## When to sync the local brain

Sync when the landing changes any of these class-level surfaces:

- a new evaluation harness or control-plane component;
- workflow router semantics;
- grader / evidence / regression contracts;
- durable skill or self-optimization process;
- a new project-level architecture boundary that future sessions must retrieve by topic.

Do not sync raw session logs, transient tool failures, temporary TODOs, or every individual test run. Keep those in the SSOT/commit evidence only.

## Recommended local-brain landing zones

- **L2 Project note**: durable project structure, component map, paths, current status, boundaries, and verification commands.
- **L6 Review note**: review/adjudication record, evidence boundary, risks retained, and follow-up gates.
- **L4 Skill**: operational procedure remains in the Hermes skill library; local brain may link to it but should not become the executable source of procedure.

## Minimal note contents

L2 project note should include:

- purpose;
- implementation paths;
- component map;
- verification commands and summarized outputs;
- commit IDs if already created;
- explicit boundaries and non-goals;
- links to SSOT and L6 review.

L6 review note should include:

- decision;
- what landed;
- evidence commands/output summaries;
- **Evidence Boundary** section;
- risks retained;
- follow-up integration gates.

## Verification pattern

After writing notes:

```bash
python3 ~/.hermes/scripts/brainctl.py --json reindex
python3 ~/.hermes/scripts/brainctl.py --json validate
python3 ~/.hermes/scripts/brainctl.py --json search "<project/topic>"
```

Interpretation:

- `reindex` should include the new content.
- `search` should find the new L2/L6 notes.
- `validate` may fail due to historical vault issues; filter for the new note paths and ensure they have no new violations.

## Pitfalls

- Do not promote model agreement or social-media trend claims directly into L1/L2 without evidence boundary.
- Do not store task progress or temporary status as durable brain structure.
- Do not copy full SSOT documents into local brain; summarize structure and link back.
- Do not treat local-brain sync as live runtime rollout. It is retrieval/indexing for future sessions.
