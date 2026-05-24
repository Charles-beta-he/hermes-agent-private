---
name: tradingagents-reference-patterns
description: "Use when adapting lessons from TauricResearch/TradingAgents into Hermes multi-agent workflows: typed role graphs, quick/deep model routing, bounded adversarial debate, role-scoped tools, structured decision handoffs, resumable checkpoints, and compact outcome reflection."
---

# TradingAgents reference patterns

Use this skill when the user asks to analyze TradingAgents, improve Hermes from TradingAgents, or design a multi-agent workflow with analyst/researcher/debater/manager roles.

Source boundary:
- Reference repo: https://github.com/TauricResearch/TradingAgents
- Last source-verified commit for this skill: `61522e103e61601c553b4544abcd53fa7ebf9f1d`
- Treat TradingAgents as reference architecture only. Do not add it as a Hermes runtime dependency unless explicitly requested.

## Patterns to reuse in Hermes

1. Typed role graph before prompt writing
   - Define the workflow state fields and role handoff fields first.
   - TradingAgents evidence: `AgentState`, `InvestDebateState`, and `RiskDebateState` in `tradingagents/agents/utils/agent_states.py`.
   - Hermes rule: new multi-agent plans should name state fields, owners, and transitions before spawning subagents.

2. Quick/deep model split
   - Use cheaper/faster models for broad evidence collection and expensive/strong models for synthesis or arbitration.
   - TradingAgents evidence: `TradingAgentsGraph` constructs `quick_thinking_llm` and `deep_thinking_llm`; `GraphSetup` uses deep models for Research/Portfolio Manager nodes.
   - Hermes rule: explicitly label producer, reviewer, and arbiter tiers in taskpool/multi-model plans.

3. Bounded adversarial debate plus manager node
   - Debate is useful only if capped and routed to a final owner.
   - TradingAgents evidence: `ConditionalLogic.should_continue_debate()` and `should_continue_risk_analysis()` route to manager nodes after fixed round counts.
   - Hermes rule: every bull/bear, pro/con, or multi-model disagreement loop needs `max_rounds`, a stop condition, and a named arbiter.

4. Role-scoped tools
   - Give each role only the data/tool surfaces it needs.
   - TradingAgents evidence: `_create_tool_nodes()` maps market, social/sentiment, news, and fundamentals analysts to separate tool lists.
   - Hermes rule: delegation prompts should include minimal `enabled_toolsets`; do not inherit all tools by default for reference-analysis or review agents.

5. Structured handoffs with fail-soft provider fallback
   - Use schema-backed outputs at decision boundaries, then render to human-readable markdown if needed.
   - TradingAgents evidence: `ResearchPlan`, `TraderProposal`, `PortfolioDecision` Pydantic schemas plus `invoke_structured_or_freetext()` fallback.
   - Hermes rule: if fallback output cannot be parsed/verified, fail closed instead of silently accepting free text.

6. Deferred outcome reflection
   - Store pending decisions/actions, resolve outcomes later, and reflect compactly.
   - TradingAgents evidence: `TradingMemoryLog` appends pending entries, later atomically resolves return/alpha/reflection; `Reflector` asks for 2-4 terse sentences.
   - Hermes rule: only durable lessons become skills/references; never save WIP or per-run status to memory.

7. Deterministic resumability
   - Key checkpoints by stable work identity and keep storage scope narrow.
   - TradingAgents evidence: per-ticker SQLite checkpoint DBs and deterministic `thread_id(ticker, date)`.
   - Hermes rule: taskpool/continuation checkpoints should be keyed by task/run ID and be auditable without guessing.

8. Config/env manifest
   - Centralize env-var to config-key mapping and coerce types from defaults.
   - TradingAgents evidence: `_ENV_OVERRIDES` in `default_config.py`.
   - Hermes rule: new environment knobs need a registry entry and tests, not scattered `os.getenv()` calls.

9. Role-level observability
   - Track runtime by role specification.
   - TradingAgents evidence: `AnalystExecutionPlan` and `AnalystWallTimeTracker`.
   - Hermes rule: multi-agent summaries should expose per-role timing/bottlenecks when practical.

## Recommended Hermes design checklist

Before implementing a TradingAgents-inspired workflow in Hermes:

1. Write the state schema: fields, owner role, transition source, verification method.
2. Write the role manifest: role name, model tier, toolsets, expected artifact, timeout/round cap.
3. Separate evidence producers from synthesis/arbiter roles.
4. Add a bounded debate loop only if disagreement materially improves the decision.
5. Make final decisions structured; parse or validate them before state transition.
6. Store compact lessons in a skill/reference only after outcome evidence exists.
7. Use stable task IDs for checkpoints and logs.
8. Verify with deterministic commands: parse schemas, inspect generated artifacts, run focused tests.

## Pitfalls

- Do not copy the finance-specific prompts or trading advice policy into Hermes global behavior.
- Do not claim LangGraph feature parity unless Hermes has actually implemented the corresponding graph/checkpoint runtime.
- Do not let fallback free text bypass proof gates.
- Do not promote one run's outcome into memory; durable procedures belong in skills.
