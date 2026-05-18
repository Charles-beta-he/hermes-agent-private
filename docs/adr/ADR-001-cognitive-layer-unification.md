# ADR-001: Unify Hermes cognition helpers behind a single cognitive pipeline

Date: 2026-05-17
Status: accepted

## Context
Hermes currently contains several cognition-oriented modules, but they are not operating as a single architecture layer.

Evidence status note:
- A first multi-model packet ended `not_certified` because claim/evidence links were missing and one claim mixed factual and normative content.
- A repaired certification packet then passed full 4-model ratification at `/Users/charles/.hermes/tick-20260517-165806-759911-p45027-65a8c1e6-36affef1/`.
- The certified factual scope is intentionally narrow: C1 (the relevant cognition modules exist in the repo) and C2 (TaskRouter/OpenClaw have tested or local integration while several other helpers are absent from the canonical `run_agent.py` + `agent/conversation_loop.py` path).
- The long-term unification direction in this ADR remains a design conclusion derived from those certified facts, not itself a truth-gated repository fact.
- A separate mainline-wiring review-package ratification round was then certified at `/Users/charles/.hermes/tick-20260517-173316-786371-p67743-14a2d190-8ed60fa0/`, after reframing the packet as no-action review-package certification.
- Guarded v1 seam landing is now implemented in `agent/agent_init.py` + `agent/conversation_loop.py` behind a default-off feature gate and env kill switch.

Observed components:
- `agent/task_router.py` — complexity/tier routing, with optional OpenClaw activation hints
- `agent/openclaw_control_plane.py` — activation hints, scoped policy context, memory write gate, benchmark/self-check helpers
- `agent/query_intent.py` — regex intent classifier
- `agent/pattern_retriever.py` — historical pattern retrieval
- `agent/anatomy_scanner.py` — repository anatomy / token estimation
- `agent/budget_meter.py` — per-session budget gate
- `agent/token_compressor.py` + `agent/result_spiller.py` — context pressure mitigation

Current execution seam:
- `run_agent.AIAgent.run_conversation()` forwards into `agent/conversation_loop.py`
- `agent/conversation_loop.py` is the canonical per-turn orchestration seam

Current architectural problem:
1. The codebase has cognition components, not a unified cognition layer.
2. `task_router` + OpenClaw helpers show partial integration, but several other modules appear isolated or test/self-demo only.
3. Policy, routing, retrieval, budget, and context-pressure logic are distributed across unrelated helpers rather than executed as an explicit pre-model pipeline.
4. This makes future evolution expensive: each new cognition behavior becomes another overlay instead of a contract-bound stage.

Multi-model review outcome:
- Real planner fan-out completed across `gpt-5.5`, `mimo-v2.5-pro`, `claude-opus-4-7`, `deepseek-v4-pro`
- Consensus direction: do not keep shipping isolated helpers; converge them behind a unified cognitive pipeline
- Caution from the review: the current evidence packet had verifier/linking gaps, so the safe immediate landing is architecture codification plus an implementation plan, not a speculative big-bang refactor in the same turn

## Decision
Propose a first-class `CognitivePipeline` architecture as the preferred long-term landing direction, and treat it as the candidate sole entry point for pre-model cognition decisions pending later evidence-link review / implementation validation.

The pipeline will run once per user turn before tool/model execution and emit a structured `CognitiveState` consumed by the rest of the agent.

### Stage model
1. `intent` — classify user ask (`query_intent`)
2. `complexity_route` — choose tier/provider/model hints (`task_router`)
3. `pattern_recall` — look for reusable successful prior patterns (`pattern_retriever`)
4. `repo_anatomy` — optionally summarize target repo shape when code/repo work is implied (`anatomy_scanner`)
5. `budget_gate` — compute affordability / downgrade hints (`budget_meter`)
6. `policy_context` — attach scoped policy / activation hints / memory gate config (`openclaw_control_plane`)
7. `context_pressure` — attach compression / spilling recommendations (`token_compressor`, `result_spiller`)

The output of these stages becomes a typed turn artifact, e.g. `CognitiveState`, rather than ad-hoc dict fields scattered across modules.

## Alternatives Considered
| Option | Pros | Cons | Why Rejected |
|--------|------|------|-------------|
| Keep modules separate and wire ad hoc | Low short-term effort | More overlays, no contract, drift risk, duplicated heuristics | Fails the long-term once-for-all architecture goal |
| Big-bang immediate code refactor with no design landing | Fast visible change | High blast radius, hard to verify, poor rollback | Unsafe given current verifier/link-review gap |
| Replace everything with OpenClaw control plane | Reuses an existing local enhancement | Over-couples Hermes to one local overlay family | Hermes needs a native core contract, not overlay-first architecture |
| Introduce a unified cognitive pipeline + staged migration | Durable architecture, explicit contracts, gradual rollout, testable | Requires initial design and migration work | Chosen |

## Consequences
### Positive
- One canonical pre-model cognition seam
- Existing helpers become composable stages instead of orphan modules
- Easier testing: stage contracts can be unit-tested independently and integrated end-to-end
- Easier retirement: once a capability is upstream/core, overlay glue can be deleted cleanly
- Future multi-model / policy / budget / retrieval improvements get a clear landing zone

### Negative / Costs
- Requires new state schema and stage interfaces
- Some current modules will need signature tightening and reduced side effects
- Requires migration work in `agent/conversation_loop.py` and possibly `run_agent.AIAgent` initialization
- Some currently orphaned modules may be retired if they do not justify pipeline inclusion after stage trials

### Non-goals
- No attempt to redesign tool execution, memory persistence, or provider adapters in this ADR
- No big-bang behavioral switch in the same commit as the design landing
- No mandatory enablement of all stages for all turns; several stages remain conditional/cheap-by-default
