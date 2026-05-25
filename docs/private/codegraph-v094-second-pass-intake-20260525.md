# CodeGraph v0.9.4 second-pass intake for Hermes local code knowledge graph

Checked: 2026-05-25
Source: https://github.com/colbymchenry/codegraph
Mode: read-only live source inspection via GitHub API. No clone, install, or execution in this intake pass.

## Live source evidence

- Repository: `colbymchenry/codegraph`
- Description: `Pre-indexed code knowledge graph for Claude Code, Codex, Cursor, OpenCode, and Hermes Agent — fewer tokens, fewer tool calls, 100% local`
- Stars observed: 24,043 (user note said 16.8K+; live count is higher)
- Forks observed: 1,322
- Open issues observed: 154
- Primary language: TypeScript
- License: MIT
- Default branch: `main`
- Updated: 2026-05-25T12:06:52Z
- Pushed: 2026-05-24T18:47:05Z
- Package: `@colbymchenry/codegraph` v0.9.4

Top-level structure observed:

- `.claude/`, `.cursor/`, `.github/`
- `BUNDLING.md`, `CHANGELOG.md`, `CLAUDE.md`, `README.md`, `LICENSE`
- `src/`, `docs/`, `__tests__/`, `scripts/`, `site/`
- `install.sh`, `install.ps1`, `package.json`, `tsconfig.json`, `vitest.config.ts`

Key source directories observed:

- `src/bin/`: CLI and uninstall entrypoints.
- `src/context/`: context builder / formatter surface.
- `src/db/`: schema, migrations, query layer, SQLite adapter.
- `src/extraction/`: tree-sitter extraction, language extractors, wasm runtime flags, parser worker.
- `src/graph/`: graph traversal/query layer.
- `src/installer/`: agent installation/configuration surface.
- `src/mcp/`: MCP server, tools, transport, server instructions.
- `src/resolution/`: import/name/framework resolution.
- `src/search/`: FTS/query parsing.
- `src/sync/`: file watcher and git-hook helpers.

Tests observed include db, extraction, graph, context, MCP, installer, security, sync/watch, framework integrations, and evaluation suites.

## Architecture signals

`CLAUDE.md` describes CodeGraph as a local-first code intelligence library + CLI + MCP server. It parses supported codebases with tree-sitter, stores symbols/edges/files in SQLite/FTS5, and exposes a knowledge graph to AI agents over MCP. Per-project data lives in `.codegraph/`; extraction is deterministic from AST rather than LLM-generated summaries.

Pipeline observed from repo docs:

```text
files -> ExtractionOrchestrator(tree-sitter) -> DB(nodes/edges/files)
      -> ReferenceResolver(imports/name/frameworks)
      -> GraphQueryManager / GraphTraverser(callers/callees/impact)
      -> ContextBuilder(markdown/JSON for AI consumption)
```

`BUNDLING.md` is important relative to our previous Hermes evaluation: CodeGraph now ships self-contained platform bundles with a vendored Node runtime and Node built-in SQLite/WAL/FTS5, removing native addon build risk and Node-version dependence. This directly addresses part of the earlier Hermes evaluation residual risk, where the v0.8.0 local run hit Node/version and WASM SQLite fallback constraints.

README claims revalidated benchmark results on v0.9.4: average 35% cheaper, 57% fewer tokens, 46% faster, and 71% fewer tool calls across seven OSS codebases. Treat these as upstream benchmark claims until Hermes reproduces them on local fixed tasks.

## Existing Hermes context / duplicate check

Existing exact/source-overlap tasks already exist:

1. `2026-05-21-评估本地代码知识图谱作为-hermes-上下文定位与工具调用降噪层.md`
   - Status: done
   - Evidence: `/Users/charles/.hermes/hermes-agent/docs/private/codegraph-local-evaluation-2026-05-21.md`
   - Checked CodeGraph v0.8.0, cloned to `/tmp/codegraph`, built, indexed Hermes checkout, compared three cases.
   - Conclusion: promising symbol locator but not ready for mainline; needs multi-root indexing, staleness metadata, cache/ignore policy, degraded-backend labels, and final read_file/search_files truth gates.

2. `2026-05-23-评估本地代码知识图谱接入-hermes-主链的可维护方案.md`
   - Status: done
   - Priority: P1
   - Requires multimodel: true
   - Scope: mainline maintainability / go-no-go for local code knowledge graph.

Therefore this intake must not create another generic “analyze CodeGraph” task. The only justified follow-up is a second-pass delta task focused on v0.9.4 changes and the specific unresolved blockers from the earlier Hermes evaluation.

## Net-new mechanism vs previous Hermes evaluation

The new delta worth tracking is not “local code knowledge graph” in general; that is already evaluated. Net-new signals:

1. Self-contained bundles with vendored Node runtime.
   - May remove prior Node 25 / native SQLite / WASM fallback friction.
   - Still needs local verification; do not assume upstream bundle solves Hermes integration.

2. Node built-in SQLite/WAL/FTS5 packaging direction.
   - Could simplify local install and reduce native addon risk.
   - Needs proof that generated `.codegraph/` remains outside commits and does not leak runtime state.

3. Explicit Hermes Agent support in README/description.
   - This is a compatibility signal, not sufficient evidence for mainline adoption.
   - Need inspect generated Hermes config changes before trusting installer.

4. MCP server surface.
   - Potentially useful as a default-off advisory locator.
   - Must preserve Hermes truth gates: graph results never replace read_file/search_files/terminal/browser evidence.

## Hermes gap map

Open gaps before any absorption:

- Multi-root topology: Hermes truth surfaces span `/Users/charles/.hermes/hermes-agent`, `/Users/charles/.hermes/scripts`, skills, profile overlays, and brain task files. A single repo-root index misses core control-plane files.
- Staleness contract: every graph answer must expose indexed roots, source revision/hash/mtime, index age, backend type, and degraded/fallback status.
- Evidence contract: graph outputs are advisory navigation hints only; final implementation claims still require original source reads and tests/build/selfcheck/proof-check.
- Cache/ignore governance: `.codegraph/` or any generated DB must not enter git or private docs accidentally.
- Installer safety: CodeGraph installer changes agent configs/instructions/permissions; Hermes intake must inspect those changes in a disposable sandbox before allowing any persistent config mutation.
- MCP permission boundary: query tools should be read-only and scoped to explicit roots; no background watcher or git hook should run without a permission gate.

## Proposed follow-up task shape

Create an incremental second-pass task:

- Title: `Second-pass evaluate colbymchenry/codegraph v0.9.4 bundled local code graph for Hermes multi-root advisory locator`
- Lane: `evolution`
- Priority: `P2`
- Architecture target: `local-brain` or `taskpool` (taskpool if focusing on evidence/dispatch; local-brain if focusing on codebase intelligence surface)
- Requires multimodel: true before integration/closeout because it affects MCP, local indexing, evidence truth gates, privacy, and taskpool code-navigation workflows.

Acceptance should require:

1. Reproduce or reject the prior v0.8.0 blockers on v0.9.4 without mutating user agent configs.
2. Verify bundled runtime / SQLite backend behavior on macOS in a disposable path.
3. Design a default-off, multi-root advisory locator contract for Hermes, including staleness metadata and cache/ignore policy.
4. Compare against fixed Hermes maintenance questions with current `search_files`/`read_file` baselines.
5. Produce go/no-go for a prototype; do not enable as default truth source.

## Red lines

- Do not run `curl | sh`, global npm install, or installer auto-config against the user’s real agent configs during intake.
- Do not commit `.codegraph/` or generated DBs.
- Do not let graph results override source reads, tests, build, browser/curl proof, or taskpool evidence gates.
- Do not index private user/session/taskpool data into external systems; if indexing local data, keep it local and disposable unless explicitly approved.
- Do not duplicate the completed generic CodeGraph evaluation; focus only on v0.9.4 delta and unresolved Hermes blockers.
