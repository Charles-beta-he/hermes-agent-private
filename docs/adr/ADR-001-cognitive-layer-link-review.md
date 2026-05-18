# Cognitive Layer Link Review

Date: 2026-05-18
Status: evidence-linked, then ratified
Related ticks:
- initial failed packet: /Users/charles/.hermes/tick-20260517-154738-452106-p25855-9a3f6b50-492c1a1e
- repaired certified packet: /Users/charles/.hermes/tick-20260517-165806-759911-p45027-65a8c1e6-36affef1
Related claims: C1, C2, C3

## Purpose
This note resolves the missing claim/evidence links that blocked truth certification in the multi-model discussion packet. It does not rewrite the original packet in place; instead it provides a reviewer-readable mapping from claims to concrete repo evidence.

## Claim review

### C1
Claim:
"The repo contains multiple cognition-oriented modules: task_router, query_intent, pattern_retriever, anatomy_scanner, budget_meter, openclaw_control_plane, token_compressor/result_spiller."

Disposition: supported by code evidence.

Evidence links:
- E-001 task_router exists: `agent/task_router.py:100` defines `class TaskRouter`.
- E-002 query_intent exists: `agent/query_intent.py:85` defines `def classify_intent`.
- E-003 pattern_retriever exists: `agent/pattern_retriever.py:30` defines `class PatternRetriever`.
- E-004 anatomy_scanner exists: `agent/anatomy_scanner.py:61` defines `class AnatomyScanner`.
- E-005 budget_meter exists: `agent/budget_meter.py:45` defines `class BudgetMeter`.
- E-006 openclaw activation helper exists: `agent/openclaw_control_plane.py:156` defines `activation_hints_for_text`.
- Supplemental evidence for token compression / spilling:
  - `agent/token_compressor.py:163` defines `compress_tool_output`.
  - `agent/token_compressor.py:187-200` instantiates `ResultSpiller` and spills large tool output.
  - `agent/result_spiller.py:30` defines `class ResultSpiller`.

Reviewer note:
C1 is a plain repository-fact claim and is directly supported by symbol definitions.

### C2
Claim:
"TaskRouter and OpenClaw activation hints have at least some live/tested integration, but several other cognition modules are not wired into the main execution path."

Disposition: partially supported, with one narrowed wording adjustment recommended.

Supported part A: TaskRouter/OpenClaw have tested integration.
- `agent/task_router.py:137-145` shows `TaskRouter.route()` conditionally calling `activation_hints_for_text(text)` and attaching `activation_hints` to the routing result.
- `tests/agent/test_openclaw_control_plane.py:76-80` verifies that enabling `openclaw_activation_hints` causes `router.route(...)` to return `activation_hints`.
- `agent/openclaw_control_plane.py:156-183` defines the advisory activation-hints function used by TaskRouter.

Supported part B: at least some cognition helpers are integrated outside their own module.
- `agent/token_compressor.py:187-200` calls `ResultSpiller().spill(...)` for large outputs.
- `tests/agent/test_result_spill_retrieval.py:11-29` and `:32-68` verify spill + retrieve continuity.

Supported part C: several modules are not wired into the main execution path.
- `run_agent.py` has no references to `TaskRouter`, `classify_intent`, `PatternRetriever`, `AnatomyScanner`, `BudgetMeter`, `activation_hints_for_text`, `render_scoped_policy_context`, `memory_write_gate`, `ResultSpiller`, or `compress_tool_output` by direct repo search.
- `agent/conversation_loop.py` also has no references to those symbols by direct repo search.
- `query_intent.py`, `pattern_retriever.py`, `anatomy_scanner.py`, and `budget_meter.py` each define standalone helpers, but repo-wide search found no current mainline callers from `run_agent.py` or `agent/conversation_loop.py`.

Recommended narrowing:
- Prefer: "TaskRouter and OpenClaw activation hints have tested/local integration, while several other cognition helpers are not wired into the canonical run_agent/conversation_loop path."
- Avoid stronger wording like "live integration in the main path" for TaskRouter until a mainline caller is demonstrated.

### C3
Claim:
"A durable long-term landing plan should converge these modules behind a unified cognitive pipeline rather than keep them as isolated overlays/helpers."

Disposition: design recommendation, not a truth-certifiable repository fact.

Evidence basis:
- C1 shows the modules exist and are scattered across separate helpers.
- C2 shows mixed integration status: a few tested/local links exist, but canonical main-path wiring is missing.
- `agent/cognitive_pipeline.py` and `agent/cognitive_state.py` now provide a non-mainline contract layer proving that a unified stage model can exist without breaking current behavior.

Reviewer note:
C3 should be treated as an ADR/design conclusion derived from C1+C2, not as a factual truth claim expected to pass the same evidence gate as symbol-existence claims.

## Suggested reclassification for certification
- C1: can be upgraded to verified_by_evidence.
- C2: can be upgraded to verified_by_evidence if wording is narrowed from "live/tested integration" to "tested/local integration" and the main-path absence is explicitly scoped to `run_agent.py` + `agent/conversation_loop.py`.
- C3: should be moved out of truth-gated factual claims and into design-rationale / recommendation language.

## Bottom line
The original packet likely failed less because the repo evidence was absent, and more because:
1. evidence IDs were never explicitly linked to claims;
2. C2 overstated TaskRouter integration strength; and
3. C3 is normative architecture guidance, not a pure truth claim.
