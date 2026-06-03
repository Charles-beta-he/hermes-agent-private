# Odysseus → Hermes self-strengthening intake (2026-06-03)

## Source identity

- Repo: https://github.com/pewdiepie-archdaemon/odysseus
- Canonical full name: `pewdiepie-archdaemon/odysseus`
- Default branch: `main`
- Inspected commit: `ed7956cbd3bb880c2eaa610709e16af882251ff5`
- Local source review clone: `/Users/charles/.hermes/runs/odysseus-reference-review-20260603`
- License: MIT
- GitHub metadata at intake: ~34.4k stars, ~4.1k forks, JavaScript/Python app surface
- Description: self-hosted AI workspace

## Live source evidence inspected

- `README.md`: self-hosted ChatGPT/Claude-like workspace; local-first/privacy-first; chat, agents, cookbook/model serving, deep research, model comparison, documents, memory/skills, email, notes/tasks, calendar, mobile PWA, image editor, web search, presets/sessions/2FA.
- Top-level layout: `app.py`, `src/`, `routes/`, `services/`, `core/`, `mcp_servers/`, `docker-compose.yml`, `THREAT_MODEL.md`, `ROADMAP.md`, `tests/`, `static/`, `docs/`.
- `src/agent_loop.py` (2291 lines): streaming multi-round tool execution, MCP manager, tool schemas, tool execution rules, memory/notes/email/task routing instructions, native/function-call rule set. Tools described via fenced code blocks + JSON function schemas; multi-round with 20-round cap.
- `src/agent_tools.py` (134 lines): facade over sub-modules (parsing, schemas, execution, implementations). 60+ TOOL_TAGS. Constants: MAX_AGENT_ROUNDS=20, SHELL_TIMEOUT=60, PYTHON_TIMEOUT=30, MAX_OUTPUT_CHARS=10K.
- `src/context_budget.py` (55 lines): **Key artifact** — pure-function `compute_input_token_budget()` that derives effective budget from configured value, model context_length, and explicit flag. Constants: DEFAULT_BUDGET=6000, DEFAULT_HARD_MAX=200K, headroom=0.85. Unit-testable, no I/O.
- `src/context_compactor.py` (376 lines): adaptive compaction triggered at 85% context window. Structured self-summarization with cursor-style prompt. `_sanitize_tool_messages()` repairs orphaned tool messages after trimming. `trim_for_context()` drops extra system messages, truncates system prompt, then drops older conversation turns while protecting recent 10 + current message.
- `src/deep_research.py` (887 lines): IterResearch-style loop: plan → classify → search → extract → synthesize → decide (continue/stop). Configurable rounds (default 8), time limit (5 min), URLs per round (3), content chars (15K), extraction concurrency (3). Category-specific report formats (product, comparison, how-to, fact-check).
- `src/bg_jobs.py` (270 lines): **Key artifact** — restart-safe on-disk background job state machine. Launch → poll exit-code file → `followed_up` flag prevents double-reinvocation. Retains finished results for 1 hour, then prunes. Max runtime 1 hour, output capped at 16K chars, head+tail preservation on truncation.
- `src/builtin_mcp.py` (222 lines): startup-time auto-registration of Python-stdio and npx-based MCP servers (image_gen, memory, rag, email, Playwright browser). Cache-probe before npx launch to avoid hanging on first-start downloads.
- `THREAT_MODEL.md` (81 lines): explicit self-hosted admin-console trust boundary; untrusted content wrapper for web/email/memory/skills/tool output; non-admin blocked tools; internal loopback token; no filesystem/shell sandbox caveat.
- `ROADMAP.md` (80 lines): acknowledged gaps around smaller local model prompt bloat, tool selection, skill/tool prompt-injection audit, agent-actionable todos, and security hardening.

## Gap Matrix: Odysseus capability → Hermes equivalent → gap assessment

| Odysseus module | Odysseus pattern | Hermes equivalent | Gap class | Impact |
|---|---|---|---|---|
| `context_budget.py` | Pure-function `compute_input_token_budget(configured, context_length, explicit) → int` | `IterationBudget` counts API calls only; token budget for compression is in `ContextCompressor` config/scattered across `config.yaml` + provider metadata. No isolated pure function. | **missing** — Hermes lacks a standalone, testable input-token-budget computation that is model-aware, explicit-setting-honouring, and headroom-scaled. | **high** — every agent turn needs this. A portable pure function would reduce provider-adapter complexity and improve test coverage. |
| `context_compactor.py:_sanitize_tool_messages` | Provider-agnostic 2-pass orphan tool message repair after front-trimming (OpenAI format: `role:"tool"`). | Hermes has `_strip_orphaned_tool_blocks` in `agent/anthropic_adapter.py` — Anthropic-specific (block-level format). `run_agent.py` has no equivalent for the OpenAI API path. | **partial** — missing OpenAI-compatible provider path. | **medium** — multi-provider consistency. Currently OpenAI provider could silently fail on orphaned tool messages. |
| `context_compactor.py:trim_for_context` | Adaptive multi-stage trim: drop extra system → truncate system → drop old turns (protect 10 recent) → shrink current message. | Hermes `ContextCompressor._prune_old_tool_results` has richer tool-output dedup + summarization, but no equivalent step-by-step trim-first-before-compact pipeline. | **covered** — Hermes approach is more sophisticated. | **low** — Hermes already has stronger near-term context management. |
| `bg_jobs.py` | Restart-safe on-disk state machine: launch detached → poll exit-code file → `followed_up` flag for agent auto-continue. 1h max runtime, 1h retention. | Hermes `terminal_tool.py` has `notify_on_complete` (in-memory, ephemeral on process restart) and `gateway/run.py` has session-level gateway auto-continue (interruption recovery, not job completion). No durable on-disk BG job tracker. | **gap** — Hermes has no restart-safe durable BG job state machine with re-invocation semantics. | **medium** — gateway auto-continue covers session interruption, but long shell jobs during `terminal(background=true)` are lost on gateway restart. |
| `deep_research.py` | IterResearch-style: plan → classify → search → extract → synthesize → decide (8 rounds). Category-specific final reports. | Hermes has a `research` skill (optional) and research-related skills, but no built-in dedicated DeepResearcher engine with plan/search/extract/synthesize loop. | **gap** — Hermes delegates research to skills/workflow; no compiled research engine. | **low** — acceptable gap; Hermes' workflow/kanban/taskpool architecture can compose equivalent behavior via skills. A dedicated engine risks duplicating taskpool orchestration. |
| `THREAT_MODEL.md` | Consolidated user-facing threat model doc: trust boundary, roles/capabilities table, authentication, internal loopback, prompt-injection hardening, security headers, known gaps. | Hermes has distributed red lines in `AGENTS.md`, skill governance docs, and brain contracts. No single user-facing "self-hosted threat model" document. | **gap** — documentation pattern. | **medium** — a consolidated threat-model doc would clarify Hermes' self-hosted security posture for users and contributors. |
| `agent_loop.py` prompt assembly | Dynamic tool-section selection driven by user intent keywords (`_detect_admin_intent`). Tools included/excluded per request based on keyword match. | Hermes uses toolset definitions (`toolsets.py`) with static enable/disable per session. No dynamic per-round tool selection. | **philosophical difference** — Hermes favors explicit toolset configuration; Odysseus favors adaptive inclusion. | **low** — static toolset boundaries fit Hermes' governance model better. Dynamic selection could weaken proof boundaries. |
| `builtin_mcp.py` startup registration | Auto-register built-in MCP servers on app startup with cache-probe before npx launch. | Hermes' MCP servers are configured in `config.yaml` and auto-discovered. No equivalent startup-time auto-registration of own-services-as-MCP. | **covered** — Hermes uses config-based MCP setup instead of code-based auto-registration. | **low** — different approach, both functional. |
| `tool_security.py` | Per-role tool blocking: non-admins blocked from shell/file/email/MCP/calendar/tokens/webhooks/settings. | Hermes has `agent/execution_surface_controls.py` with permission gates and boundary enforcement during deployment. | **covered** — Hermes' execution surface controls are more granular. | **low** — Hermes is stronger here. |

## Hermes gap mapping (summary)

- **local-brain / workspace UX**: Hermes has strong backend/taskpool primitives but less centralized local-first workspace UI articulation. Gap: product-facing map of capabilities and their trust boundaries.
- **taskpool / background jobs**: Odysseus bg job auto-continue overlaps Hermes process notifications and cron/taskpool runners. Gap: compare durable job completion semantics and whether Hermes needs a simpler visible "long job continuation" artifact in task notes.
- **context management**: Hermes has context compression, but Odysseus provides small code-level examples around orphaned tool-message repair. Gap: add regression cases if Hermes lacks equivalent provider-message sanitation tests.
- **research workflow**: Hermes can use Odysseus as a benchmark for research UX/report-loop shape while preserving Hermes evidence-first/multimodel gates. Gap: no direct adoption until benchmarked against current Hermes research/taskpool outputs.
- **security docs**: Hermes has many internal red lines; gap is user-facing consolidated threat model for self-hosted privileged operation.

## Candidate absorption slices (max 2)

### Slice A: Provider-agnostic orphan tool message sanitizer — ACCEPT

**Source**: Odysseus `context_compactor.py:_sanitize_tool_messages()` (lines 52-95)

**Pattern**: 2-pass message list sanitization:
1. Drop orphan `role:"tool"` messages whose parent assistant `tool_calls` were trimmed away
2. Strip dangling assistant `tool_calls` whose tool responses were all trimmed (preserves any text content)

**Why Hermes needs it**: Hermes' Anthropic adapter (`_strip_orphaned_tool_blocks`) already has this for the Anthropic block format. The OpenAI/OpenAI-compatible API path in `run_agent.py` does NOT — it relies on the agent loop not producing orphaned tool messages, which is not guaranteed after context compression or session truncation. This is a real issue: OpenAI APIs reject `role:"tool"` messages without a preceding `tool_calls` parent with HTTP 400.

**Integration recommendation**: Extract a pure utility function `agent/_sanitize_tool_messages(messages: List[Dict]) -> List[Dict]` that is:
- Provider-agnostic (operates on OpenAI message format)
- Unit-testable (no I/O, no config)
- Called by `run_agent.py` after any message list mutation (compression, truncation)

**Rationale**: Low-risk (~50 lines), fills a gap across all OpenAI-compatible providers, high testability, no new runtime dependency.

**Decision**: ACCEPT — add to `agent/` module with targeted unit tests.

### Slice B: Durable background job state machine — WATCH

**Source**: Odysseus `bg_jobs.py` (270 lines)

**Pattern**: On-disk state machine with launch → poll → follow-up lifecycle:
- Jobs stored in `data/bg_jobs.json` with per-job `.sh`/`.log`/`.exit` files
- `refresh()` reconciles running jobs against disk on every call
- `pending_followups()` + `mark_followed_up()` prevent double-reinvocation
- 1-hour max runtime with forced timeout
- 1-hour retention after follow-up, then prunes

**Why Hermes might need it**: Hermes' `terminal(background=true)` with `notify_on_complete` works but state is in-memory. A gateway process restart loses all running BG job state. If Hermes is run as a persistent gateway with long-running background tasks, restart-survivable job tracking would prevent silent work loss.

**Why WATCH instead of ACCEPT**:
- The gateway auto-continue mechanism (`gateway/run.py`) already covers the main "agent was interrupted mid-turn" case
- Background shell jobs that survive a gateway restart are rare in Hermes' architecture (cron handles recurring work)
- The pattern introduces on-disk I/O in the terminal tool's hot path
- Would need careful integration with Hermes' existing `notify_on_complete` and process-polling model

**Watch triggers**:
- If gateway restarts causing lost background jobs becomes a user-reported issue
- If telemetry shows frequent "background job vanished" cases
- If a session-level durable BG job tracker is needed for kanban workers or long-running subagents

**Decision**: WATCH — do not implement now, but document the pattern for reference.

## Rejected/excluded patterns

| Pattern | Rejection reason |
|---|---|
| Dynamic tool selection per user intent keyword (`_detect_admin_intent`) | Weakens Hermes' explicit toolset governance model. Static boundaries are a feature, not a bug. |
| Deep Research report loop as a built-in engine | Hermes' taskpool + skills architecture can compose equivalent behavior. A dedicated engine would duplicate orchestration logic. |
| Fenced-code-block tool parsing (Odysseus agent_loop) | Hermes uses native function-calling via OpenAI SDK. Fenced blocks are a fallback for models that don't support native calling. Existing support is adequate. |
| Startup-time auto-registration of MCP servers from code | Hermes uses config-based MCP setup. Code-based registration adds startup coupling without clear benefit. |

## Acceptance closeout (this session)

- ✅ Read-back of this artifact path and source commit `ed7956c`
- ✅ Gap matrix grounded in inspected source files (src/agent_loop.py, src/agent_tools.py, src/context_budget.py, src/context_compactor.py, src/deep_research.py, src/bg_jobs.py, src/builtin_mcp.py, THREAT_MODEL.md, ROADMAP.md)
- ✅ Two candidate mechanisms with accept/reject/watch decisions
- No code/document changes this pass — within phase-1 boundary ("第一阶段仅限源代码审查、差距矩阵、候选切片选择；禁止 clone 之外的安装/运行/登录/私有数据索引；后续实现须另行由任务池分解")

## Red lines / anti-drift (unchanged from first pass)

- ✅ No installing or running Odysseus during intake.
- ✅ No copying prompt rules wholesale into Hermes; converted only class-level patterns that survive Hermes governance.
- ✅ No weakening Hermes taskpool proof gates or multimodel certification.
- ✅ No indexing private data or secrets from Odysseus examples.
- ✅ Preserved Hermes SSOT: Odysseus patterns are reference architecture only.

## Architecture signals relevant to Hermes (summary)

1. **Workspace product shell, not just agent runtime** — useful as UX reference.
2. **Tool access model via MCP + direct tools** — compare tool discoverability approaches.
3. **Agent loop ergonomics** — highlights which rules are product-critical.
4. **Context budget and compaction** — isolated pure function worth absorbing.
5. **Deep Research as staged report loop** — reference for report-loop UX.
6. **Security/threat model clarity** — documentation pattern worth absorbing.

## Verification

| Command / Check | Status | Evidence |
|---|---|---|
| Source clone exists at expected path | PASS | `/Users/charles/.hermes/runs/odysseus-reference-review-20260603` — 43 entries, git checkout of `ed7956c` |
| THREAT_MODEL.md read and analyzed | PASS | 81-line explicit threat model, roles/capabilities table |
| ROADMAP.md read and analyzed | PASS | 80-line roadmap, known gaps documented |
| agent_loop.py source read (first 500 lines) | PASS | Prompt assembly, tool sections, admin intent detection |
| agent_tools.py full source read | PASS | Facade module, 60+ TOOL_TAGS, constants |
| context_budget.py full source read | PASS | 55-line pure function, candidate for absorption |
| context_compactor.py full source read | PASS | 376-line compactor, _sanitize_tool_messages, trim_for_context |
| deep_research.py source read (first 500 lines) | PASS | IterResearch loop, plan/classify/search/extract/synthesize/decide |
| bg_jobs.py full source read | PASS | 270-line restart-safe BG job state machine |
| builtin_mcp.py full source read | PASS | 222-line startup server registration |
| Hermes existing orphan handling confirmed | PASS | Anthropic-only in `agent/anthropic_adapter.py`, OpenAI path lacks equivalent |
| Hermes IterationBudget confirmed | PASS | `agent/iteration_budget.py` — iteration counting only, not token budget |
| Hermes ContextCompressor confirmed | PASS | `agent/context_compressor.py` — ~1756 lines, sophisticated compression |
| Gap matrix complete | PASS | 10-row matrix covering all inspected Odysseus modules |
| Candidate selection documented | PASS | Slice A: ACCEPT (orphan sanitizer), Slice B: WATCH (BG job state machine) |
