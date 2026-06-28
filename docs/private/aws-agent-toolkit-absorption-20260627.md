# AWS Agent Toolkit for AWS → Hermes MCP / skills / plugins absorption review

Task: `2026-06-26-评估-aws-agent-toolkit-for-aws-对-hermes-mcp-servers-skills-plugins-官方工具包化的可吸收模式-2`

## Source evidence

- Source repo: `https://github.com/aws/agent-toolkit-for-aws`
- Local clone: `/Users/charles/.hermes/runs/aws-agent-toolkit-for-aws-20260627`
- Commit inspected: `49c4592dc490f569d5adf0c483239886bad2f09b`
- Validation command: `cd ~/.hermes/runs/aws-agent-toolkit-for-aws-20260627 && python3 tools/validate.py` → `All validations passed.`

## Reference repo structure summary with source paths

AWS organizes the same capability package for multiple agent hosts instead of keeping skills/MCP/plugin metadata as unrelated surfaces:

1. **Marketplace manifests**
   - `.claude-plugin/marketplace.json`
   - `.agents/plugins/marketplace.json`
   - `.cursor-plugin/marketplace.json`
   - `.cursor-plugin/marketplace.json` lists four installable plugins: `aws-core`, `aws-agents`, `aws-data-analytics`, `aws-agents-for-devsecops`, each with a source path under `plugins/`.
2. **Plugin bundle directories**
   - `plugins/aws-core/`
   - `plugins/aws-agents/`
   - `plugins/aws-data-analytics/`
   - `plugins/aws-agents-for-devsecops/`
   - `plugins/aws-core/.codex-plugin/plugin.json` contains `skills: "./skills/"`, `mcpServers: "./.mcp.json"`, interface metadata, category, capabilities, and prompt examples.
3. **MCP server config per plugin**
   - `plugins/aws-core/.mcp.json` defines `mcpServers.aws-mcp.command = "uvx"` and pins `mcp-proxy-for-aws@1.6.3`.
   - Every plugin has its own `.mcp.json`: `plugins/aws-agents/.mcp.json`, `plugins/aws-agents-for-devsecops/.mcp.json`, `plugins/aws-core/.mcp.json`, `plugins/aws-data-analytics/.mcp.json`.
4. **Skills as validated leaf packages**
   - Top-level `skills/` has 64 `SKILL.md` files.
   - Plugin-local skills add 43 `SKILL.md` files under `plugins/*/skills/`.
   - `tools/validate.py` validates marketplace manifests, plugin manifests, `.mcp.json`, and skill frontmatter with a stdlib-only checker.
5. **Rules / policy overlay**
   - `rules/aws-agent-rules.md` tells agents to prefer AWS MCP, load relevant skills before acting, verify docs, use IaC for infrastructure, and follow secret-handling red lines.

## Hermes current-state comparison

Hermes already has the ingredients but not the same unified package contract:

- Built-in tool implementations live under `tools/` and auto-register through `tools/registry.py`; current inventory found 103 non-cache Python tool files.
- Skills are loaded from `skills/*/SKILL.md`; current inventory found 76 bundled skill files.
- Plugin loading is governed by `hermes_cli/plugins.py`, which documents four sources: bundled `plugins/`, user `~/.hermes/plugins/`, project `./.hermes/plugins/` when enabled, and pip entry points. Directory plugins require `plugin.yaml` plus `__init__.py register(ctx)`.
- Current inventory found 74 bundled `plugin.yaml` manifests.
- MCP servers are configured separately in `~/.hermes/config.yaml#mcp_servers`; current live config has `filesystem`, `git`, `memory`, `time`, `figma_context`, and `stitch`. This is operational, but it is not yet packaged as a per-plugin installable bundle with skill references and validation metadata.

## Transferable patterns for Hermes

Recommended absorption is **contract and validation only**, not AWS runtime integration:

1. Add a Hermes-native optional `plugin-bundle manifest` shape that can reference:
   - plugin manifest path (`plugin.yaml` / entry point),
   - bundled skills directory or explicit skill IDs,
   - MCP server config fragments,
   - safety/rules file,
   - validation command and rollback surfaces.
2. Add a read-only validator first, modeled after AWS `tools/validate.py`, to check:
   - manifest JSON/YAML parses,
   - referenced skills exist and have frontmatter,
   - referenced MCP server entries have `command` or `url`, no unexpanded `~` in args,
   - referenced plugin entry point exists,
   - capabilities and write surfaces are explicit.
3. Keep install/enable separate from discovery:
   - discovery can index a bundle,
   - validation can certify the references,
   - activation still requires the existing Hermes config/plugin authority gates.
4. Preserve Hermes red lines:
   - no AWS binding,
   - no implicit external MCP server enablement,
   - no weakening taskpool proof or mission-envelope gates,
   - no auto-install of third-party skills.

## Proposed minimal next slice

Create a follow-up implementation task for a read-only `hermes plugin-bundle validate <path>` / script equivalent that validates a manifest fixture under `tests/fixtures/plugin_bundle/` without mutating config. Acceptance should require:

- fixture manifest includes one plugin reference, one skill reference, and one MCP server fragment;
- validator rejects missing files and unexpanded `~` in MCP args;
- focused pytest covers pass/fail paths;
- no runtime MCP process is started.

## Rollback boundary

The safe rollback is deleting the validator and fixture only. Do not migrate existing plugins/skills/MCP config until the read-only validator has passed review and, for runtime enablement, a separate human-gated task explicitly authorizes config mutation.
