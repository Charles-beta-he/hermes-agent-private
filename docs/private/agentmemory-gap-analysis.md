# AgentMemory source-backed gap analysis for Hermes

Source: https://github.com/rohitg00/agentmemory
Checked: 2026-05-21
Local clone: `/tmp/agentmemory`
Commit checked: `bc641077913c0ac043e702a8f6519189e89b1721`
GitHub API canonical: `rohitg00/agentmemory`
License: Apache-2.0
Package: `@agentmemory/agentmemory` v0.9.21
Runtime baseline: Node >=20, TypeScript ESM, iii-sdk/iii-engine backed state.

## Repository shape

AgentMemory is a persistent memory server for coding agents. It exposes REST/MCP surfaces and agent-specific hook/plugin integrations. The repo includes:

- Core memory engine: `src/functions/*`, `src/state/*`, `src/mcp/*`, `src/hooks/*`.
- Agent integrations: `integrations/hermes`, `integrations/openclaw`, `plugin/.claude-plugin`, `.codex-plugin`, OpenCode hooks.
- Bench/eval harnesses: `benchmark/*`, `eval/*`, `docs/benchmarks/*`.
- Tests: broad vitest suite under `test/`, including privacy, retention, hybrid search, vector index, MCP, hooks, replay, export/import.

## Verified core mechanics

### 1. Shared memory server boundary

README and `integrations/hermes/README.md` describe AgentMemory as one local/self-hosted memory server shared across Claude Code, Codex, Cursor, Gemini CLI, Hermes, OpenClaw, OpenCode and generic MCP/REST clients.

Hermes integration has two advertised modes:

- MCP-only mode:
  - `mcp_servers.agentmemory.command: npx`
  - `args: ["-y", "@agentmemory/mcp"]`
  - separate memory server on `localhost:3111`.
- Deeper plugin mode:
  - copy `integrations/hermes` to `~/.hermes/plugins/agentmemory`.
  - hooks: `prefetch`, `sync_turn`, `on_session_end`, `on_pre_compress`, `on_memory_write`, `system_prompt_block`.

Caveat: this is upstream's plugin claim; actual compatibility with the current private Hermes checkout must be tested before enabling.

### 2. Memory write semantics

`src/functions/remember.ts` registers `mem::remember` with typed boundaries:

- content required.
- optional `type`, `concepts`, `files`, `ttlDays`, `sourceObservationIds`.
- valid memory types: `pattern`, `preference`, `architecture`, `bug`, `workflow`, `fact`.
- near-duplicate detection via Jaccard similarity > 0.7 supersedes the prior latest memory.
- writes audit/state through iii-backed KV.
- updates BM25 index immediately after save.
- writes vector index through guarded embedding path.

This maps well to Hermes' durable memory rule: declarative stable facts belong in memory; procedures should become skills. AgentMemory's `type` and `ttlDays` could support that separation if wrapped by Hermes policy.

### 3. Retrieval stack

`src/functions/search.ts` shows a combined retrieval path:

- in-memory BM25-like search index.
- optional vector index and embedding provider.
- guarded vector writes: skip on provider failure or dimension mismatch instead of breaking memory save.
- batch embedding rebuild path with configurable batch size.
- access tracking hooks for retrieved memories.

Hermes implication: useful pattern is not "replace session_search", but introduce an optional layered retrieval service with graceful fallback and explicit observability for embedding/index health.

### 4. Governance/privacy surfaces

The repo contains dedicated tests/functions for privacy, audit, retention, auto-forget, remember/forget audit, export/import, replay-sensitive, and viewer security. This is valuable as a checklist for Hermes memory hardening.

Important caution: AgentMemory's README claims benchmark/retrieval numbers. Treat these as upstream claims until reproduced locally with the included eval harness.

## Hermes gap matrix

### Directly absorb as design patterns

1. Shared memory server boundary:
   - one local memory service used by multiple agents via MCP/REST.
   - useful for Hermes + Claude Code + Codex shared project memory.

2. Hook-driven capture lifecycle:
   - prefetch before LLM call.
   - sync turn after conversation/tool events.
   - pre-compress reinjection.
   - session-end consolidation.
   - memory-write mirroring.

3. Layered retrieval:
   - BM25/FTS baseline remains mandatory.
   - vectors/graph are optional accelerators.
   - degraded state must be explicit, not silent.

4. Memory versioning/supersession:
   - latest pointer + parent/supersedes chain avoids stale duplicate ghosts.
   - aligns with Hermes memory ghost-prevention needs.

5. Retention/forget/audit model:
   - every delete/forget should leave auditable evidence without retaining sensitive content.

### Do not directly import without proof

1. Do not replace Hermes' existing MEMORY.md / USER.md rules with AgentMemory defaults.
2. Do not trust README benchmark numbers as acceptance evidence.
3. Do not enable upstream Hermes plugin blindly; verify hook API compatibility against current private Hermes.
4. Do not add another opaque persistent database without storage governance, TTL and backup policy.
5. Do not let cross-agent shared memory bypass Hermes' current memory-save policy.

## Recommended Hermes execution order

1. Compatibility spike, read-only:
   - run AgentMemory server locally.
   - register as MCP in a temporary Hermes config/profile.
   - verify health, list tools, perform recall/write in isolated data dir.

2. Policy wrapper:
   - enforce Hermes memory categories before calling AgentMemory.
   - stable facts -> memory.
   - procedures -> skills.
   - WIP/session state -> searchable session log only.
   - sensitive or short-lived facts -> TTL or reject.

3. Retrieval bridge:
   - add optional context source that queries AgentMemory, but keeps existing memory/session_search as baseline.
   - output should include source IDs and confidence/degraded-mode labels.

4. Consolidation job:
   - periodically propose memory/skill candidates from sessions.
   - require deterministic ghost-prevention checks before durable write.

5. Governance/selfcheck:
   - add health checks for server reachability, vector index availability, retention policy, data dir size, and auth/HTTPS settings.

## Immediate next action

Treat AgentMemory as a high-signal reference architecture for Hermes local-brain memory evolution, not a drop-in dependency. The next concrete task should be a temporary-profile compatibility spike with no default config mutation.
