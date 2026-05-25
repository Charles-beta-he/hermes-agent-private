# Experience Ratchet proposal review — project-doc landing tranche

Generated: 2026-05-23T11:29:00Z
Task: `2026-05-23-experience-ratchet-proposal-review-project-doc-landing-tranche`
Scope: review `/Users/charles/.hermes/experience-ratchet/proposals/*` and materialize only source-backed private project-doc landing notes. Skill, public-consensus, memory/profile, and runtime enablement remain outside this tranche.

## Decision summary

| Proposal | Landing zone | Decision | Rationale / evidence |
|---|---:|---|---|
| `proposal-agentmemory-82cf8de92265-20260523T082247Z` | project_doc | APPROVE private landing note | `docs/private/agentmemory-gap-analysis.md` lines 23-37 and 41-52 support a local memory server boundary with explicit MCP/REST/plugin contracts plus metadata/audit semantics; upstream plugin claims still require compatibility testing before enablement. |
| `proposal-bytebot-f8e27c2932a5-20260523T082247Z` | project_doc | APPROVE private landing note | `docs/private/bytebot-gap-analysis.md` lines 25-42 and 94-110 show Bytebot is valuable only behind a Hermes-style external-control safety boundary; direct unauthenticated desktop/action adoption is rejected. |
| `proposal-rag-anything-ad6e571324cd-20260523T082247Z` | project_doc | APPROVE private landing note | `docs/private/rag-anything-hermes-gap-analysis.md` lines 21-42 and 46-48 support a governed multimodal ingestion spine that preserves structure/layout and modal decomposition before retrieval. |
| `proposal-karpathy-skills-4bddfedcd6b1-20260523T082247Z` | skill | DEFER | Not a project-doc landing proposal; handled by skill landing review. |
| `proposal-hermes-upstream-ratchet-b1b0b8bc42f5-20260523T082247Z` | skill | DEFER | Not a project-doc landing proposal; handled by skill landing review. |
| `proposal-openclaw-wrong-source-ad275766a45a-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires public-consensus write surface and human review; outside task boundary. |
| `proposal-hermes-upstream-ratchet-3e3fa53b5dd1-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires public-consensus write surface and human review; outside task boundary. |
| `proposal-bytebot-0bb88eef12ce-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires public-consensus write surface and human review; outside task boundary. |
| `proposal-agentmemory-1b5ce49966a9-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires public-consensus write surface and human review; outside task boundary. |

## Accepted private project-doc landings

### 1. Shared agent memory is a boundary, not a direct write surface

- Atom: `agentmemory-82cf8de92265`
- Proposal: `/Users/charles/.hermes/experience-ratchet/proposals/proposal-agentmemory-82cf8de92265-20260523T082247Z.json`
- Source artifacts:
  - `/Users/charles/.hermes/hermes-agent/docs/private/agentmemory-gap-analysis.md` lines 23-37
  - `/Users/charles/.hermes/hermes-agent/docs/private/agentmemory-gap-analysis.md` lines 41-52
  - `rohitg00/agentmemory` commit `bc641077913c0ac043e702a8f6519189e89b1721`
- Accepted claim:
  - Hermes should treat shared memory integration as a separately governed local service boundary reached through explicit MCP/REST/plugin contracts.
  - Any future plugin path must preserve source observation metadata, retention/privacy controls, auditability, and rollback semantics.
- Anti-conditions / falsifiers:
  - Do not enable direct integration if the memory backend cannot enforce retention/privacy/source-observation metadata.
  - Do not treat upstream `integrations/hermes` plugin claims as compatible-by-default with the current private Hermes checkout.
- Project-doc landing implication:
  - Future Hermes multi-brain / memory architecture docs should describe shared memory as an external governed subsystem, not as permissionless global memory writes.

### 2. Desktop/computer-use adoption requires a fail-closed external-control boundary

- Atom: `bytebot-f8e27c2932a5`
- Proposal: `/Users/charles/.hermes/experience-ratchet/proposals/proposal-bytebot-f8e27c2932a5-20260523T082247Z.json`
- Source artifacts:
  - `/Users/charles/.hermes/hermes-agent/docs/private/bytebot-gap-analysis.md` lines 25-42
  - `/Users/charles/.hermes/hermes-agent/docs/private/bytebot-gap-analysis.md` lines 94-110
  - `bytebot-ai/bytebot` commit `3d37894ce07ef8d8b40adc7fd309ad96c2a71313`
- Accepted claim:
  - Hermes may only absorb desktop/computer-use takeover or action APIs behind an explicit external-control gate with scope, idempotency, audit, and approval semantics.
  - Bytebot-like desktops are an optional isolated sandbox lane or browser-harness escalation path, not a replacement for Hermes’ existing host-safe control model.
- Anti-conditions / falsifiers:
  - If action APIs are unauthenticated or privileged by default, the integration remains reference-only.
  - Reject direct exposure of broad file/app/action APIs until permission-tier and audit gates exist.
- Project-doc landing implication:
  - Future external-control and computer-use architecture docs should state that any sandbox desktop runtime is subordinate to Hermes’ fail-closed packet gate and cannot bypass it.

### 3. Multimodal document intelligence must preserve structure before retrieval

- Atom: `rag-anything-ad6e571324cd`
- Proposal: `/Users/charles/.hermes/experience-ratchet/proposals/proposal-rag-anything-ad6e571324cd-20260523T082247Z.json`
- Source artifacts:
  - `/Users/charles/.hermes/hermes-agent/docs/private/rag-anything-hermes-gap-analysis.md` lines 21-42
  - `/Users/charles/.hermes/hermes-agent/docs/private/rag-anything-hermes-gap-analysis.md` lines 46-48
- Accepted claim:
  - Hermes should frame document intelligence as a governed multimodal ingestion spine: parse documents, preserve structure/layout, decompose modalities, then perform retrieval.
  - Retrieval-only text flattening is insufficient for durable document evidence workflows.
- Anti-conditions / falsifiers:
  - If structural extraction cannot cite source spans or artifacts, output remains advisory only.
  - Do not skip modal decomposition and claim semantic retrieval alone is authoritative for mixed-format evidence.
- Project-doc landing implication:
  - Future knowledge-ingestion architecture docs should treat multimodal parsing and structure-preserving evidence capture as a prerequisite layer before indexing/retrieval.

## Explicit non-actions

- No public-consensus atom was promoted.
- No memory/profile/core state was mutated.
- No skill patch was applied from this task.
- No runtime feature was enabled.
- No third-party code was installed or executed.
- No claim was treated as complete solely because a generic selfcheck/proof-check passed.

## Recommended follow-through surfaces

These are documentation-only landing surfaces for future curated edits; this task does not require mutating all of them now:

1. `docs/private/multibrain-architecture.md`
   - Add the shared-memory-server boundary when/if that document next changes.
2. private computer-use / external-control architecture notes
   - Keep Bytebot-derived sandbox-only rules tied to `external_control_gate.py` governance.
3. future private knowledge-ingestion architecture note
   - Anchor multimodal evidence ingestion before retrieval/indexing.

## Closeout evidence expectation

Close this tranche only if read-back confirms this landing review note exists and control-plane validation still reports healthy proposal state (`experience-ratchet-maze validate`, taskpool reconciliation/proof checks as applicable).