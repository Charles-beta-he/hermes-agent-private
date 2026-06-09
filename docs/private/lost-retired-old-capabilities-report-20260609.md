# Lost / retired old capability report — 2026-06-09

## Scope

This report compares the current upgraded `private/main` against two preserved baselines:

- Pre-upstream-ratchet backup: `backup/private-main-pre-20260609-upstream-ratchet` (`cfd3b39ab`)
- Older private line backup: `origin/backup/origin-private-main-before-upstream-align-20260606` (`e8a334041`)
- Current head at audit time: `8da2d29e1`
- Current upstream at audit time: `6a8dda171`

## Executive summary

No registered critical runtime overlay was silently lost during the final upstream ratchet. The core replayed overlays still exist and were verified after the upgrade: auto-relay continuation, conductor router import seam, memory provider rollback, CLI smoke, and post-update gates.

However, several older private-line capabilities remain intentionally quarantined or effectively retired rather than restored. They are not in current HEAD and should not be described as active capabilities until separately ratcheted back with tests.

## A. Newly removed or absorbed during the 2026-06-09 upstream ratchet

| Capability / surface | Current status | Evidence | Impact |
| --- | --- | --- | --- |
| Desktop `CronJobActionsMenu` component | Deleted from current HEAD; mostly absorbed into upstream/current cron page layout | `apps/desktop/src/app/cron/cron-job-actions-menu.tsx` existed in `cfd3b39ab`, missing in current HEAD. Current full cron page still exposes Pause/Resume, Trigger now, Edit, Delete in `apps/desktop/src/app/cron/index.tsx`. Sidebar quick row exposes Trigger and Manage only in `apps/desktop/src/app/chat/sidebar/cron-jobs-section.tsx`. | No backend cron capability loss. Possible UX regression if the old sidebar/dropdown row actions were required: pause/resume/edit/delete are no longer all available from the compact sidebar menu; they require opening the cron management/detail surface. |
| Home Assistant gateway adapter old path | Absorbed into plugin layout | `gateway/platforms/homeassistant.py` was renamed/moved to `plugins/platforms/homeassistant/adapter.py`; plugin metadata added at `plugins/platforms/homeassistant/plugin.yaml`. | No capability loss expected; path/import consumers must use plugin adapter layout. |
| Old `hermes_cli/main.py` monolith drift | Retired / absorbed upstream | Upstream modularized CLI into `hermes_cli/subcommands/*`; current `hermes_cli/main.py` is smaller and smoke-tested. | Intentional architecture retirement, not feature loss, unless an old private CLI-only subcommand was not ported separately. |

## B. Older private-line capabilities still not restored in current HEAD

These are real old-private capabilities or WIP branches that remain absent from current HEAD. They were already identified in the 2026-06-06 ratchet plan as needing separate scoped ports.

| Capability family | Current status | Evidence | Risk / note |
| --- | --- | --- | --- |
| `post_chat_reflection_and_run_envelope` | Missing / quarantined | Old line had `agent/post_chat_reflection.py`, `agent/run_controller.py`, `agent/run_envelope.py`; all are missing in current HEAD. Related WIP still exists in stashes (`agent/run_controller.py`, `agent/run_envelope.py`, run-envelope tests/docs). | Real old capability not active. Should only be restored as suggest-only/default-off; avoid automatic memory/skill mutation until bg-review safeguards are reconciled. |
| `norm_governance_control_plane` | Missing / quarantined | Old line had `agent/norm_governance.py`, `agent/norm_write_guard.py`, `agent/norm_taskpool_gate.py`, `hermes_cli/norms.py`; all missing in current HEAD. Current `tools/file_tools.py`, `tools/memory_tool.py`, and `tools/skill_manager_tool.py` still optional-import `agent.norm_write_guard`; because the file is absent, that extra durable write guard is disabled/fail-open. | This is the highest-priority possible regression if we still want old norm write gating. Current abstraction governance remains present, but norm_write_guard-specific protections are not active. Needs careful redesign to avoid duplicate norm SSOT. |
| `mcp_semantic_probes` | Missing / quarantined | Old line had `tools/mcp_semantic_probe.py`; current HEAD does not. `tools/mcp_tool.py` and `model_tools.py` exist. | Real-client semantic MCP health probes are not active. Existing MCP timeout/RPC hardening remains, but semantic probe capability is not restored. |
| `browser_replay_evidence` | Partial / missing tests-docs | `tools/browser_tool.py` and `tools/website_policy.py` exist, but old `tests/tools/test_browser_replay_evidence.py` and `website/docs/developer-guide/browser-replay-evidence.md` are missing. | Browser replay evidence persistence is not certified. Avoid claiming this capability is active beyond whatever current browser tool behavior already supports. |
| `taskpool_validation_security_gates` | Missing / quarantined | Current HEAD lacks `tests/run_agent/test_taskpool_dispatch_tick_runtime.py` and `tests/run_agent/test_taskpool_run_envelope.py`; older docs mention long-task evidence validator and provider routing cost-quality validator as not restored. | Anti-false-done/provider-routing validators are not part of current test gate. Restore as report-only scripts first if needed. |
| `reference_intake_docs_and_skills` | Selectively not restored | Old docs such as `docs/private/agentmemory-gap-analysis.md` and `docs/private/openclaw-garden-skills-gap-analysis.md` are absent. | Documentation/reference loss only; restore selectively if still useful, but avoid polluting overlay registry with stale claims. |

## C. Registry drift / partially declared overlays

These are not newly lost in the latest merge; they are registry or plan drift where the registry names files that were already absent from the immediate pre-ratchet backup and remain absent now.

| Overlay | Drift | Evidence |
| --- | --- | --- |
| `result-spilling-retrieval` | `agent/result_spiller.py` exists, but `tools/retrieve_result_tool.py` and the listed retrieve-result tests are absent in both the pre-ratchet backup and current HEAD. | Current code still contains `retrieve_result(...)` hints in `agent/result_spiller.py` / `agent/token_compressor.py`, but no standalone tool file was found. |
| `token-compression-routing` | `agent/token_compressor.py` and `agent/task_router.py` exist, but `agent/query_intent.py`, `agent/deterministic_bypass.py`, and `agent/budget_meter.py` are absent in both pre-ratchet backup and current HEAD. | Treat the overlay as partial; do not claim full old routing stack is active. |
| `cognitive-openclaw-control-plane` | Core files exist (`openclaw_control_plane`, `cognitive_state`, `conductor_runtime`, `system_prompt`), but `cognitive_pipeline.py`, `anatomy_scanner.py`, and `pattern_retriever.py` are absent in both pre-ratchet backup and current HEAD. | Current OpenClaw/conductor core is active and tested, but broader cognitive pipeline pieces are not active. |

## D. Confirmed not lost / carried forward

| Capability | Evidence |
| --- | --- |
| Auto-relay continuation | `agent/conversation_loop.py` contains `_try_auto_relay`; `agent/turn_finalizer.py` invokes it; targeted tests passed during upgrade. |
| Kanban budget failure handoff | `agent/conversation_loop.py` contains `_record_kanban_budget_failure`; `agent/turn_finalizer.py` calls it after relay gate. |
| Conductor router helper | `agent/conversation_loop.py` contains `_run_conductor_intent_router`; `tests/agent/test_conductor_runtime.py` passed. |
| Memory provider rollback | `agent/memory_manager.py` retains rollback on provider `get_tool_schemas()` failure; post-update check reports `memory_manager_provider_rollback: OK`. |
| Gemini OAuth provider overlay | `agent/gemini_cloudcode_adapter.py`, `hermes_cli/model_switch.py`, `hermes_cli/providers.py`, and `tests/agent/test_gemini_cloudcode.py` are present. |
| Prompt cache layering | `agent/prompt_caching.py`, `agent/chat_completion_helpers.py`, and `agent/prompt_builder.py` are present. |
| Process lifecycle hardening | `tools/process_registry.py` and `tests/tools/test_process_registry.py` are present; process registry targeted tests were included in the upgrade suite. |
| Abstraction governance file/tool gates | `agent/abstraction_governance.py`, `model_tools.py`, `tools/file_tools.py`, `tools/skill_manager_tool.py`, and `tests/agent/test_abstraction_governance.py` are present. |

## E. Stashes that still contain unreplayed WIP / old capabilities

Current stash inventory still includes old WIP not applied to HEAD:

- `stash@{0}`: delegate tool/test edits plus private reference docs.
- `stash@{1}`: `tests/run_agent/test_taskpool_run_envelope.py`.
- `stash@{2}`: `rowboat_tree.json`, `tests/run_agent/test_taskpool_dispatch_tick_runtime.py`.
- `stash@{3}`: older post-update fixes for context compressor, memory manager, prompt builder, gateway.
- `stash@{4}`: run envelope/controller, browser replay evidence test/docs, kanban DB/auxiliary client edits, private docs.

These are evidence/WIP sources, not active capabilities.

## Recommended next actions

As of follow-up closure commit, there are no active upgrade-blocking old-capability remnants. The remaining old-line surfaces are explicitly classified as retired/quarantined evidence, not current product debt:

1. `norm_governance_control_plane`: retired from runtime. Stale optional imports of `agent.norm_write_guard` were removed from file/memory/skill tools and covered by `tests/tools/test_retired_norm_governance_remnants.py`.
2. `taskpool_validation_security_gates`: retired as a lifecycle-blocking gate. Any future restoration must start as report-only tests/scripts.
3. `browser_replay_evidence`: retired as an active/public capability because current `tools/browser_tool.py` does not implement replay bundle persistence. Historical docs/tests remain only in backup refs/stashes.
4. `post_chat_reflection_and_run_envelope`: retired as active runtime because it would introduce a competing ledger/control-plane and possible cron/reflection writes. Historical evidence remains in backup refs/stashes.
5. `mcp_semantic_probes`: retired as active runtime gate. Non-mutating semantic MCP probes may be restored later as report-only diagnostics, but are not claimed active now.
6. `overlay-registry.md` was corrected so partial overlays no longer claim absent modules/tools/tests as active.

Stashes listed above are preserved as historical evidence/WIP sources. They are not active capabilities and should not be popped wholesale; future use requires a new scoped ratchet tranche with tests.
