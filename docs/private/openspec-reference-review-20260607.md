# OpenSpec reference review for Hermes Control Tower

Date: 2026-06-07
Reference repo: https://github.com/Fission-AI/OpenSpec
Pinned commit: 1b06fddd59d8e592d5b5794a1970b22867e85b1f
Local clone: /Users/charles/.hermes/runs/reference-repos/OpenSpec
Target context: Hermes Control Tower / workflow control plane / proposal-only command registry

## Review boundary

This is a reference-architecture review, not an integration proposal. Do not vendor OpenSpec into Hermes. Extract patterns that improve Hermes' own ledger-first workflow model while preserving the Control Tower Ledger as the single canonical workflow fact source.

## Source-verified OpenSpec claims

1. OpenSpec is a lightweight spec-driven development CLI/package.
   Evidence:
   - README.md:80-99 documents install/init and `/opsx:propose` quick start.
   - package.json:1-3 identifies `@fission-ai/openspec` version 1.4.1.
   - package.json:29-31 exposes the `openspec` CLI.

2. Its central workflow is artifact-guided, not executor-first.
   Evidence:
   - README.md:47-68 shows proposal/specs/design/tasks then apply/archive flow.
   - docs/opsx.md:46-58 frames OPSX as actions rather than rigid phases, with proposal -> specs -> design -> tasks -> implement.
   - docs/concepts.md:26-50 separates canonical specs from proposed changes.

3. It models artifact dependencies as a graph.
   Evidence:
   - src/core/artifact-graph/types.ts:3-31 defines artifacts, `requires`, and optional `apply` tracking.
   - src/core/artifact-graph/schema.ts:23-44 validates YAML schemas, duplicate IDs, invalid references, and cycles.
   - src/core/artifact-graph/graph.ts:69-166 computes deterministic build order, next-ready artifacts, completeness, and blocked artifacts.
   - test/core/artifact-graph/workflow.integration.test.ts:37-92 verifies proposal -> specs/design -> tasks readiness and completion.

4. It keeps the workflow customizable through schema/templates instead of hardcoded prompts.
   Evidence:
   - docs/opsx.md:11-26 says OPSX exists because hardcoded instructions were black-box and hard to customize.
   - docs/opsx.md:72-124 documents project config context/rules injection.
   - schemas/spec-driven/schema.yaml:1-153 defines the default proposal/specs/design/tasks schema and apply tracking.

5. It has a local coordination workspace concept for multi-repo visibility, distinct from repo-local specs.
   Evidence:
   - docs/concepts.md:52-67 defines workspace/context store/initiative/link/change.
   - docs/concepts.md:70-91 distinguishes private local workspace view from repo-local `openspec/` state.
   - docs/concepts.md:143-178 lists workspace setup/list/link/doctor/update/open commands.
   - src/core/workspace/state-io.ts:69-90 finds workspace roots via namespaced `.openspec-workspace` state.

6. It has adapter patterns for multiple AI tools.
   Evidence:
   - docs/supported-tools.md:21-58 lists skill/command path patterns for many tools.
   - src/core/command-generation/types.ts:8-48 separates tool-agnostic command content from per-tool formatting.
   - src/core/command-generation/registry.ts:36-105 registers tool command adapters.
   - src/core/shared/tool-detection.ts:14-45 defines workflow skill and command IDs.

7. Its generated agent instructions include useful safety boundaries but are prompt-driven, not a durable execution control plane.
   Evidence:
   - src/core/templates/workflows/apply-change.ts:28-55 tells the agent to read status/instructions and stop on workspace-planning edit constraints.
   - src/core/templates/workflows/verify-change.ts:48-172 defines verification report heuristics and graceful degradation.
   - src/core/templates/workflows/archive-change.ts:28-87 prompts/warns before archive and moves files to archive.

## Transferable patterns for Hermes

### A. Schema-defined workflow graph

OpenSpec's `schema.yaml` + artifact graph is directly useful as a reference for Hermes Control Tower's future workflow-map layer.

Hermes adaptation:
- Keep Control Tower Ledger as the SSOT.
- Add a `workflow_schema` / `workflow_artifact` model or read-side projection that defines nodes, dependencies, output/evidence requirements, and readiness predicates.
- Compute `next_dispatchable` and `blocked_reason` from a deterministic graph, not scattered hardcoded if/else rules.
- Persist schema version and projection checkpoint in the ledger.

Do not copy:
- Filesystem-existence-as-completion as authoritative state. OpenSpec uses file existence for artifact completion; Hermes should treat files as evidence artifacts and ledger events as authoritative.

### B. Human-editable templates and rules injection

OpenSpec's project config + per-artifact rule injection is useful for Hermes workflow customization.

Hermes adaptation:
- Add workflow/profile-level context and rules, but store their activation/version in the ledger.
- Use them to generate proposed command guidance, dashboard explanations, and worker prompts.
- Keep user/project rules scoped; do not promote them to global memory or global hard gates unless explicitly elevated.

### C. Multi-repo coordination workspace

OpenSpec's workspace/context-store/initiative model is highly relevant to Hermes tasks spanning app/admin/backend/docs.

Hermes adaptation:
- Model a Control Tower workspace as a ledger-backed local view over linked repos, task sources, dashboards, evidence stores, and gateway channels.
- Store local path bindings as private projection/config, not canonical task facts.
- Canonical task/workflow facts remain in Control Tower Ledger.

### D. Tool adapter registry

OpenSpec's tool command adapter registry is a useful pattern for Hermes external surfaces.

Hermes adaptation:
- Define adapter contracts for dashboard/gateway/kanban/taskpool/worker/CLI surfaces.
- A command proposal should be tool-agnostic (`command_type`, target, risk, payload).
- Surface adapters format/display/propose commands; they do not own command truth.

### E. Verification as first-class workflow artifact

OpenSpec's verify workflow gives a compact 3-axis verification heuristic: completeness, correctness, coherence.

Hermes adaptation:
- Map to Control Tower evidence maturity:
  - completeness -> tasks/requirements checked
  - correctness -> tests/proofs pass
  - coherence -> design/ADR/policy alignment
- Store verification reports as evidence rows/events, not as chat-only summaries.

## Low-fit / caution areas

1. OpenSpec is mostly file/artifact and prompt orchestration. Hermes Control Tower needs event-sourced execution governance.
   - OpenSpec does not provide the approval/executor authority model Hermes now needs.
   - It should inform workflow graph/projections, not replace the ledger.

2. OpenSpec archive/sync patterns are human/prompt mediated.
   - Good for planning artifacts.
   - Not sufficient for Hermes lifecycle actions like claim/release/mark done without a policy executor.

3. OpenSpec's current workspace support is explicitly beta/evolving.
   - docs/concepts.md:52-56 warns workspace command behavior/state files/JSON output are evolving.
   - Treat as conceptual reference, not stable dependency.

4. OpenSpec telemetry exists.
   - README.md:184-191 says anonymous command/version telemetry is collected with opt-out.
   - If using the CLI locally for experiments, set `OPENSPEC_TELEMETRY=0` unless explicitly desired.

## Recommendation

OpenSpec is helpful for Hermes, but as a reference pattern, not an integration dependency.

Priority for Hermes Control Tower:

P1: Adopt a schema-defined workflow map/readiness model inspired by OpenSpec's artifact graph.
P1: Add workflow-profile templates/rules injection, but ledger-versioned and scoped.
P2: Add workspace/local-view projection for multi-repo task context.
P2: Convert verification into evidence rows using completeness/correctness/coherence dimensions.
P3: Consider an adapter registry abstraction for external control surfaces after command/approval semantics stabilize.

Do not spend effort now on installing OpenSpec into Hermes or generating OpenSpec slash commands. The highest-value extraction is the artifact graph + schema/profile customization model.

## Suggested next Hermes slice

Phase 3A remains approval lifecycle with no execution, as previously recommended.

OpenSpec-informed follow-up after 3A:

Phase 3B: Ledger-backed workflow schema projection
- Define a small Control Tower workflow schema for the current proposal-only command lifecycle:
  - command_proposed
  - approval_requested
  - approval_decided
  - executor_eligible (future, disabled)
- Add deterministic graph validation:
  - duplicate node IDs rejected
  - missing dependencies rejected
  - cycles rejected
- Add snapshot fields:
  - `workflow_map.nodes`
  - `workflow_map.ready`
  - `workflow_map.blocked`
- Keep execution disabled.

This gives Hermes the most useful part of OpenSpec while preserving the unique Control Tower requirement: one durable canonical ledger.
