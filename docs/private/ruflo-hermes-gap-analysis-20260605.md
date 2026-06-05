# Ruflo → Hermes local-brain / agent-team gap analysis (2026-06-05)

## SSOT / provenance

- Task: `2026-06-05-task---评估-ruflo-多代理-ai-harness-对-hermes-local-brain-agent-team-的可吸收模式`
- Source repo: <https://github.com/ruvnet/ruflo>
- Local checkout: `/Users/charles/.hermes/runs/ruflo-reference-review-20260605`
- Commit reviewed: `d065b15927c6ba7318623e8af123e7980e4c6681`
- Scope: source-grounded review only. No third-party Ruflo hooks/plugins were installed or executed.

## Source review surface

Read/inspected these repository surfaces as the evidence boundary:

- `README.md`: product claim and install surface. It describes Ruflo as a “Multi-agent AI harness for Claude Code and Codex” and states it adds swarms, self-learning memory, federated comms, and security to Claude Code (`README.md:23-39`). It also distinguishes plugin-lite vs full CLI install and warns full install writes `.claude/`, `.claude-flow/`, helpers, settings, MCP server, hooks, and daemon (`README.md:56-64`).
- `AGENTS.md`: contributor/executor contract. It explicitly treats claude-flow as a ledger/orchestrator and Codex as the executor, and warns not to stop after orchestration calls (`AGENTS.md:10-25`, `AGENTS.md:29-66`).
- `CLAUDE.md`: full runtime policy. It claims v3.6 stable, 314 MCP tools, 16 agent roles, 19 AgentDB controllers, 21 plugins (`CLAUDE.md:1-5`), and defines event-sourcing, input validation, DDD, and dual Claude+Codex collaboration (`CLAUDE.md:28-45`, `CLAUDE.md:106-144`).
- `package.json`: actual package surface. The root package is `claude-flow` `3.10.37`, ships CLI and v3 package artifacts, depends on `@claude-flow/*`, `@ruvector/*`, `agentdb`, and has security overrides (`package.json:1-75`, `package.json:77-107`).
- `v3/README.md`: architecture claims: 15-agent hierarchical mesh, plugin architecture, MCP-first API, event sourcing, hybrid SQLite+AgentDB memory, and security modules (`v3/README.md:20-64`).
- `plugins/ruflo-swarm/README.md`: bounded swarm contract: agent teams, monitor streams, worktree isolation, consensus strategies, anti-drift defaults, 12-tool MCP surface, and smoke test contract (`plugins/ruflo-swarm/README.md:12-37`, `:51-76`).
- `plugins/ruflo-agentdb/README.md`: memory substrate contract: controller bridge, embeddings, HNSW pattern router, causal graph, namespace convention, hook integration, and smoke contract (`plugins/ruflo-agentdb/README.md:1-24`, `:71-140`).
- `plugins/ruflo-federation/README.md`: cross-node comms contract: mTLS/ed25519 identity, PII policy, trust tiers, signed envelopes, Byzantine consensus, and hop/token/USD circuit breakers (`plugins/ruflo-federation/README.md:12-43`).
- `.claude-plugin/docs/STRUCTURE.md`: plugin packaging surface: official Claude Code plugin format, commands/agents/hooks layout, MCP server declarations, install/verify scripts (`.claude-plugin/docs/STRUCTURE.md:1-21`, `:63-108`).
- `v3/mcp/tool-registry.ts`: real code for the tool registry: Map-based O(1) lookup, validation, category/tag indexes, execution metrics (`v3/mcp/tool-registry.ts:1-16`, `:59-137`).

## Hermes primitive mapping

| Ruflo pattern | Hermes analogue | Assessment |
|---|---|---|
| Ledger/orchestrator vs executor separation | `brain_task_orchestrator.py` + `agent_team_controller.py` workers | Strongly absorbable. Hermes already has the right SSOT shape; Ruflo reinforces that orchestration records must never be mistaken for implementation proof. |
| Swarm anti-drift defaults (hierarchical, specialized, small team, leader/consensus) | taskpool `parallel_boundary`, `agent-team-parallel`, proof-gated worker handoff | Absorb as policy/checklist, not as a new task pool. The useful slice is improving worker prompts/artifacts to show coordinator/worker role separation and boundary-safe N. |
| Worktree isolation per agent | Hermes worktree-isolated parallel workers | Already aligned. Potential improvement: expose isolation path and conflict boundary more visibly in handoff artifacts and worker job status. |
| AgentDB namespace convention and hook-fed memory | Hermes brain overlays, memories, evidence_refs, `SessionDB`/FTS | Absorb selectively: namespace taxonomy and reserved namespace guardrails are useful; do not import AgentDB/ruvector runtime before a separate benchmark. |
| Plugin marketplace package shape (commands/agents/hooks + verify scripts) | Hermes skills/plugins and gateway hooks | Useful as a packaging reference for future external skill/brain overlays. Hermes should keep plugin manifests simple and evidence-gated, not auto-install third-party hooks. |
| Federation trust / signed envelopes / PII gates / hop+cost caps | edge-worker + envelope bus + provider budget gates | High-value long-term pattern. Absorb as a design task for remote/edge workers: signed envelopes, trust tiers, and delegation budget caps before any distributed execution. |
| MCP tool registry with validation/indexes/metrics | Hermes `tools/registry.py`, `model_tools.py`, toolsets | Useful low-risk slice: add registry diagnostics/metrics or validation reporting, but only after checking existing registry behavior and tests. |

## Gap / risk analysis

1. **Do not create a competing pool.** Ruflo's swarm/task/memory primitives are attractive but Hermes SSOT must remain `brain_task_orchestrator.py` + `kanban.db`; any absorbed worker must fetch/report through that pool only.
2. **Do not equate orchestration with delivery.** Ruflo's own AGENTS file says claude-flow tracks while Codex executes. Hermes should preserve the same distinction in taskpool proof gates: multimodel PASS/advisory, hashes, selfcheck, and py_compile are not feature-specific implementation proof.
3. **Hook surface is high blast radius.** Ruflo full install writes hooks, settings, daemons, MCP config, and helper scripts. Hermes must not auto-install/execute these; use static source review and governed adapter tasks only.
4. **Performance claims need independent proof.** Ruflo README/v3 docs include large performance claims, but v3/CLAUDE.md itself marks some figures measured and others target/unverified. Hermes should benchmark any vector/memory import locally before treating claims as architectural truth.
5. **Federation is promising but dangerous.** mTLS/ed25519/PII/trust tiers/caps map well to Hermes edge-worker governance, but require a separate design/prototype with fail-closed tests before runtime integration.

## Recommended staged absorption plan

### Stage 0 — landed by this tranche

- Durable SSOT/reference-review artifact: this document.
- Source checkout and commit recorded.
- No Ruflo code executed; no third-party plugin installed.

### Stage 1 — lowest-risk Hermes improvement

Create a follow-up implementation task for **agent-team handoff role-boundary evidence**:

- Update `agent_team_controller.py`/handoff artifact generation so each worker packet explicitly states:
  - Hermes taskpool is the only SSOT;
  - controller/orchestrator records are not proof of implementation;
  - worker must produce feature-specific evidence before review/done;
  - parallel boundary + worktree/isolation path + stop conditions.
- Verification: focused test asserting generated handoff contains those red lines and does not weaken lifecycle gates.

### Stage 2 — memory namespace governance

Create a separate design task to map Ruflo's namespace convention to Hermes brain overlays/memory providers:

- reserved namespaces / profiles;
- provenance-required memory writes;
- cleanup/GC owner per namespace;
- read-back tests for memory search and brain overlay route diagnostics.

### Stage 3 — edge/federation security gate

Create a design/prototype task for edge-worker signed envelopes and delegation caps:

- signed worker result envelope;
- hop/token/USD budget fields;
- trust tier and PII policy fields;
- fail-closed tests for missing signature, exceeded hop budget, and unsafe content.

## Minimal implementation slice to enqueue next

Title: `Add agent-team handoff role-boundary evidence contract inspired by Ruflo ledger/executor split`

Acceptance evidence:

- code/test changes in Hermes only;
- generated handoff artifact read-back containing SSOT, controller-not-proof, feature-specific evidence, boundary/worktree, stop-condition sections;
- focused pytest for handoff content;
- `selfcheck --json` and `proof-check --json` remain PASS/WARN-only for unrelated historical debt.

## Verification run for this tranche

- `git clone --depth 1 https://github.com/ruvnet/ruflo /Users/charles/.hermes/runs/ruflo-reference-review-20260605` → PASS, commit `d065b15927c6ba7318623e8af123e7980e4c6681`.
- Read-back targets present: README, AGENTS, CLAUDE, package, v3 README, swarm/agentdb/federation/plugin/tool-registry files.
- Artifact path: `docs/private/ruflo-hermes-gap-analysis-20260605.md`.
