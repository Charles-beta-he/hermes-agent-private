# TradingAgents reference analysis for Hermes self-improvement

Source: https://github.com/TauricResearch/TradingAgents
Pinned commit inspected: `61522e103e61601c553b4544abcd53fa7ebf9f1d` (`fix(llm): skip Anthropic effort kwarg on non-supporting models (#831)`).
Boundary: reference architecture only. No runtime dependency on TradingAgents is proposed or claimed.

## Source-verified patterns

1. Role graph with explicit typed state
   - Evidence: `tradingagents/graph/setup.py` builds a LangGraph `StateGraph(AgentState)` with analyst nodes, bull/bear debate, trader, risk debaters, and portfolio manager.
   - Evidence: `tradingagents/agents/utils/agent_states.py` defines `AgentState`, `InvestDebateState`, and `RiskDebateState` as typed state fields rather than implicit prose-only handoffs.

2. Two-speed model routing inside one workflow
   - Evidence: `tradingagents/graph/trading_graph.py` constructs `deep_thinking_llm` and `quick_thinking_llm`; graph setup sends quick models to analysts/trader/debaters and deep models to research/portfolio managers.
   - Hermes analogue: keep cheap/fast agents for broad evidence gathering; reserve P10/arbiter-grade models for synthesis, ratification, and irreversible actions.

3. Bounded adversarial debate followed by a manager node
   - Evidence: `tradingagents/graph/conditional_logic.py` stops bull/bear debate after `2 * max_debate_rounds` and risk debate after `3 * max_risk_discuss_rounds`, then routes to `Research Manager` / `Portfolio Manager`.
   - Hermes analogue: every multi-model disagreement loop needs an explicit round cap plus a manager/arbiter node; do not let advisory debate become an unbounded tool loop.

4. Tool-scope segregation by role
   - Evidence: `TradingAgentsGraph._create_tool_nodes()` maps market/social/news/fundamentals analysts to separate `ToolNode` tool lists.
   - Hermes analogue: subagent/toolset selection should be role-scoped and evidence-scoped; avoid giving all tools to all subagents by default when a smaller toolset proves enough.

5. Structured output at decision boundaries with graceful fallback
   - Evidence: `tradingagents/agents/schemas.py` defines Pydantic schemas for `ResearchPlan`, `TraderProposal`, and `PortfolioDecision`.
   - Evidence: `tradingagents/agents/utils/structured.py` binds `with_structured_output()` when available and falls back to free text on unsupported providers or malformed output.
   - Hermes analogue: use schemas for handoff/ratification artifacts, but preserve a free-text fallback only when downstream can still parse/verify the result fail-closed.

6. Deferred outcome reflection and compact reusable lessons
   - Evidence: `tradingagents/agents/utils/memory.py` appends pending decisions, later resolves outcome fields atomically, and injects same-ticker plus cross-ticker lessons.
   - Evidence: `tradingagents/graph/reflection.py` restricts reflection to exactly 2-4 terse sentences, designed for future prompt reuse.
   - Hermes analogue: completed task retrospectives should be compact, evidence-linked, and promoted only into skill/reference files when still durable; avoid stale WIP memory ghosts.

7. Resumability keyed by deterministic work identity
   - Evidence: `tradingagents/graph/checkpointer.py` uses per-ticker SQLite checkpoint DBs and a deterministic `thread_id(ticker, date)` hash.
   - Hermes analogue: long-running taskpool/agent runs should key checkpoints by stable task identity and keep checkpoint scope narrow to avoid cross-task contention.

8. Declarative config/env override registry
   - Evidence: `tradingagents/default_config.py` keeps `_ENV_OVERRIDES` as the single env-var to config-key mapping and coerces values from existing defaults.
   - Hermes analogue: route env/config expansion through a manifest rather than ad-hoc reads scattered through entry points.

9. Wall-time observability tied to plan specs
   - Evidence: `tradingagents/graph/analyst_execution.py` defines `AnalystNodeSpec`, `AnalystExecutionPlan`, and `AnalystWallTimeTracker`.
   - Hermes analogue: taskpool and multi-agent runs should track per-role elapsed time from the role manifest, not scrape logs after the fact.

## Gap matrix for Hermes

| TradingAgents pattern | Hermes gap / risk | Local improvement artifact | Acceptance criterion |
| --- | --- | --- | --- |
| Typed graph state | Hermes has rich taskpool state but many ad-hoc prose handoffs remain | Prefer schema-backed continuation/ratification packets for new multi-agent workflows | New workflows define state fields before prompts/tool calls |
| Quick/deep split | Hermes has model routing, but prompt authors can still blur evidence gathering vs arbitration | Add skill guidance: broad cheap collection, expensive synthesis only at gates | Plans name producer/reviewer/arbiter model tiers explicitly |
| Bounded debate | Multi-model certification can loop or overrun if stop conditions drift | Round caps plus manager node required for disagreement loops | Every debate loop has max rounds and final owner |
| Role tool scopes | Subagents may inherit too many tools | Role manifest lists enabled_toolsets per subagent | Delegation prompts include minimal toolsets and evidence contract |
| Structured decisions + fallback | Some artifacts are natural language only | Use schema for handoff/decision artifacts; fallback must still be parseable or fail closed | Verification parses artifact or rejects it |
| Outcome reflection | Memory/skills can capture stale details if not curated | Store only compact durable lessons in skills, never task progress in memory | Retrospective asks “will this still matter in 10 sessions?” |
| Deterministic checkpoint identity | Long runs can be hard to resume/audit | Stable task/run IDs and narrow checkpoint files | Run can be resumed or audited from ID without guessing |
| Env override manifest | Config can drift across scripts | Centralized config/env registry for new knobs | Adding a config knob requires one manifest entry + test |
| Role wall-time | Runtime bottlenecks not always visible by role | Per-role timing in taskpool/multi-agent summaries | Summary includes role-level duration where applicable |

## Non-claims

- Hermes should not import TradingAgents or LangGraph as a dependency from this analysis alone.
- TradingAgents financial prompts/tools are domain-specific and should not become global Hermes policy.
- TradingAgents memory log is not a replacement for Hermes session DB, taskpool evidence, skills, or governed memory hygiene.

## Applied self-improvement

Created skill: `tradingagents-reference-patterns`.
Use it when adapting external multi-agent research frameworks into Hermes design, especially for role graph state, quick/deep model routing, bounded adversarial debate, structured handoffs, resumability, and compact outcome reflection.
