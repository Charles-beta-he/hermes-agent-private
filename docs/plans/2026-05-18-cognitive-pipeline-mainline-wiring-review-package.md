# Cognitive Pipeline Mainline Wiring Review Package

Date: 2026-05-18
Status: certified_and_landed
Prerequisite certification baseline:
- Certified factual packet: `/Users/charles/.hermes/tick-20260517-165806-759911-p45027-65a8c1e6-36affef1/`
- Scope certified as fact: (C1) relevant cognition modules exist; (C2) TaskRouter/OpenClaw have tested/local integration and several helpers are absent from canonical main path.
- Non-fact design conclusion: unified cognitive pipeline remains an architectural rollout decision, not a truth-certified repository fact.

Implementation update (2026-05-18):
- Round 1 mainline-wiring review packet (`/Users/charles/.hermes/tmp/cognitive-mainline-wiring-cert-round1.json`) was not certified because it mixed review-package ratification with implementation authorization.
- Round 2 review-package-only packet (`/Users/charles/.hermes/tmp/cognitive-mainline-wiring-cert-round2.json`) was fully certified at `/Users/charles/.hermes/tick-20260517-173316-786371-p67743-14a2d190-8ed60fa0/`.
- Guarded v1 wiring is now landed in `agent/agent_init.py` + `agent/conversation_loop.py` with default-off, advisory-only, fail-soft behavior.
- Targeted regression suite passed: `./venv/bin/pytest -q tests/agent/test_cognitive_state.py tests/agent/test_cognitive_pipeline.py tests/run_agent/test_run_agent.py -k 'cognitive_pipeline or cognitive_state'` -> `16 passed`.

## Goal
Prepare a separately reviewable, low-ambiguity package for deciding whether and how to wire `agent.cognitive_pipeline.CognitivePipeline` into the real Hermes per-turn main path.

This package originally did not perform the wiring. It defined the exact seam, rollout constraints, certifiable claims, non-goals, and verification plan for the next phase.

That next phase is now complete for guarded v1 mainline wiring: the review package was separately certified, then implemented under a top-level gate.

## Current landed baseline
Already landed off-mainline:
- `agent/cognitive_state.py`
- `agent/cognitive_pipeline.py`
- `tests/agent/test_cognitive_state.py`
- `tests/agent/test_cognitive_pipeline.py`

Already verified:
- non-mainline contract layer is additive
- default stages are fail-soft
- no current `run_agent.py` / `agent/conversation_loop.py` path depends on this pipeline yet

## Mainline seam proposal

### Canonical entrypoint
Current canonical turn entrypoint remains:
- `run_agent.AIAgent.run_conversation()` -> `agent/conversation_loop.py:run_conversation()`

### Recommended insertion point
Primary insertion point:
- `agent/conversation_loop.py`, after:
  - task/session/bootstrap reset
  - message history hydration
  - user message append (`messages.append(user_msg)`)
  - system prompt availability (`active_system_prompt` ready)
  - optional preflight compression
  - plugin `pre_llm_call` context collection
- and before:
  - first actual LLM request inside the main loop

Most concrete current seam window:
- user message append: `agent/conversation_loop.py:294-298`
- system prompt ready: `:354`
- preflight compression block: `:356-423`
- plugin context injection setup: `:424-458`
- main loop start: `:460`

Recommended hook position for v1 wiring:
- immediately after plugin/user-context preparation and immediately before the main conversation loop begins
- rationale: by this point the turn has final user input, hydrated history, chosen tool availability, session/runtime state, and post-compression message context; but no model-side behavior has executed yet

## Why this seam is preferred
1. It is truly once-per-turn.
2. It is upstream of all model/tool iterations.
3. It avoids mutating constructor/bootstrap behavior in `agent_init.py`.
4. It preserves the current cached system-prompt model; pipeline outputs can initially remain runtime-only rather than forcing prompt-cache invalidation.
5. It allows a feature-flagged no-op fallback: if pipeline execution fails, the existing loop can continue unchanged.

## Why not wire in `agent_init.py`
Rejected for v1 because:
- `agent_init.py` is instance/bootstrap time, not per-turn time.
- many stage inputs are turn-scoped (`user_message`, current `conversation_history`, workdir metadata, memory candidate, plugin context).
- wiring there would tempt persistent mutable state coupling rather than a clean per-turn artifact.

## Proposed v1 wiring contract

### New runtime attributes allowed on `AIAgent`
Advisory only for v1:
- `agent.cognitive_pipeline`
- `agent.cognitive_pipeline_enabled`
- `agent.current_cognitive_state`
- `agent.last_cognitive_state`

### TurnInput source mapping
- `user_message` <- current effective user message
- `conversation_history` <- current `messages` snapshot before first model call
- `workdir` <- best available cwd/project context from agent state if present
- `enabled_toolsets` <- derived from enabled tools / toolsets
- `config_flags` <- rollout flag + stage toggles
- `metadata` <- bounded v1 metadata only; no bulky payloads

### Initial consumer scope for v1
Allowed consumers:
- logging / observability
- advisory routing metadata
- advisory policy context
- advisory context-pressure recommendations

Not allowed in v1:
- silent mutation of persisted conversation history structure
- forced model/provider switch without explicit guarded logic
- forced memory writes
- forced compression/spill side effects
- hidden changes to tool availability

## Gating / rollout plan

### Required feature flag
Implemented top-level gate:
- config key: `agent.cognitive_pipeline.enabled`
- env override: `HERMES_COGNITIVE_PIPELINE_ENABLED=1`

Implemented behavior:
- default off
- fail-soft: exceptions disable pipeline for that turn, not the conversation
- advisory-only state stashing on `agent.current_cognitive_state` / `agent.last_cognitive_state`

### Optional stage toggles
Only after the top-level gate exists:
- `intent`
- `route`
- `pattern`
- `repo_anatomy`
- `budget`
- `policy`
- `context_pressure`

### Kill switch
Mandatory before rollout approval:
- one config/env switch that bypasses pipeline creation and execution entirely without code rollback

## Review claims for the next certification round
These are the right claims for a future mainline-wiring review packet.

### Factual claims
M1. `agent/conversation_loop.py` has a stable once-per-turn seam before the first model call where `TurnInput` can be assembled.
M2. Mainline wiring can be made fail-soft and default-off without changing behavior when the feature flag is disabled.
M3. v1 wiring can keep pipeline outputs advisory-only, avoiding forced provider/model/tool/memory side effects.

### Normative / design claims
D1. The above seam is the best rollout point relative to `agent_init.py` or later intra-loop hooks.
D2. A top-level kill switch plus advisory-only consumption is the safest first mainline rollout shape.

Note: D1/D2 should be reviewed as design conclusions, not shoved through the fact truth gate unchanged.

## Completed code changes for the guarded v1 implementation
1. `agent/agent_init.py`
   - added `agent.cognitive_pipeline_enabled`
   - added `agent.cognitive_pipeline_advisory_only`
   - added `agent.current_cognitive_state` / `agent.last_cognitive_state`
   - added lazy guarded default-pipeline construction behind `agent.cognitive_pipeline.enabled` and env override
2. `agent/conversation_loop.py`
   - added a once-per-turn helper to assemble `TurnInput`
   - executes the pipeline under the top-level gate at the certified seam
   - stashes result on the agent for downstream read-only consumers
   - degrades exceptions to fail-soft no-op behavior
3. `tests/run_agent/test_run_agent.py`
   - added constructor-gate tests and mainline wiring behavior tests
4. `run_agent.py`
   - no heavy logic required; existing forwarder path remained intact

## Verification matrix

### Must-pass before enabling by default
- `./venv/bin/pytest -q tests/agent/test_cognitive_state.py tests/agent/test_cognitive_pipeline.py tests/run_agent/test_run_agent.py -k 'cognitive_pipeline or cognitive_state'` -> passed (`16 passed`)

### Required new tests
1. flag disabled -> no pipeline execution, behavior unchanged
2. flag enabled -> pipeline runs once per turn before first model call
3. pipeline exception -> turn continues, fail-soft no-op
4. constructor default-off / env override gate wiring

### Recommended observational checks
- verify system prompt cache path is unchanged
- verify no duplicate user-message injection
- verify no unbounded metadata copied into messages
- verify no new file/system side effects when all stages remain advisory

## Risks and blockers

### High-risk if done carelessly
- mutating provider/model selection before existing fallback logic understands the new source of truth
- letting policy stage rewrite prompts directly in v1
- coupling pipeline state to persisted message history and inflating context
- making budget/context-pressure stages enforce side effects too early

### Remaining blocker categories
- None for guarded v1 seam landing.
- Follow-on work, if desired, is downstream consumption/observability rather than seam certification.

## Recommended review outcome options
1. Approved package as the design baseline for implementation
2. Implement guarded v1 seam behind a default-off kill switch
3. Keep downstream consumers advisory-only until later phases are verified

## Suggested next action
- Continue with Task 11+ only if you want downstream prompt/policy/memory consumers to read `CognitiveState`.
- Otherwise keep the current state: certified mainline seam, default-off gate, advisory-only runtime artifact.
