# origin/private/main divergence audit — 2026-06-06

## Context

Local runtime branch `private/main` was rebased/reconstructed onto the latest official Hermes upstream baseline:

- upstream baseline: `56236b16e feat(dashboard): rehaul Skills hub browser — connected hubs, featured, preview + security scan (#40384)`
- local overlay commit: `f83492b8c [verified] Align private overlays with upstream main`
- local vs upstream: `1 ahead / 0 behind`

The private remote branch `origin/private/main` still pointed at the older private-history line:

- remote head before convergence: `e8a334041 feat(mcp): add real-client semantic probes for MCP tool health checks (#7)`
- local vs remote before convergence: `1292 ahead / 69 behind`

A dry merge-tree check showed conflicts in old private-line files such as:

- `agent/agent_init.py`
- `agent/auxiliary_client.py`
- `agent/chat_completion_helpers.py`
- `agent/codex_runtime.py`
- `agent/conversation_loop.py`
- `agent/skill_commands.py`
- `cli.py`

So the old remote branch was not merged wholesale.

## Audit summary of the 69 remote-only commits

The remote-only commits are mostly the former private overlay stack and evidence/docs from 2026-05-18 through 2026-06-03. They fall into these classes:

### Covered by the new verified overlay commit

These capabilities were replayed narrowly and verified in the new upstream-aligned local commit:

- local runtime artifact ignore rules
- core private overlay governance docs:
  - `docs/private/overlay-registry.md`
  - `docs/private/upgrade-sop.md`
  - `docs/private/stable-points.md`
  - `docs/private/multibrain-architecture.md`
- selected continuation/result/token/conductor/OpenClaw/cognitive overlay modules:
  - `agent/result_spiller.py`
  - `agent/token_compressor.py`
  - `agent/conductor_runtime.py`
  - `agent/openclaw_control_plane.py`
  - `agent/cognitive_state.py`
  - `agent/codex_circuit_breaker.py`
  - `agent/task_router.py`
- selected post-update hardening call sites:
  - `agent/context_compressor.py`
  - `agent/conversation_loop.py`
  - `agent/memory_manager.py`
  - `agent/prompt_builder.py`
  - `hermes_cli/gateway.py`
  - `tools/mcp_tool.py`
  - `tools/memory_tool.py`
- regression tests for the replayed surfaces:
  - `tests/agent/test_conductor_runtime.py`
  - `tests/agent/test_openclaw_control_plane.py`
  - `tests/agent/test_cognitive_state.py`
  - `tests/tools/test_memory_tool_openclaw_gate.py`

### Not replayed wholesale by design

The following old-private-line areas were intentionally not blindly merged because they either touch high-conflict mainline seams, are older experimental surfaces, or need separate ratchet tickets if still wanted:

- broad continuation/auto-relay implementation files beyond the verified replay subset
- prompt-cache layering files beyond the currently verified prompt-builder hooks
- Gemini OAuth/provider overlays
- Kanban cross-surface progress bridge
- norm governance control plane
- post-chat reflection ledger and run-envelope ledger
- browser replay evidence changes
- process kill lifecycle hardening
- skills health audit CLI additions
- MCP semantic probe additions
- abstraction-governance file/tool gates
- assorted reference-intake docs from late May / early June

### Why not merge old remote branch

The old remote branch was based on a much older upstream history and would reintroduce conflicts plus stale mainline edits. The safer convergence policy is:

1. Preserve the old remote head under a backup branch before rewriting.
2. Keep `private/main` as `upstream/main + verified overlays`.
3. Reintroduce any old-private-line capability later only via scoped cherry-pick/port with tests.

## Verification before remote rewrite

The upstream-aligned local branch was verified with:

- Python compile for touched runtime files
- targeted overlay tests: `22 passed`
- `git diff --check`
- `post-update-check.py --fix`: `16 OK | 0 fixed | 2 need attention`
  - remaining attention items were non-blocking telemetry/quality warnings, not update regressions

## Remote convergence plan

Before rewriting `origin/private/main`, push the previous remote head to a dated backup branch:

- `backup/origin-private-main-before-upstream-align-20260606`

Then push local `private/main` to `origin/private/main` with `--force-with-lease` pinned to the audited old remote head, not blind `--force`.

## Follow-up candidates

If any old remote-only capability is still desired, process it as a separate ratchet task:

1. inspect the old commit/file intent;
2. check whether upstream already absorbed it;
3. port a minimal patch onto current upstream-aligned `private/main`;
4. add/restore focused tests;
5. run post-update checks;
6. commit scoped evidence.
