---
title: Local Codegraph
description: Use codebase-memory-mcp as a local code knowledge graph for Hermes.
---

# Local Codegraph

Hermes can integrate with [`codebase-memory-mcp`](https://github.com/DeusData/codebase-memory-mcp) as a local code knowledge-graph backend.

The integration is safe by default:

- source code is indexed locally;
- cache is scoped under `$HERMES_HOME/cache/codebase-memory-mcp`;
- `enable` writes an activation snippet but does not modify `config.yaml` unless `--merge-config` is explicit;
- graph results are advisory navigation evidence, not a replacement for reading source and running tests.

## Commands

```bash
hermes codegraph list
hermes codegraph doctor
hermes codegraph install --dry-run
hermes codegraph install
hermes codegraph enable
hermes codegraph smoke --repo /path/to/repo --query "symbol or business term"
```

## Install the backend

Preview the selected release asset and checksum manifest:

```bash
hermes codegraph install --dry-run
```

Install the verified release binary into `$HERMES_HOME/bin/codebase-memory-mcp`:

```bash
hermes codegraph install
```

Hermes chooses the asset for the current OS/architecture. On Linux it prefers the portable bundle. To pin an exact release or asset:

```bash
hermes codegraph install --tag v0.8.1
hermes codegraph install --asset codebase-memory-mcp-darwin-arm64.tar.gz
```

The installer downloads `checksums.txt`, verifies that the selected asset is present, downloads the archive, verifies SHA-256, safely extracts it, and then verifies the installed binary with `--version`.

## Enable Hermes config

Create Hermes-scoped activation files:

```bash
hermes codegraph enable
```

This writes:

```text
$HERMES_HOME/config/codegraph-mcp.yaml
$HERMES_HOME/state/codegraph-activation.json
$HERMES_HOME/cache/codebase-memory-mcp/
```

It does **not** merge the MCP server into `$HERMES_HOME/config.yaml` by default. To do that explicitly:

```bash
hermes codegraph enable --merge-config
```

A backup of `config.yaml` is created before merge.

## Smoke test a project

```bash
hermes codegraph smoke \
  --repo /path/to/project \
  --query "payment service" \
  --function-name process_payment
```

The smoke test runs:

1. `index_repository`
2. `list_projects`
3. `search_graph`
4. `trace_path` when `--function-name` is provided

## Evidence rules

Use codegraph to reduce context and find likely files quickly. Before editing or claiming behavior, still verify with:

- `read_file` or direct source inspection;
- `git diff`;
- build/typecheck/tests;
- runtime or browser verification when relevant.

Do not commit `.codebase-memory/graph.db.zst` or other generated graph/cache files unless a project explicitly treats them as artifacts.
