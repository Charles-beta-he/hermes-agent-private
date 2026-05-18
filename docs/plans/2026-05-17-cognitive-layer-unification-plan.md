# Cognitive Layer Unification Implementation Plan

> For Hermes: use subagent-driven-development skill to implement this plan task-by-task.

Goal: turn Hermes's scattered cognition helpers into one explicit, testable, low-drift cognitive pipeline executed at the start of every turn.

Certification note:
- The original packet failed certification because claim/evidence links were missing and a normative architecture claim was incorrectly treated as a truth claim.
- A repaired packet then passed full 4-model certification at `/Users/charles/.hermes/tick-20260517-165806-759911-p45027-65a8c1e6-36affef1/` with `can_claim_complete_certification: true`.
- Certified factual scope: the cognition-oriented modules exist, TaskRouter/OpenClaw have tested or local integration, and several helpers are not wired into the canonical `run_agent.py` + `agent/conversation_loop.py` path.
- This implementation plan therefore proceeds from certified current-state facts, while the eventual mainline unification remains an architectural rollout decision rather than a separately truth-certified claim.

Architecture: introduce a typed `CognitiveState` plus `CognitivePipeline` in `agent/`, wire it into `agent/conversation_loop.py` as the single pre-model cognition seam, then migrate existing helpers behind stage adapters. Preserve current behavior with default-off or no-op adapters until each stage is verified.

Tech Stack: Python, existing Hermes agent runtime, OpenClaw optional helpers, pytest.

---

## Current State Summary
- `run_agent.AIAgent.run_conversation()` forwards to `agent/conversation_loop.py`
- `conversation_loop.py` is the canonical turn orchestrator and is the right landing seam
- `task_router` and `openclaw_control_plane` have partial integration
- `query_intent`, `pattern_retriever`, `anatomy_scanner`, `budget_meter` exist but are not a unified main-path cognition layer
- `token_compressor` and `result_spiller` represent context-pressure cognition and should be modeled as late-stage recommendations

## Target End State
Every turn produces a `CognitiveState` before the main model call. The rest of the runtime reads the state instead of performing disconnected one-off heuristics.

Example target object:
- `intent`: classified ask type / detail / salience / recency
- `route`: target tier + provider/model hints + activation hints
- `pattern`: reusable past approach summary if found
- `repo_context`: optional anatomy summary / high-level file map
- `budget`: budget allow/deny + downgrade hints
- `policy`: scoped policy context + memory gate flags
- `context_pressure`: compression/spill recommendations
- `observations`: stage timing, skip reasons, confidence

## Keep / Unify / Retire / Delay

### Keep and unify now
- `agent/task_router.py`
- `agent/openclaw_control_plane.py`
- `agent/query_intent.py`
- `agent/pattern_retriever.py`
- `agent/anatomy_scanner.py`
- `agent/budget_meter.py`
- `agent/token_compressor.py`
- `agent/result_spiller.py`

### Retire after migration if redundant
- Any ad-hoc per-turn heuristics in `conversation_loop.py` or `run_agent.py` that duplicate route / policy / budget logic
- Direct optional imports that become stage adapters inside the pipeline

### Delay until pipeline exists
- Any additional cognition overlays
- Any new budget heuristics not expressed through `CognitiveState`
- Deep policy rewriting in `prompt_builder.py`

---

## Phase 1: Create core contracts

### Task 1: Add typed cognitive state
Objective: define the canonical artifact produced once per turn.

Files:
- Create: `agent/cognitive_state.py`
- Test: `tests/agent/test_cognitive_state.py`

Steps:
1. Add dataclasses / TypedDicts for `IntentState`, `RouteState`, `PatternState`, `RepoContextState`, `BudgetState`, `PolicyState`, `ContextPressureState`, `CognitiveState`
2. Keep every field optional or defaultable so migration is additive
3. Add serializer helpers (`to_dict()`) for logging and tests
4. Test construction, defaults, and serialization

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_state.py`

### Task 2: Add pipeline stage protocol
Objective: define one contract for all cognition modules.

Files:
- Create: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Add `CognitiveStage` protocol/interface with `name` and `run(turn_input, state) -> state`
2. Add `TurnInput` container for user message, conversation history, repo/workdir, enabled toolsets, config flags
3. Add `CognitivePipeline.run()` that executes stages in order and records timing/skip reasons
4. Add a no-op default pipeline for compatibility

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py`

---

## Phase 2: Wrap existing modules as stages

### Task 3: Intent stage
Objective: wrap `query_intent` as the first cheap stage.

Files:
- Modify: `agent/query_intent.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Keep classifier logic intact
2. Add adapter `IntentStage` that writes into `state.intent`
3. Add skip rule for empty/non-text inputs
4. Add unit test proving state is populated

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py -k intent`

### Task 4: Route stage
Objective: wrap `task_router` and activation hints into `state.route`.

Files:
- Modify: `agent/task_router.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_openclaw_control_plane.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Add adapter `RouteStage`
2. Preserve current `TaskRouter.route()` behavior
3. Move optional activation hints into the stage output instead of ad-hoc dict mutation outside the pipeline
4. Add tests for route + activation hints together

Verification:
- `./venv/bin/pytest -q tests/agent/test_openclaw_control_plane.py tests/agent/test_cognitive_pipeline.py -k 'route or activation'`

### Task 5: Pattern recall stage
Objective: expose successful-pattern reuse through state rather than hidden helper calls.

Files:
- Modify: `agent/pattern_retriever.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Add adapter `PatternRecallStage`
2. Store summary-only pattern output in `state.pattern`
3. Bound runtime and result size
4. Add tests for hit/miss behavior

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py -k pattern`

### Task 6: Repository anatomy stage
Objective: use anatomy only when the turn implies codebase/repo work.

Files:
- Modify: `agent/anatomy_scanner.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Add adapter `RepoAnatomyStage`
2. Trigger only when workdir/repo context exists and intent suggests code work
3. Emit a summary / top paths, not full scans by default
4. Add tests for conditional enablement

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py -k anatomy`

### Task 7: Budget stage
Objective: turn `budget_meter` into an explicit gate/hint stage.

Files:
- Modify: `agent/budget_meter.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Add adapter `BudgetStage`
2. Populate `state.budget` with allow/deny + downgrade recommendation
3. Do not silently hard-fail the turn in v1; emit guidance first
4. Add tests for below-limit and above-limit cases

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py -k budget`

### Task 8: Policy/context-pressure stages
Objective: normalize OpenClaw policy helpers and context-pressure helpers into state.

Files:
- Modify: `agent/openclaw_control_plane.py`
- Modify: `agent/token_compressor.py`
- Modify: `agent/result_spiller.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/agent/test_openclaw_control_plane.py`
- Test: `tests/agent/test_result_spill_retrieval.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Add `PolicyStage` for scoped policy context / memory gate flags / activation hints metadata
2. Add `ContextPressureStage` for compression/spill recommendations only
3. Keep all heavy or optional behavior fail-soft/default-off
4. Add tests proving stage output is advisory and bounded

Verification:
- `./venv/bin/pytest -q tests/agent/test_openclaw_control_plane.py tests/agent/test_result_spill_retrieval.py tests/agent/test_cognitive_pipeline.py -k 'policy or spill or compression'`

---

## Phase 3: Wire pipeline into the main turn loop

### Task 9: Add pipeline creation to agent bootstrap
Objective: make the pipeline available on every `AIAgent` instance.

Files:
- Modify: `run_agent.py`
- Modify: `agent/cognitive_pipeline.py`
- Test: `tests/run_agent/test_run_agent.py`

Steps:
1. Instantiate a default pipeline on `AIAgent` construction
2. Keep feature flags so rollout can be staged
3. Add minimal constructor tests

Verification:
- `./venv/bin/pytest -q tests/run_agent/test_run_agent.py -k cognitive`

### Task 10: Execute pipeline once per turn in `conversation_loop.py`
Objective: establish the single cognition seam.
Status update (2026-05-18): completed for guarded v1. `agent/conversation_loop.py` now executes the pipeline once per turn at the certified seam, under a default-off gate, and degrades exceptions fail-soft.

Files:
- Modify: `agent/conversation_loop.py`
- Modify: `run_agent.py`
- Test: `tests/agent/test_cognitive_pipeline.py`
- Test: `tests/run_agent/test_run_agent.py`

Steps:
1. Build `TurnInput` near the top of `run_conversation()`
2. Run `agent.cognitive_pipeline.run(turn_input)` before main prompt/model execution
3. Store the result on the agent for downstream readers
4. Log stage timings and skip reasons
5. Ensure failures degrade to no-op state, not turn failure

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py tests/run_agent/test_run_agent.py -k 'cognitive or conversation'`
- completed with targeted guarded-wiring regression: `./venv/bin/pytest -q tests/agent/test_cognitive_state.py tests/agent/test_cognitive_pipeline.py tests/run_agent/test_run_agent.py -k 'cognitive_pipeline or cognitive_state'` -> `16 passed`

### Task 11: Consume `CognitiveState` instead of ad-hoc helpers
Objective: make downstream logic read a single artifact.
Status update (2026-05-18): completed as one ratchet-up tranche. Prompt/policy/memory/context-pressure consumers now read `agent.current_cognitive_state` first, with fail-soft fallback only when state is absent.

Files:
- Modify: `agent/conversation_loop.py`
- Modify: `agent/prompt_builder.py`
- Modify: `tools/memory_tool.py`
- Modify: `agent/cognitive_pipeline.py`
- Modify: `agent/tool_executor.py`
- Modify: `agent/agent_runtime_helpers.py`
- Test: `tests/tools/test_memory_tool_openclaw_gate.py`
- Test: `tests/agent/test_prompt_builder.py`
- Test: `tests/run_agent/test_run_agent.py`
- Test: `tests/agent/test_cognitive_pipeline.py`

Steps:
1. Route prompt-side advisory hints through `state.route`, `state.pattern`, `state.repo_context`, `state.budget`, and `state.context_pressure`
2. Inject scoped policy/memory guidance through `state.policy`
3. Use memory gate flags through `state.policy.memory_gate.enabled` rather than direct helper probing whenever state is present
4. Keep prompt changes bounded and API-call-time only so the cached session system prompt stays stable

Verification:
- `./venv/bin/pytest -q tests/tools/test_memory_tool_openclaw_gate.py tests/agent/test_prompt_builder.py tests/run_agent/test_run_agent.py tests/agent/test_cognitive_pipeline.py`

---

## Phase 4: Clean up drift and make it durable

### Task 12: Delete or deprecate orphan call patterns
Objective: remove architectural ambiguity after migration.
Status update (2026-05-18): completed for downstream consumers in scope. Direct built-in memory call paths were aligned to pass `current_cognitive_state`, and remaining OpenClaw helper usage is constrained to canonical pipeline stages or fail-soft fallback paths.

Files:
- Modify: `agent/cognitive_pipeline.py`
- Modify: `agent/conversation_loop.py`
- Modify: `agent/tool_executor.py`
- Modify: `agent/agent_runtime_helpers.py`
- Modify: `tools/memory_tool.py`
- Test: affected tests

Steps:
1. Remove split behavior between registry-vs-direct memory paths by passing `current_cognitive_state` through both
2. Leave module-level business logic intact, but move orchestration ownership to pipeline/state consumers
3. Keep default-off/fail-soft fallback behavior for absent state and disabled features

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py tests/tools/test_memory_tool_openclaw_gate.py tests/run_agent/test_run_agent.py`

### Task 13: Add regression and observability coverage
Objective: prevent the cognition layer from decaying back into overlays.
Status update (2026-05-18): completed using focused integration coverage on the canonical turn path plus advisory logging checks.

Files:
- Modify: `tests/agent/test_cognitive_pipeline.py`
- Modify: `tests/agent/test_prompt_builder.py`
- Modify: `tests/tools/test_memory_tool_openclaw_gate.py`
- Modify: `tests/run_agent/test_run_agent.py`
- Modify: `agent/conversation_loop.py`

Steps:
1. Add end-to-end coverage proving cognitive state is stashed, injected into the prompt, and logged when context pressure is present
2. Add policy-stage coverage proving `memory_gate.enabled` is emitted even without a concrete candidate
3. Keep observability minimal but explicit: advisory log line + prompt-side evidence + fail-soft behavior checks
4. Re-run the full related suite instead of only the new tests

Verification:
- `./venv/bin/pytest -q tests/agent/test_cognitive_pipeline.py tests/agent/test_prompt_builder.py tests/tools/test_memory_tool_openclaw_gate.py tests/run_agent/test_run_agent.py` -> `473 passed, 1 skipped`

---

## Acceptance Criteria
- `conversation_loop.py` executes exactly one canonical cognitive pipeline per turn
- `CognitiveState` exists and is the only supported pre-model cognition artifact
- `task_router`, `query_intent`, `pattern_retriever`, `anatomy_scanner`, `budget_meter`, `openclaw_control_plane`, `token_compressor`, `result_spiller` are either stage-backed or explicitly deferred
- No stage failure hard-crashes a turn; the pipeline degrades gracefully
- OpenClaw-specific behavior remains optional/default-off unless explicitly enabled
- Tests exist for stage unit behavior and at least one pipeline integration flow

## Risks
- Hidden duplication in current turn setup may conflict with pipeline output
- Over-eager anatomy/pattern/budget stages may add latency if not bounded
- Policy injection must stay minimal to avoid prompt bloat
- Budget stage must advise before it denies, or it will become a user-facing regression

## Recommended commit sequence
1. `feat(agent): add cognitive state and pipeline contracts`
2. `feat(agent): wrap intent route pattern anatomy budget policy stages`
3. `feat(agent): execute cognitive pipeline in conversation loop`
4. `refactor(agent): route prompt and memory gates through cognitive state`
5. `test(agent): add cognitive pipeline integration coverage`
6. `docs(adr): record cognitive layer unification decision`

## Resume Protocol
- Plan file: `.hermes/plans/2026-05-17-cognitive-layer-unification.md`
- ADR: `docs/adr/ADR-001-cognitive-layer-unification.md`
- This implementation plan: `docs/plans/2026-05-17-cognitive-layer-unification-plan.md`
- Guarded mainline seam landed in: `agent/conversation_loop.py`
- Next implementation seam if continuing: certification/documentation follow-up only; downstream consumers already read `agent.current_cognitive_state` in the canonical path.
