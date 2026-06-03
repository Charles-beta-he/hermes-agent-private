# Headroom → Hermes Token/Context Control Plane 参考评估

- Source repo: https://github.com/chopratejas/headroom
- Canonical name: `chopratejas/headroom`
- Local read-only evidence dir: `/Users/charles/.hermes/runs/headroom-reference-review-20260603`
- Pinned commit: `378d77e79d0020ca7fba3de8df7aaf910056ad2a`
- Inspection date: 2026-06-03
- License: Apache-2.0
- Primary language: Python; also Rust crates and TypeScript SDK/plugin surfaces
- Scope boundary: reference-architecture intake only. No install, no proxy startup, no SDK execution, no traffic replay, no external credentials, no modification of Hermes runtime paths in this step.

## Live source evidence

GitHub API metadata captured:

- Description: “Compress tool outputs, logs, files, and RAG chunks before they reach the LLM. 60-95% fewer tokens, same answers. Library, proxy, MCP server.”
- Stars: 7847
- Forks: 525
- Default branch: `main`
- Topics include: `agent`, `context-engineering`, `context-window`, `mcp`, `proxy`, `rag`, `token-optimization`, `typescript`.

Files read during intake:

- `README.md`
- `llms.txt`
- `pyproject.toml`
- `docs/spec/002-architecture.md`
- `docs/spec/008-capabilities.md`
- `docs/spec/015-interfaces.md`
- `docs/spec/016-observability.md`
- `docs/spec/018-policies.md`
- `docs/spec/020-security.md`

Tree/source surfaces inspected:

- `headroom/` — 349 files, core Python package.
- `headroom/transforms/` — SmartCrusher, CodeCompressor, log/diff/search/html compressors, content router, cache aligner, read lifecycle.
- `headroom/proxy/` — local LLM proxy, memory injection/query/ranker, semantic cache, rate limiter, metrics, WebSocket/session registry, provider routes.
- `headroom/ccr/` — Compress-Cache-Retrieve MCP server, context tracker, tool injection, batch store.
- `headroom/cli/` — proxy, wrap, mcp, learn, memory, evals, install commands.
- `headroom/providers/` — provider registry and runtime slices for Anthropic/OpenAI-compatible/Gemini/Codex/Copilot/Cursor/OpenClaw/Aider.
- `headroom/learn/` — scanners/analyzer/writer for failure learning.
- `sdk/typescript/`, `crates/`, `plugins/`, `tests/`, `e2e/`, `benchmarks/`.

## Core architecture signals

Headroom is not just a compressor. It is a context-control layer with multiple integration surfaces:

1. **Content routing before LLM**
   - Tool outputs, logs, files, diffs, RAG chunks, JSON, code, text are routed to content-specific compressors.
   - `ContentRouter` + `SmartCrusher` + `CodeCompressor` + log/diff/search/html compressors are the absorbable class-level pattern.

2. **CCR reversible compression**
   - Compressed prompt reaches the LLM with retrieval markers/tools.
   - Original content is retained locally and retrievable on demand via `headroom_retrieve`.
   - This is the critical anti-loss pattern: token reduction must not destroy evidence.

3. **Proxy / SDK / CLI wrap / MCP surfaces**
   - `headroom proxy` exposes OpenAI/Anthropic-compatible endpoints and health/metrics endpoints.
   - `headroom wrap claude|codex|cursor|aider|copilot` starts a local proxy and registers retrieval MCP tooling.
   - MCP tools: `headroom_compress`, `headroom_retrieve`, `headroom_stats`.

4. **Policy hierarchy and mode separation**
   - Runtime headers, environment variables, config file, then defaults.
   - Modes include observe/audit-like behavior and optimize/simulate-like behavior.
   - Hermes should copy this as explicit “observe first, optimize later” governance, not silently mutate context.

5. **Observability as a first-class surface**
   - `/metrics`, `/health`, `/livez`, `/readyz`, `/stats`.
   - Tracks original tokens, compressed tokens, savings, latency, cache hits/misses.
   - Hermes already values proof spine; Headroom provides a concrete token-efficiency telemetry grammar.

6. **Failure learning / cross-agent memory is adjacent but risky**
   - `headroom learn` mines failed sessions and writes corrections to agent instruction files.
   - This overlaps Hermes memory/skill ratchet, but must be gated: no automatic rewrite of AGENTS/skills/memory without abstraction-layer review and provenance.

## Hermes current gap

Hermes already has strong qualitative/context governance:

- session search and context compaction,
- skills and durable memory,
- taskpool proof/evidence gates,
- token-saver skills and frontend RTK patterns,
- MCP/tool orchestration.

The missing piece is a governed **token/context control plane** that is measured, reversible, and content-type-aware before data enters the model/tool boundary.

Observed gaps:

- No unified per-tool-output compression policy with typed content routing.
- No standard reversible compression marker + retrieve contract for large tool outputs in Hermes-owned tools.
- No metrics grammar for token savings, compression latency, retrieval hit/miss, or evidence loss checks.
- No default “audit/simulate/optimize” rollout mode for context compression changes.
- No taskpool-integrated benchmark set that proves “same answer / fewer tokens / evidence retrievable”.
- Current context compaction and skills reduce tokens, but they are not a local proxy/MCP control plane with reversible retrieval semantics.

## Capability mapping

- Headroom SmartCrusher → Hermes structured tool-output reducer for JSON-heavy outputs (`list`, `search`, browser snapshots, taskpool dashboards).
- Headroom CodeCompressor → Hermes source-reading compression for large diffs/search results, preserving imports/signatures/call sites as anchors.
- Headroom log/diff/search compressors → Hermes terminal/process output summarizer with fatal/error/warning/change-point preservation.
- Headroom CCR → Hermes `compressed_artifact_id` + `retrieve_original` proof store for any lossy display.
- Headroom proxy stats → Hermes token efficiency telemetry: original_tokens, compressed_tokens, savings_ratio, retrieval_count, false_negative_recovery.
- Headroom policy hierarchy → Hermes compression-mode governance: `audit` → `simulate` → `optimize`, with per-tool override.
- Headroom learn → Hermes experience-ratchet candidate source, but only through abstraction-governance; never direct auto-write.

## Recommended Hermes intake task shape

Priority: **P1**

Reason: this directly targets token pressure and tool-output overload, but the implementation can affect evidence fidelity, privacy, and control-plane behavior. It needs multi-model review before any runtime adoption.

Smallest verifiable integration slice:

1. Build a Hermes-local prototype for **advisory-only tool-output compression**.
2. Inputs: captured safe outputs only — no credentials, no live user secrets, no provider request bodies.
3. Content classes: JSON arrays, logs, diffs/search results, source snippets.
4. Output contract:
   - compressed preview,
   - original artifact path/hash,
   - retrieval command/tool reference,
   - preservation anchors (errors, file paths, line numbers, IDs, totals),
   - token estimate before/after.
5. Verify against fixed fixtures:
   - answer-equivalence check,
   - retrieval availability,
   - no secret retention in generated summaries,
   - no evidence deletion.
6. Only after proof: discuss whether any MCP/proxy integration is warranted.

## Red lines / anti-drift constraints

- Do not install or run Headroom in Hermes core as the first step.
- Do not route live Hermes/provider traffic through an external proxy during intake.
- Do not auto-write AGENTS.md, CLAUDE.md, Hermes memories, or skills from `headroom learn`-style analysis without abstraction-governance review.
- Do not let compressed output replace original evidence. The original must remain retrievable and hashable.
- Do not compress away IDs, counts, file paths, line numbers, errors, warnings, task IDs, PR/issue references, or timestamps needed for verification.
- Do not store credentials, API keys, tokens, cookies, authorization headers, provider request bodies, or private file contents in reusable fixtures.
- Do not make token savings the only success metric; correctness and evidence retrieval are higher priority.
- Compression must be explicitly observable: mode, ratio, anchors, retrieval handle, and fallback status.

## Follow-up implementation surfaces in Hermes

Likely landing areas after multi-model certification:

- `~/.hermes/scripts/` prototype for fixture-based compression comparison.
- Hermes tool wrapper layer or browser/terminal output postprocessor, advisory-only at first.
- Taskpool proof/evidence schema extension for `compressed_preview` + `original_artifact_ref`.
- Skill/reference doc for “reversible compression proof contract”.
- Optional MCP tool later: `hermes_retrieve_artifact` for compressed proof artifacts.

## Acceptance evidence for taskpool

A task should be accepted only when it has:

- Source-pinned intake: repo metadata + commit hash + docs/files read list.
- Gap document: this file.
- Prototype plan or spike that is fixture-based and local-only.
- Multi-model certification because it touches context/evidence/privacy boundaries.
- Proof-check pass and selfcheck result; unrelated taskpool debt must be labeled separately.
