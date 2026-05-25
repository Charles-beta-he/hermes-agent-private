# Bytebot source-backed gap analysis for Hermes

Source: https://github.com/bytebot-ai/bytebot
Checked: 2026-05-21
Local clone: `/tmp/bytebot`
Commit checked: `3d37894ce07ef8d8b40adc7fd309ad96c2a71313`
GitHub API canonical: `bytebot-ai/bytebot`
License: Apache-2.0 at repository root
Runtime shape: Docker Compose / containers, Node/NestJS/Next.js, PostgreSQL, Ubuntu 22.04 XFCE desktop, nut.js desktop automation, noVNC, REST + MCP surfaces.

## Repository shape

Bytebot is a self-hosted AI desktop agent. It gives the agent a containerized Linux desktop rather than only a browser/plugin/API surface.

Major components verified from source/docs:

- `bytebot-desktop` / `bytebotd`: Ubuntu desktop + automation daemon, exposed on port 9990.
- `bytebot-agent`: NestJS task processor, LLM orchestration, task/message persistence, exposed on port 9991.
- `bytebot-ui`: Next.js task UI + VNC view/takeover UX, exposed on port 9992.
- `postgres`: task/message/file persistence.
- Docs/API: `docs/core-concepts/architecture.mdx`, `docs/rest-api/computer-use.mdx`, `docs/guides/takeover-mode.mdx`.

## Verified core mechanics

### 1. Real desktop execution boundary

`README.md` and architecture docs describe a full virtual desktop, not just web automation:

- Ubuntu 22.04 + XFCE4.
- Firefox ESR, Thunderbird, text editors/dev tools.
- noVNC for live/manual access.
- bytebotd daemon built on nut.js.
- direct mouse/keyboard/screenshot/file actions.

The default docker compose exposes:

- `9990`: desktop daemon + noVNC / computer-use.
- `9991`: agent API.
- `9992`: UI.
- `5432`: PostgreSQL in the provided compose.

Important security caveat: the compose sets `privileged: true` on the desktop container and architecture docs state Desktop API has no auth by default when localhost-only. This must be fail-closed for Hermes adoption.

### 2. Computer-use action API

`packages/bytebotd/src/computer-use/computer-use.controller.ts` exposes `POST /computer-use` with validation pipe.

`packages/bytebotd/src/computer-use/computer-use.service.ts` routes action types:

- `move_mouse`, `trace_mouse`, `click_mouse`, `press_mouse`, `drag_mouse`.
- `scroll`, `type_keys`, `press_keys`, `type_text`, `paste_text`, `wait`.
- `screenshot`, `cursor_position`.
- `application`.
- `write_file`, `read_file`.

`packages/bytebotd/src/mcp/computer-use.tools.ts` exposes corresponding MCP tools such as `computer_move_mouse`, `computer_click_mouse`, etc.

### 3. Agent loop and task lifecycle

`packages/bytebot-agent/src/agent/agent.processor.ts` shows the task loop:

- one active task guard via `isProcessing/currentTaskId`.
- loads latest summary + unsummarized messages.
- calls model provider service: Anthropic/OpenAI/Google/proxy.
- records assistant blocks.
- executes computer tool-use via `handleComputerToolUse`.
- summarizes when token usage exceeds threshold.
- supports takeover/resume/cancel events.

`packages/bytebot-agent/prisma/schema.prisma` defines task state:

- `PENDING`, `RUNNING`, `NEEDS_HELP`, `NEEDS_REVIEW`, `COMPLETED`, `CANCELLED`, `FAILED`.
- `control` field tracks USER/ASSISTANT.
- messages/summaries/files are persisted in PostgreSQL.

### 4. Takeover mode

`docs/guides/takeover-mode.mdx` verifies human-in-the-loop behavior:

- manual takeover during task execution aborts in-flight operations and starts input capture.
- `NEEDS_HELP` enables takeover.
- user clicks/drags/scrolls/typing/key presses are logged into the same action space.
- desktop tab setup mode is not recorded.
- docs warn that screenshots are taken for every takeover action.

## Hermes relevance

Bytebot is most relevant to Hermes' long-term external-control / computer-use / browser-harness evolution. It complements previous CUA / Agent-S style analysis with a more productized, self-hosted desktop-agent pattern.

## Current Hermes boundary to preserve

Source-backed current Hermes surfaces that matter before absorbing anything from Bytebot:

- `tools/computer_use/schema.py`, `tools/computer_use/tool.py`, and `website/docs/user-guide/features/computer-use.md` show Hermes computer-use is currently host-macOS only, background/co-work oriented, approval-gated for mutating actions, and already has explicit safe actions (`capture`, `wait`, `list_apps`) plus a `noop` backend for test/CI.
- `plugins/browser/browser_use/provider.py` shows Hermes browser-harness already treats remote browser creation as an authenticated/idempotent managed boundary.
- `~/.hermes/scripts/external_control_gate.py` shows Hermes already has a stronger external-control governance pattern than Bytebot: action schema validation, fresh telemetry checks, assumption checks, confirmation tokens, idempotency keys, audit paths, and fail-closed refusal of real execution.

That means Hermes should absorb Bytebot as an additional isolated runtime boundary, not replace the existing approval/idempotency/audit model with Bytebot's broader unauthenticated desktop API.

## Hermes ↔ Bytebot gap matrix

| Capability / boundary | Hermes today | Bytebot verified surface | Adopt / reject decision |
|---|---|---|---|
| Host interaction model | macOS background computer-use on the real host desktop via `cua-driver`; mutating actions approval-gated | isolated Ubuntu/XFCE desktop container with noVNC + REST/MCP desktop daemon | Adopt as optional sandbox lane only; keep host-macOS tool for local co-work |
| Browser automation | authenticated managed/direct Browser Use sessions with idempotent session creation | desktop-resident Firefox inside the container; broader than browser-only | Adopt as escalation path when browser-harness is insufficient, not as default |
| Safety gate for external actions | strong fail-closed packet gate in `external_control_gate.py` | desktop API on `9990` has no auth by default; compose exposes ports | Reject direct exposure pattern; wrap any Bytebot call in Hermes-style scope/idempotency/audit gate |
| Human takeover | Hermes currently has approval checkpoints but no unified desktop takeover trace in the same action language | takeover mode logs user actions in the same action space and can auto-enter on `NEEDS_HELP` | Adopt trace schema/state-machine idea, but only inside a sandbox and with screenshot-retention policy |
| Task lifecycle honesty | Hermes kanban/taskpool already has `blocked` / review semantics, but computer-use runtime itself does not emit a Bytebot-like desktop help state | `NEEDS_HELP` / `NEEDS_REVIEW` are first-class task states in Prisma | Adopt mapping concept into Hermes task/runtime bridge |
| File mutation in GUI runtime | Hermes separates filesystem tools from computer-use and external-control | Bytebot desktop API includes `write_file` / `read_file` plus app launching | Reject broad file/app mutation until it passes Hermes permission-tier and audit gates |
| Auth/network defaults | Hermes managed browser path is credentialed and gateway-aware | Bytebot docs explicitly say desktop API has no auth by default and compose binds public ports | Reject current default for production; require localhost-only or authenticated proxy |

## Net-new value vs earlier CUA / Agent-S analysis

What is materially new here, beyond generic GUI-agent research:

1. Productized disposable desktop: Bytebot gives Hermes a concrete reference for a full isolated Linux desktop runtime, not just another host-desktop or browser-only action policy.
2. Takeover trace capture: the same action vocabulary is shared between agent and human intervention, which is more operationally useful than simple pause/resume approvals.
3. Runtime task honesty: `NEEDS_HELP` and `NEEDS_REVIEW` are encoded at the desktop-agent level, not bolted on later in orchestration.
4. Dual REST+MCP desktop surface: Hermes could consume a narrow read-only desktop capability through MCP or HTTP without embedding Bytebot internals.

### High-value patterns to absorb

1. Containerized disposable desktop rather than acting on the user's real machine.
   - Safer boundary for browsing, downloading, login workflows, and GUI testing.
   - Aligns with Hermes red line: do not mutate host apps/files unless explicitly scoped.

2. Unified action space for agent and human takeover.
   - Human demonstrations become machine-readable action traces.
   - Useful for Hermes browser-harness acceptance and future workflow learning.

3. Task state machine with `NEEDS_HELP` and `NEEDS_REVIEW`.
   - More honest than silent failure or fake completion.
   - Maps well to taskpool blocked/review states.

4. Screenshot-after-action feedback loop.
   - After each GUI action, capture state for next model step.
   - Hermes could formalize this as an observation/action/verification loop.

5. Desktop API + MCP dual surface.
   - Hermes can call a read-only/sandboxed computer-use MCP without embedding Bytebot internals.

6. Persistent configured desktop.
   - Useful for password-manager based login flows and app-specific setup, if secrets are contained and screenshot capture policy is explicit.

### Do not adopt blindly

1. Do not expose unauthenticated `9990/9991/9992` beyond localhost.
2. Do not run privileged desktop containers as a default Hermes capability.
3. Do not let Bytebot operate on the host desktop directly; keep a virtual/container boundary.
4. Do not store login screenshots/action traces without retention and redaction policy.
5. Do not map Bytebot's broad action API to Hermes tools without permission tiers and confirmation gates.
6. Do not treat README marketing claims as evidence of reliability; verify with deterministic workflows.

## Suggested Hermes execution order

1. Read-only compatibility spike:
   - run Bytebot only in an isolated environment/profile.
   - verify `/computer-use` screenshot and cursor position only.
   - no credentials, no external accounts, no host mounts.

2. Safety wrapper:
   - add Hermes action packet for computer-use operations.
   - require scope, target desktop id, screenshot retention policy, credential policy, idempotency key, audit path.
   - default allowlist: screenshot/cursor/read-only only.

3. Browser-harness bridge:
   - compare Bytebot desktop browser workflows against current Hermes browser-harness.
   - use Bytebot only when browser_harness cannot handle desktop-app, download-folder, password-manager, or non-web UI cases.

4. Human takeover trace prototype:
   - capture a manually demonstrated workflow in the container.
   - convert action trace + screenshots into a reusable skill/checklist, not automatic replay yet.

5. Production hardening gate:
   - authentication, network isolation, no privileged mode unless justified, storage TTL, screenshot redaction, audit/selfcheck.

## Read-only spike plan (temporary profile only)

Goal: verify Hermes can treat Bytebot as a sandboxed observation source before allowing any mutating desktop actions.

Boundary for the spike:

- isolated temporary Hermes profile or disposable workdir only;
- local-only Bytebot runtime;
- no real accounts, no host mounts, no shared secrets, no privileged follow-on experiments;
- allowlist only `screenshot` and `cursor_position` equivalents;
- evidence recorded as source paths + hashes + audit notes.

Suggested staged plan:

1. Runtime isolation
   - Launch Bytebot in a disposable environment only.
   - Keep ports bound to localhost; do not expose `9990/9991/9992` outside the machine.
   - Do not reuse the user's normal Hermes profile or desktop session.

2. Read-only bridge
   - Build a tiny Hermes-facing adapter that only exposes `capture screenshot` and `cursor position`.
   - Require an explicit desktop target id and audit path even for read-only calls.
   - Refuse `click/type/scroll/drag/application/read_file/write_file` in the adapter.

3. Deterministic verification
   - Verify a screenshot round-trip and cursor-position query from the isolated desktop.
   - Verify the adapter refuses every mutating action class.
   - Record evidence with source refs and response payload samples.

4. Exit criteria before any write-capable follow-up
   - authenticated or localhost-fenced desktop access;
   - Hermes-side idempotency/audit wrapper around every desktop request;
   - screenshot retention/redaction policy;
   - explicit mapping from runtime `needs_help/review` to Hermes blocked/review states.

## Explicit adoption boundary

Absorb now:

- isolated-container desktop reference architecture;
- takeover trace/state-machine concepts;
- read-only desktop observation spike;
- runtime-to-taskpool state mapping design.

Do not absorb now:

- unauthenticated exposed desktop/agent/UI ports;
- privileged desktop container as a silent default;
- Bytebot file/app mutation API as a direct Hermes tool surface;
- storage of takeover screenshots without retention/redaction controls;
- any direct control of the host desktop or real accounts via Bytebot.

## Recommended task priority

P2 evolution: high leverage for Hermes computer-use/external-control capability, but not a correctness blocker. Requires security review before any execution beyond read-only screenshot/cursor checks.
