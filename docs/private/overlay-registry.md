# Private Hermes Overlay Registry

This registry records local Hermes enhancements that must remain explicit while the checkout tracks upstream.

## Governance rules

- Upstream remote is read-only: `upstream=https://github.com/NousResearch/hermes-agent.git`.
- Private remote is the writable canonical repo: `origin=https://github.com/Charles-beta-he/hermes-agent-private.git`.
- Runtime state, logs, sessions, raw memory, credentials, caches, and SQLite state are not source assets.
- Every long-lived local enhancement must be one of: committed overlay, registered replay check, or retired because upstream absorbed it.
- After each upstream sync run `python3 ~/.hermes/scripts/post-update-check.py --fix`, then inspect the summary rather than trusting exit code alone.
- Upstream sync is ratchet-style: watcher detection and impact mapping are allowed, but `private/main` promotion requires recorded verification evidence.
- Every upstream SHA must have a semantic multi-model review packet before integration work starts: `~/.hermes/reports/upstream-ratchet/<upstream-sha-12>/review-packet.json`.

## Ratchet evidence fields

For each overlay, ratchet decisions must record:

- `replay_verified_sha`: upstream/private integration commit where replay was verified.
- `last_replay_status`: `kept`, `absorbed`, `redesigned`, or `blocked`.
- `retire_justification`: equivalence evidence when upstream absorbed the capability.

A path match is never enough to retire an overlay; retire only when behavior equivalence is proven by tests or explicit review evidence.

## Active overlays

| ID | Purpose | Main touchpoints | Tests / evidence | Replay / retire rule |
| --- | --- | --- | --- | --- |
| continuation-control-plane | Durable continuation packets, auto-relay, fragmentation guards | `agent/continuation.py`, `agent/conversation_loop.py`, `tests/agent/test_continuation.py`, `tests/run_agent/test_auto_relay_continuation.py`, `tests/scripts/test_continuation_fragmentation_guard.py` | Targeted pytest slices for continuation and auto-relay | Keep until upstream has equivalent durable handoff semantics and budget-exhaustion relay behavior |
| result-spilling-retrieval | Spill oversized tool/model results and retrieve them safely | `agent/result_spiller.py`, `tools/retrieve_result_tool.py`, `tests/agent/test_result_spill_retrieval.py`, `tests/tools/test_retrieve_result_tool.py` | Result spill/retrieve tests | Keep while long outputs exceed prompt budget; retire only if upstream exposes equivalent retrieval tool |
| token-compression-routing | Token budget reduction, query intent routing, task router, deterministic bypass | `agent/token_compressor.py`, `agent/task_router.py`, `agent/query_intent.py`, `agent/deterministic_bypass.py`, `agent/budget_meter.py` | Compile + targeted router/compression tests when available | Keep while private PA workflow depends on low-token routing decisions |
| cognitive-openclaw-control-plane | Default-off cognitive/OpenClaw-informed advisory layer | `agent/openclaw_control_plane.py`, `agent/cognitive_pipeline.py`, `agent/cognitive_state.py`, `agent/conductor_runtime.py`, `agent/anatomy_scanner.py`, `agent/pattern_retriever.py`, `agent/system_prompt.py` | `tests/agent/test_openclaw_control_plane.py`, cognitive/conductor tests | Must remain fail-soft/default-off; retire only if upstream absorbs equivalent safe advisory plane |
| prompt-cache-layering | Stable/context/volatile prompt cache splitting and native cache blocks | `agent/prompt_builder.py`, `agent/prompt_caching.py`, `agent/chat_completion_helpers.py` | prompt builder/cache tests | Keep while upstream lacks local cache-layer behavior; verify cache markers after each sync |
| gemini-oauth-integration | Gemini OAuth provider/model picker/fallback wiring | `agent/gemini_cloudcode_adapter.py`, `hermes_cli/model_switch.py`, `hermes_cli/providers.py`, Gemini tests | Gemini OAuth model picker and adapter tests | Retire once upstream supports the same OAuth provider path |
| background-review-resilience | Background review summary filtering, resilience, gateway/CLI surfacing | `agent/background_review.py`, `hermes_cli/main.py`, `hermes_cli/gateway.py`, background review tests | background review regression tests | Keep while bg-review/tool-result false alarms are possible |
| mcp-memory-hardening | MCP timeout/RPC hardening and memory gating | `tools/mcp_tool.py`, `tools/memory_tool.py`, `agent/memory_manager.py` | memory tool and MCP smoke checks | Keep until upstream equivalent is confirmed by grep + test |

## First landing checkpoint

- Freeze branch: `backup/landing-20260518-203325`
- Freeze tag: `backup-landing-20260518-203325`
- Freeze directory: `/Users/charles/.hermes/backups/hermes-governance/landing-20260518-203325`
- Private repo: `https://github.com/Charles-beta-he/hermes-agent-private`
