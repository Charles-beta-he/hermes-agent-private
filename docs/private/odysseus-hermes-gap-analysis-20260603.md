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
- `src/agent_loop.py`: streaming multi-round tool execution, MCP manager, tool schemas, tool execution rules, memory/notes/email/task routing instructions, native/function-call rule set.
- `src/agent_tools.py`: facade over tool parsing/schemas/execution/implementations; tool block tags include shell, Python, web search/fetch, file read/write, memory, tasks, endpoints, MCP, research, email, UI control, image editing.
- `src/context_budget.py` + `src/context_compactor.py`: adaptive input-token budget and compaction around context-window thresholds; repairs orphaned tool messages after trimming.
- `src/deep_research.py`: IterResearch-style loop: plan → searches → evolving report → comprehensiveness check → final report.
- `src/bg_jobs.py`: background shell job state, monitor/re-invocation pattern so completed long jobs do not silently disappear.
- `src/builtin_mcp.py`: built-in MCP registration for image generation, memory, RAG, email, Playwright browser.
- `THREAT_MODEL.md`: explicit self-hosted admin-console trust boundary; untrusted content wrapper for web/email/memory/skills/tool output; non-admin blocked tools; internal loopback token; no filesystem/shell sandbox caveat.
- `ROADMAP.md`: known gaps around smaller local model prompt bloat, tool selection, skill/tool prompt-injection audit, agent-actionable todos, and security hardening.

## Architecture signals relevant to Hermes

1. **Workspace product shell, not just agent runtime**
   Odysseus bundles chat, agents, research, docs, notes/tasks, email, calendar, cookbook/model serving, PWA/mobile, and settings into one local-first UX. Hermes already has stronger taskpool/governance primitives, but Odysseus is useful as a reference for user-facing workspace consolidation.

2. **Tool access model via MCP + direct tools**
   Built-in MCP servers and direct tool execution are surfaced as app capabilities. Hermes can compare this against its native MCP/gateway/toolset approach, especially for tool discoverability and UI exposure.

3. **Agent loop ergonomics**
   The repo encodes concise operational rules for action bias, failure retry, background job auto-continue, email UID discipline, and panel controls. Hermes already has richer policy, but Odysseus highlights which rules are product-critical enough to appear in the runtime agent prompt.

4. **Context budget and compaction**
   Odysseus has explicit context budget computation and tool-message sanitation after trimming. Hermes can evaluate whether any compact, testable cases should be added to Hermes context compression/regression tests.

5. **Deep Research as staged report loop**
   Its research engine is an IterResearch-inspired deterministic loop. Hermes should treat it as a reference for report-loop UX and evidence update cadence, not as a replacement for taskpool certification.

6. **Security/threat model clarity**
   `THREAT_MODEL.md` is unusually explicit for a self-hosted AI workspace: admin-console framing, untrusted surfaces, loopback token, non-admin tool restrictions, and unsandboxed shell/filesystem caveats. Hermes can absorb the documentation pattern and compare against its own user-facing safety contract.

## Hermes gap mapping

- `local-brain / workspace UX`: Hermes has strong backend/taskpool primitives but less centralized local-first workspace UI articulation. Gap: product-facing map of capabilities and their trust boundaries.
- `taskpool / background jobs`: Odysseus bg job auto-continue overlaps Hermes process notifications and cron/taskpool runners. Gap: compare durable job completion semantics and whether Hermes needs a simpler visible “long job continuation” artifact in task notes.
- `context management`: Hermes has context compression, but Odysseus provides small code-level examples around orphaned tool-message repair. Gap: add regression cases if Hermes lacks equivalent provider-message sanitation tests.
- `research workflow`: Hermes can use Odysseus as a benchmark for research UX/report-loop shape while preserving Hermes evidence-first/multimodel gates. Gap: no direct adoption until benchmarked against current Hermes research/taskpool outputs.
- `security docs`: Hermes has many internal red lines; gap is user-facing consolidated threat model for self-hosted privileged operation.

## Smallest verifiable integration slice

Do **not** clone/install/run Odysseus as part of the first task. First slice should be source-only and reversible:

1. Build a source-pinned comparison artifact: Odysseus capability map → Hermes existing primitives → accept/reject/watch.
2. Select at most two candidate absorptions:
   - threat-model documentation structure;
   - context/tool-message compaction regression cases;
   - background job auto-continue UX proof artifact.
3. For each candidate, produce a Hermes-local proof:
   - docs patch or test fixture path;
   - no new runtime dependency;
   - no third-party skill/tool execution;
   - taskpool proof-check/selfcheck after any landed change.

## Red lines / anti-drift

- No installing or running Odysseus during intake.
- No copying prompt rules wholesale into Hermes; convert only class-level patterns that survive Hermes governance.
- No weakening Hermes taskpool proof gates or multimodel certification in favor of Odysseus’ simpler workspace loop.
- No indexing private data or secrets from Odysseus examples.
- Preserve Hermes SSOT: Odysseus notes/tasks/research patterns are reference architecture only, not a competing taskpool.

## Recommended taskpool classification

- Lane: `evolution`
- Project: `hermes-agent`
- Architecture target: `local-brain`
- Priority: `P2`
- Multimodel triage: `advisory` initially; require multimodel certification only if the follow-up proposes control-plane/security behavior changes.
- Parallel boundary: source review/gap mapping/prototype selection only; no clone/install/runtime integration in first pass.

## Acceptance evidence for task closeout

- Read-back of this artifact path and source commit.
- A gap matrix grounded in inspected source files, not README-only marketing.
- One bounded adoption/rejection decision for each candidate mechanism.
- If code/docs are changed: scoped commit plus `proof-check`/`selfcheck` or targeted tests.
