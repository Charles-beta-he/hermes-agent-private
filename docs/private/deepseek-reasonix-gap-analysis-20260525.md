# DeepSeek-Reasonix → Hermes self-strengthening gap analysis (2026-05-25)

## Live source evidence

- Source: https://github.com/esengine/DeepSeek-Reasonix
- Repo: `esengine/DeepSeek-Reasonix`
- Default branch: `main`
- Language: TypeScript
- Description: DeepSeek-native AI coding agent for terminal; engineered around prefix-cache stability.
- Observed repo stats at intake: 7700 stars, 418 forks, updated 2026-05-25T10:41:36Z.
- Topics: agent, ai-coding, cli, coding-agent, deepseek, ink, llm, prompt-caching, r1, terminal, tool-use, tui, typescript.
- Top-level surfaces inspected: README.md, REASONIX.md, package.json, docs/ARCHITECTURE.md, top-level tree.
- Notable directories: `src/cli`, `src/tools`, `src/mcp`, `src/repair`, `src/index`, `src/code`, `src/core`, `src/ports`, `src/adapters`, `src/frame`, `src/memory`, `src/transcript`, `src/telemetry`, `src/server`, `tests`, `benchmarks`, `dashboard`, `docs`.

## Architecture signals worth evaluating

1. Cache-first loop
   - Immutable prefix + append-only log + volatile scratch partition.
   - Prefix hash/pinning and append-order serialization are treated as explicit invariants.
   - Tool results remain ordered even when read-only calls run in parallel.

2. DeepSeek-specific tool-call repair
   - Schema flattening for deep/wide tool schemas.
   - Reasoning-content tool-call scavenging.
   - Truncated JSON repair.
   - Duplicate call-storm suppression.

3. Cost and routing discipline
   - Flash-first default with explicit pro escalation marker.
   - Auxiliary calls pinned to cheaper tiers.
   - Turn-end compaction for oversized tool results.
   - Visible cost/cache metrics in terminal UI.

4. Event-log / replayable architecture
   - `src/core` exposes Event union, pure reducers, and eventization.
   - `src/transcript` handles write/diff/replay.
   - `src/ports` separates ModelClient, ToolHost, EventSink, MemoryStore, HookRunner, CheckpointStore.

5. Local agent runtime surface
   - CLI/TUI via Commander + Ink.
   - MCP stdio/SSE transports.
   - Background job registry, semantic index, memory stores, telemetry, dashboard server.

## Hermes gap mapping

- Prompt/cache economy: compare Reasonix's immutable-prefix/append-log/scratch partition against Hermes current run loop, skill injection, tool schema loading, and memory/context injection. Target is lower prompt churn without weakening instruction hierarchy or profile-specific context.
- Tool-call robustness: evaluate whether Hermes has equivalent flatten/scavenge/truncation/storm guards for providers with weak tool-call fidelity; if not, design provider-gated repair only where safe.
- Cost observability: map Reasonix per-turn/session cost and cache-hit cells to Hermes CLI/TUI/log telemetry without exposing secrets or creating noisy persistent memories.
- Replay/event-sourcing: compare Reasonix event reducers/transcript replay to Hermes session DB/taskpool evidence needs; extract a small evidence replay or deterministic reducer slice only if it strengthens false-completion prevention.
- MCP/tool parallelism: compare Reasonix `parallelSafe` contract to Hermes tool metadata and taskpool/browser evidence gates; avoid broad parallelization for mutating tools.

## Smallest verifiable integration slice

1. Produce a design note comparing Hermes current context/tool-call loop to Reasonix's cache-first and repair-loop invariants.
2. Choose one low-risk prototype target, preferably either:
   - provider-gated tool-call storm/truncation detector with tests, or
   - prompt-churn/cache-observability telemetry that is read-only and opt-in.
3. Add regression tests proving no mutation-tool parallelization, no secret/log leakage, and no instruction-priority weakening.
4. Run Hermes targeted tests plus taskpool proof/selfcheck before any lifecycle closeout.

## Red lines / anti-drift constraints

- Do not install or run Reasonix as an external agent during intake.
- Do not copy DeepSeek-only assumptions into all Hermes providers.
- Do not store raw transcripts, secrets, API keys, cookies, or private user content while evaluating replay/memory patterns.
- Do not weaken Hermes tool side-effect barriers or permission gates for parallel dispatch.
- Do not treat repo popularity or multimodel agreement as proof of fit; require local Hermes tests and evidence artifacts.

## Taskpool follow-up shape

Add one P2 evolution task for Hermes to evaluate and selectively absorb DeepSeek-Reasonix patterns into the local-brain/runtime architecture, with multimodel review only before high-risk control-plane or profile/memory/skill changes.
