# Agent harness worker-bus architecture intake (2026-06-03)

## Source identity

- Source type: user-provided video summary
- Publisher: `精读AI`
- Interpreted subject: iii.dev founder Mike Piccolo's view on AI Agent framework design
- Canonical URL: not provided; live web searches for `Mike Piccolo iii.dev agent harness engine bus` did not find a stable primary source during intake
- Evidence status: **secondary/user-provided summary**; future task must verify primary transcript/video/article before implementation

## Core thesis from the supplied summary

Mike Piccolo argues that mainstream AI Agent frameworks such as LangChain have a structural flaw: they package many independent production concerns into one framework. The result is early convenience but late-stage lock-in: once a production system needs to adjust only one concern, the monolith makes targeted replacement difficult.

Proposed solution: split the agent harness into independent workers connected through an `engine bus`. A system can move from a thin harness (about 4 workers) to a thick harness (13+ workers) by changing configuration and composition rather than rewriting or being locked into a single framework.

## The 15 production-grade agent harness tasks

1. Receive and persist frontend requests
2. Resolve model credentials
3. Check model capabilities: vision, streaming, context window
4. Drive the conversation state machine
5. Load and provide skill descriptions
6. Assemble the system prompt
7. Stream tokens
8. Run policy checks before tool calls
9. Suspend and route human approvals
10. Track budget consumption
11. Run pre/post execution hooks
12. Persist sessions as a branch tree
13. Compact context when full
14. Push event streams to frontend
15. Emit OpenTelemetry traces across the full path

## Hermes architecture mapping

Hermes already has many of the 15 tasks, but they are distributed across agent core, gateway, cron, taskpool, plugins, and session DB. The key learning is not to copy another framework. The valuable abstraction is to make each production concern visible as an independently swappable contract.

### Current Hermes landing zones

- Frontend/gateway request persistence: `gateway/`, `hermes_state.py`, session DB
- Model credential resolution/capabilities: provider adapters, `run_agent.py`, model/provider config
- Conversation state machine: `run_agent.py` / `AIAgent.run_conversation()`
- Skills: `skills/`, `agent/skill_commands.py`, runtime skill injection
- Prompt assembly: persona/config/context/memory/skills/tool schemas in agent loop
- Token streaming/event delivery: gateway/platform adapters, provider streaming path, Telegram delivery semantics
- Tool-call policy: `model_tools.py`, tools registry, execution surface controls, permission gates
- Human approval: taskpool permission gates, user clarification, gateway message route
- Budget tracking: iteration budget, model/cost/routing scripts, taskpool budget-handoff authority
- Hooks: gateway hooks, taskpool event hooks, cron hooks, post-update checks
- Session branch tree: FTS session DB and conversation lineage; not fully exposed as a branch-tree control plane
- Context compaction: context engine/compression modules and provider adapters
- Event stream frontend: TUI/gateway surfaces; not uniformly modeled as one event bus
- OpenTelemetry: observability plugin exists, but full-path trace contract is not guaranteed at every worker boundary

## Gap assessment

### Gap 1 — Missing explicit harness contract matrix

Hermes has the pieces, but no single machine-readable matrix says which component owns each harness concern, which input/output contract it exposes, and whether it is swappable.

Impact: future contributors may add features inside `run_agent.py` or gateway paths instead of creating composable worker contracts.

### Gap 2 — Engine-bus vs implicit call-chain boundary

Hermes already uses events in taskpool and gateway hooks, but the core agent loop still contains a large sequential call chain. The proposed worker-bus idea suggests a stronger boundary: each harness task emits/consumes typed events and can be replaced by config.

Impact: high-risk refactors become easier if the first step is documentation + probes, not immediate rewiring.

### Gap 3 — Thin/thick harness profiles

The video's important operational point is that a thin agent should not pay for a thick production harness unless the use case needs it. Hermes currently has toolsets and profile-level config, but not an explicit `thin/thick harness profile` concept spanning all 15 tasks.

Impact: could improve cost/latency and reduce prompt/tool overhead for simple sessions while keeping production governance available for taskpool/work lanes.

### Gap 4 — Session branch tree as first-class artifact

Hermes has session search/lineage, but branch-tree semantics are not consistently surfaced as an operational contract. The video calls out branch-tree persistence as one of the 15 independent tasks.

Impact: useful for retries, forks, approvals, and multi-agent handoff; should be evaluated against Hermes session DB before implementation.

### Gap 5 — Full-path tracing contract

Hermes has observability capabilities, logs, run ledger, taskpool evidence, and browser replay evidence, but not a guaranteed OpenTelemetry-style trace boundary across request → model → tool → approval → event stream → session persist.

Impact: production diagnosis improves if trace IDs cross the same 15 harness concerns.

## Capability delta for Hermes

If absorbed carefully, this video should produce a **harness concern map** for Hermes:

- a 15-row matrix mapping concern → current Hermes owner → contract/input/output → swappability → evidence/log/trace boundary;
- a thin/thick profile proposal that reduces overhead without weakening proof gates;
- a worker-bus adoption plan that starts with typed events/probes before changing runtime execution;
- a red-line rule preventing monolithic framework-style accretion in `run_agent.py` / gateway / taskpool.

## Smallest verifiable integration slice

Do not implement a worker bus immediately. First task should be source-verified and audit-only:

1. Verify the primary source if possible: original video/transcript/article by 精读AI or Mike Piccolo/iii.dev.
2. Create `docs/private/hermes-agent-harness-15-concern-map-*.md` mapping all 15 tasks to Hermes owners and existing files.
3. Mark each concern as one of:
   - `already-modular`
   - `implicit-call-chain`
   - `config-swappable`
   - `event-hooked`
   - `missing-contract`
4. Pick at most one low-risk prototype:
   - a typed event/probe emitted at one existing boundary; or
   - a config-only thin/thick harness profile draft; or
   - a trace-id propagation proof across one request/tool/session path.
5. No runtime behavior change until the matrix is reviewed and passes taskpool proof gates.

## Red lines / anti-drift

- Do not rewrite Hermes into an external framework or import LangChain-style abstraction layers.
- Do not build a competing taskpool or duplicate gateway/session state.
- Do not weaken proof gates, permission gates, human approval, or budget-handoff authority for thin profiles.
- Do not treat `engine bus` as a buzzword; every proposed worker must have input/output/event/error/trace contracts.
- Do not collapse all 15 concerns into one new “harness manager” class; that would recreate the criticized monolith.
- Do not implement based solely on the secondary summary; verify primary source before code changes.

## Recommended taskpool classification

- Lane: `evolution`
- Project: `hermes-agent`
- Architecture target: `local-brain`
- Priority: `P2`
- Multimodel triage: `advisory`
- Requires multimodel later: yes, if the follow-up proposes runtime worker-bus or tracing changes
- Parallel boundary: source verification + Hermes concern matrix + one low-risk prototype proposal only

## Acceptance evidence for task closeout

- Primary source search/verification result recorded, including if source remains unavailable.
- 15-concern Hermes matrix with concrete file owners and gaps.
- At most one prototype proposal, bounded and reversible.
- No code changes unless explicitly split into a follow-up task with tests/proof gates.
- `proof-check` / `selfcheck` after task creation or any lifecycle transition.
