# Hermes private overlay ratchet merge plan — 2026-06-06

## Trigger

User correctly challenged the post-upgrade state: several old private-line capabilities were not replayed wholesale during upstream alignment. They are **not deleted as product decisions**; they are quarantined behind the backup ref and must be reintroduced through a ratchet process: evidence → multi-model review → scoped port → tests → post-update-check → commit.

## Evidence anchors

- Current branch: `private/main`
- Current upstream-aligned overlay commit: `f83492b8c [verified] Align private overlays with upstream main`
- Current audit commit: `0407d7e3f docs(private): audit remote divergence before alignment`
- Official upstream baseline: `56236b16e feat(dashboard): rehaul Skills hub browser — connected hubs, featured, preview + security scan (#40384)`
- Old private head preserved at: `origin/backup/origin-private-main-before-upstream-align-20260606` = `e8a334041`
- Old-only commit inventory: 67 non-merge commits reachable from old backup but not current HEAD.
- Existing divergence audit: `docs/private/origin-private-divergence-audit-20260606.md`

## Ratchet rule

Do **not** cherry-pick the old branch wholesale. For each capability family:

1. Compare old implementation against current upstream/current private overlay.
2. Classify: already absorbed / replayed partial / still valuable / stale / dangerous.
3. If still valuable, create a small port patch with default-off or fail-soft boundary where needed.
4. Add or restore focused tests.
5. Run at minimum: `py_compile` for touched runtime files, focused pytest, `git diff --check`, `python3 ~/.hermes/scripts/post-update-check.py --fix`.
6. Commit as one scoped ratchet tranche.

## Capability families to ratchet

### P0/P1 safety and runtime integrity first

1. `abstraction_governance_file_tool_gates`
   - Old commits: `e8a334041`, `1e0a9a21a`, related file/tool gate tests.
   - Old surfaces: `agent/abstraction_governance.py`, `model_tools.py`, `tools/file_tools.py`, `tools/skill_manager_tool.py`, `tests/agent/test_abstraction_governance.py`.
   - Why first: protects file/tool writes and prevents governance bypass after update.
   - Merge mode: review old gate semantics, port only policy-neutral guardrails; avoid hardcoding project facts.

2. `process_kill_lifecycle_hardening`
   - Old commit: `1e0a9a21a [verified] Harden process kill lifecycle`.
   - Old surfaces: `tools/process_registry.py`, `tests/tools/test_process_registry.py`.
   - Why first: destructive process actions need audited lifecycle behavior.
   - Merge mode: scoped port + regression test; fail-safe but avoid blocking recoverable user-controlled cleanup.

3. `tool_guardrail_halt_guidance`
   - Old commit: `72cdad441 Improve tool guardrail halt guidance`.
   - Old surfaces: `agent/tool_guardrails.py`, `run_agent.py`, `tests/agent/test_tool_guardrails.py`, `tests/run_agent/test_tool_call_guardrail_runtime.py`.
   - Why first: improves safety when tool guardrails trip.
   - Merge mode: text/flow level port if current upstream has not absorbed it.

### P1 continuity and execution recovery

4. `continuation_auto_relay_budget_handoff`
   - Old commits: `3b3e7cdc2`, `f60a24807`, `54dd0b4ca`, `340a65176`, `40d5e28a3`, `4049c25b4`.
   - Old surfaces: `agent/continuation.py`, `agent/conversation_loop.py`, `agent/background_review.py`, `agent/tool_executor.py`, continuation tests.
   - Current state: only selected result/token/conductor pieces replayed; broad relay/handoff stack not replayed.
   - Merge mode: split into sub-tranches: active continuation cap, goal-bound handoff, auto-relay quota reset, kanban worker auto-relay. Each must keep recursion limits and no-stale-state rules.

5. `codex_auxiliary_fallback_salvage`
   - Old commits: `b79d73224`, `b087cc75f`, `25412427b`.
   - Old surfaces: `agent/codex_runtime.py`, `agent/auxiliary_client.py`, tests.
   - Why: prevents null output / auxiliary fallback chain failures.
   - Merge mode: compare with current upstream delegate/provider handling; port only missing failure-detection logic.

6. `cron_legacy_schedule_recovery`
   - Old commit: `938cd663b fix cron scheduler legacy schedule recovery`.
   - Old surfaces: `cron/jobs.py`, `tests/cron/test_jobs.py`.
   - Why: scheduled PA/report reliability.
   - Merge mode: verify current scheduler behavior first; port compatibility test before code.

### P1/P2 observability and taskpool governance

7. `kanban_cross_surface_progress_bridge`
   - Old commits: `b6eb631c6`, `2dc0af46e`.
   - Old surfaces: `gateway/run.py`, `hermes_cli/kanban.py`, `hermes_cli/kanban_db.py`, delegate/progress tests.
   - Why: improves visibility across Telegram/gateway/kanban/taskpool.
   - Merge mode: default read-only/status-only; avoid mutating task lifecycle from presentation surfaces.

8. `taskpool_validation_security_gates`
   - Old commits: `3304acb40`, `e1c44a775`, `a8c911894`, `73440e57c`.
   - Old surfaces: taskpool security regression gate, long-task evidence validator, provider routing cost-quality evidence validator.
   - Why: preserves anti-false-done and provider-routing governance.
   - Merge mode: scripts/tests first; hook into post-update-check only after stable.

9. `skills_health_audit_cli`
   - Old commits: `75e8ec705`, `fa91f2533`.
   - Old surfaces: `hermes_cli/main.py`, `hermes_cli/skills_hub.py`, tests.
   - Why: post-update-check currently warns many skills lack tests; old audit may help quantify.
   - Merge mode: CLI/report-only first; no fail-closed until coverage policy is ratified.

### P2 model/provider and prompt efficiency

10. `gemini_oauth_provider_overlay`
    - Old commit: `6e49e25ca` plus model config normalization `d6c38900e`.
    - Old surfaces: `agent/gemini_cloudcode_adapter.py`, `hermes_cli/model_switch.py`, `hermes_cli/providers.py`, cron/model config tests.
    - Current environment: `model-router.py probe` reports `gemini-3.1-pro-preview` and `gemini-3-flash-preview` available through `google-gemini-cli`, so some capability may already exist in config/scripts.
    - Merge mode: evidence-first gap check; do not displace current primary model; fallback/optional participant only.

11. `prompt_cache_layering`
    - Old commit: `5536c4b8d` plus norm prompt injection `a15648e93`.
    - Old surfaces: `agent/prompt_caching.py`, `agent/chat_completion_helpers.py`, `agent/prompt_builder.py`, prompt cache tests.
    - Current state: prompt-builder hooks replayed only partially.
    - Merge mode: certify stable/context/volatile cache boundary first; no provider-specific cache markers without tests.

12. `mcp_semantic_probes`
    - Old commits: `a87ec81aa`, `641d35b5b`, `e8a334041`.
    - Old surfaces: `tools/mcp_semantic_probe.py`, `tools/mcp_tool.py`, `model_tools.py`, tests.
    - Why: MCP health checks beyond syntax/connection.
    - Merge mode: read-only probes, strict timeout, no server mutation.

### P2/P3 reflective systems and docs

13. `post_chat_reflection_and_run_envelope`
    - Old commits: `fc3b7a9b8`, `33b265b23`, `63e2d3928`, `4d14affc1`, `5a81c0a29`.
    - Old surfaces: `agent/post_chat_reflection.py`, `agent/run_controller.py`, `agent/run_envelope.py`, CLI/config/tests.
    - Merge mode: suggest-only first; no automatic memory/skill mutation without current bg-review safeguards.

14. `norm_governance_control_plane`
    - Old commits: `66da45f4e`, `5d191782f`, `0b69ef3f6`, `a15648e93`.
    - Old surfaces: `agent/norm_governance.py`, `agent/norm_write_guard.py`, `agent/norm_taskpool_gate.py`, `hermes_cli/norms.py`, docs/tests.
    - Merge mode: very careful. Current runtime already has Active Hermes Norms in prompt. Do not duplicate or create competing norm SSOT. First map to current runtime policy injection and memory/skill gates.

15. `browser_replay_evidence`
    - Old commits: `8c8c5e6e6`, `42ecfe7e9`.
    - Old surfaces: `tools/browser_tool.py`, `tools/website_policy.py`, browser replay tests/docs.
    - Merge mode: evidence persistence and navigation policy fail-closed; ensure no privacy leak or huge artifact bloat.

16. `reference_intake_docs_and_skills`
    - Old commits: multiple `docs/private/*gap-analysis*`, TradingAgents skill, Matt Pocock skill synthesis.
    - Merge mode: docs/skills can be restored selectively if still relevant, but must not pollute current overlay registry with stale claims.

## Proposed tranche order

- Tranche A — Safety gates: abstraction/file/tool gates, process kill lifecycle, tool guardrail halt guidance.
- Tranche B — Continuation reliability: active continuation cap, goal-bound handoff, auto-relay quota reset, kanban worker auto-relay.
- Tranche C — Provider/runtime recovery: Codex auxiliary salvage, cron legacy recovery, Gemini OAuth gap check.
- Tranche D — Observability/taskpool governance: kanban progress bridge, taskpool validation/security gates, skills health audit report-only.
- Tranche E — Efficiency/reflection: prompt cache layering, MCP semantic probes, browser replay evidence, post-chat reflection/run envelope.
- Tranche F — Norm governance/docs: norm control plane reconciliation and reference docs/skills selective restore.

## Multi-model certification packet intent

This plan should be sent as a **review-package-only** multi-model round, not immediate implementation authorization.

Questions for models:

1. Is the tranche order safe and complete given the old-only commit inventory?
2. Which capability family should be first after considering blast radius and current post-update health?
3. Which old capabilities should be retired instead of ported?
4. Which claims need more evidence before any code port?
5. What are the required tests/gates for Tranche A?

Acceptance:

- Does not recommend wholesale merge/cherry-pick of old branch.
- Preserves current upstream-aligned stable state.
- Uses scoped, reversible, default-off/fail-soft ports where appropriate.
- Keeps expensive or side-effectful multi-model/taskpool automation explicit and governed.
