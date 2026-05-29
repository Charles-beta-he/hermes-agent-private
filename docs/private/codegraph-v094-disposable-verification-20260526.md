# CodeGraph v0.9.4 disposable verification tranche

Task: `2026-05-25-second-pass-evaluate-colbymchenry-codegraph-v094-bundled-local-code-graph-for-he`
Checked: 2026-05-26
Run directory: `/tmp/hermes-codegraph-v094-followthrough-20260526071851`
Mode: disposable package/runtime verification; no persistent Hermes config mutation; no global install; no `.codegraph/` committed.

## Commands / artifacts

- Downloaded npm metadata and tarball for `@colbymchenry/codegraph@0.9.4` with Python `urllib` into `/tmp/hermes-codegraph-v094-followthrough-20260526071851`.
- Extracted npm package and inspected `package.json` plus `npm-shim.js`.
- Ran CodeGraph through the shim with isolated `HOME=/tmp/hermes-codegraph-v094-followthrough-20260526071851/home` and `CODEGRAPH_INSTALL_DIR=/tmp/hermes-codegraph-v094-followthrough-20260526071851/cache`.
- Created disposable two-file Python sample under `/tmp/hermes-codegraph-v094-followthrough-20260526071851/sample` and ran:
  - `node npm-shim.js --help`
  - `node npm-shim.js init /tmp/hermes-codegraph-v094-followthrough-20260526071851/sample --index`
  - `node npm-shim.js query AIAgent --path ... --limit 5`
  - `node npm-shim.js context 'AIAgent run_conversation' --path ...`
  - `node npm-shim.js status ...`
  - `node npm-shim.js install --print-config hermes`
  - stale-index probe: query new symbol before sync, run `sync`, query after sync.

Raw artifacts retained in the run dir:

- `package-summary.json`
- `npm-shim.js` from extracted package
- `codegraph-help.txt`
- `codegraph-init.txt`
- `codegraph-query-aiagent.txt`
- `codegraph-context.txt`
- `codegraph-status-before.txt`
- `codegraph-install-help.txt`
- `codegraph-print-config-hermes.txt`
- `codegraph-query-before-sync.txt`
- `codegraph-sync.txt`
- `codegraph-query-after-sync.txt`
- `codegraph-status-after.txt`
- `disposable-run-summary.json`
- `mutation-check.json`

## Findings vs prior v0.8.0 blocker

### Bundled runtime / SQLite

PASS for the specific prior blocker. The npm package itself is a thin shim (`bin.codegraph = npm-shim.js`) and the first invocation resolved/downloaded the platform bundle into the isolated `CODEGRAPH_INSTALL_DIR`:

```text
codegraph: platform bundle missing (registry did not provide @colbymchenry/codegraph-darwin-arm64).
codegraph: downloading codegraph-darwin-arm64.tar.gz from GitHub Releases (0.9.4)...
codegraph: checksum verified.
codegraph: bundle ready.
```

`codegraph status` on the disposable sample reports:

```text
Backend: node:sqlite — built-in (full WAL)
Journal: wal
✓ Index is up to date
```

This removes the v0.8.0 evaluation's local friction around Node-version refusal / native `better-sqlite3` unavailability / WASM fallback for the tested macOS arm64 path.

### Disposable index behavior

PASS for basic local-only indexing on a small Python project:

- `init --index` indexed 2 files.
- SQLite tables include `files`, `nodes`, `edges`, `nodes_fts`, `project_metadata`, and related FTS/schema tables.
- `disposable-run-summary.json` observed `files_count=2`, `nodes_count=7`, `edges_count=9` after initial index.
- Query found `AIAgent` at `app.py:1` and related method/import symbols.
- Context query emitted source snippets for `run_conversation`, `AIAgent`, `chat`, and `main`.

### Staleness / sync behavior

PASS for explicit sync, but not enough for Hermes truth-gate replacement:

- After appending `new_symbol_for_staleness_probe` to `app.py`, a query before sync returned no results.
- After `codegraph sync`, output reported `Synced 1 changed files` and the same query found the new function at `app.py:9`.
- Therefore Hermes must require a visible freshness contract around any CodeGraph answer: indexed root(s), backend, DB path/cache root, index status, last sync/index time or source snapshot, and a final `read_file`/`search_files` confirmation before implementation claims.

### Installer / MCP permission surface

WARN / permission-gated:

- `install --help` confirms `--print-config <id>` exits with no file writes and normal install can write MCP config into agents including Hermes Agent.
- `install --print-config hermes` produced a Hermes config snippet:

```yaml
mcp_servers:
  codegraph:
    command: codegraph
    args:
      - serve
      - --mcp
    timeout: 120
    connect_timeout: 60
    enabled: true

platform_toolsets:
  cli:
    - hermes-cli
    - mcp-codegraph
```

- Mutation check after print-config: isolated `$HOME` stayed empty; real `/Users/charles/.hermes/hermes-agent/.codegraph` absent; real `/Users/charles/.codegraph` absent.
- A persistent Hermes MCP/toolset registration must remain explicit-approval only. The printed snippet is too broad for default mainline because it enables `mcp-codegraph` and starts `codegraph serve --mcp` without embedding Hermes-specific staleness/source-truth red lines in every returned result.

## Go / no-go decision

Decision: **GO for a default-off prototype design only; NO-GO for default enablement or direct truth-gate integration.**

The v0.9.4 bundle materially improves the runtime risk from the prior v0.8.0 evaluation: the disposable macOS arm64 run used bundled CodeGraph with built-in `node:sqlite`/WAL and successfully indexed/query/contexted a local Python project. However, the result still does not solve Hermes' multi-root topology, freshness disclosure, generated DB/cache governance, or source-truth proof requirements by itself.

## Required Hermes contract before any prototype

1. **Default-off only**: no automatic MCP registration and no global toolset enablement.
2. **Disposable/external cache first**: generated `.codegraph/` must live in a disposable or explicitly ignored/cache path; never commit generated DBs.
3. **Multi-root aware**: a Hermes prototype must cover at least `~/.hermes/hermes-agent` and `~/.hermes/scripts`; optional brain/taskpool indexing requires explicit privacy approval.
4. **Staleness payload required**: every advisory answer must show roots, backend (`node:sqlite` vs degraded), DB path/cache root, index status, and sync/source snapshot before use.
5. **Advisory-only truth gate**: final claims must still cite `read_file`, `search_files`, tests/build/runtime checks, and taskpool `selfcheck`/`proof-check` where relevant. CodeGraph may reduce locator cost, not certify implementation truth.
6. **MCP permission boundary**: `codegraph serve --mcp` is read-only locator/query/context/impact surface only; watcher/background sync and any config writes require explicit permission gates.
7. **Prototype evaluator**: benchmark against fixed Hermes maintenance questions from the prior evaluation, including the control-plane question that crosses repo root into `~/.hermes/scripts`.

## Closeout assessment for this task

The task's bounded evidence tranche satisfies the requested second-pass evaluation closeout: net-new v0.9.4 bundle/SQLite/MCP deltas were inspected, the prior runtime blocker was re-tested in a disposable path, staleness behavior was probed, the Hermes print-config was read without mutation, and a go/no-go contract was recorded. No mainline code/config integration was performed or implied.
