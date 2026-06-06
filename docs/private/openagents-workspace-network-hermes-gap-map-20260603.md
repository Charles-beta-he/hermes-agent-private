# OpenAgents Workspace/Network → Hermes gap map (bounded follow-through tranche)

Task: `2026-06-02-评估-openagents-workspace-network-对-hermes-多智能体协作与外部工作区集成的参考价值-3`

Review time: 2026-06-03 cron follow-through executor
Source repo: https://github.com/openagents-org/openagents
Reviewed commit: `15c27db24079c8a04138a74491b253c2a5cea897` (`develop`, shallow clone under `~/.hermes/runs/openagents-reference-review-20260603-followthrough/repo`)

## Evidence checked

- `README.md:5` identifies OpenAgents Workspace as “The Collaborative OS for Agents”.
- `README.md:81-83` lists shared browser, shared files, and tunnels.
- `README.md:116` explicitly lists **Hermes Agent** as supported: “Nous Hermes CLI with tools, profiles, and memory”.
- `pyproject.toml:6-7` confirms package `openagents`, version `0.9.3.post20`; `pyproject.toml:28` requires Python `>=3.8`.
- `pyproject.toml:37-38,53,55` shows async/network dependencies and optional SDK surfaces including `aiohttp`, `websockets`, gRPC, MCP-related SDK notes.
- `docs/openagents_network_model.md:27` says the model defines how agents discover each other, communicate through events, and share resources within/across networks.
- `docs/openagents_network_model.md:33` states “Events, not requests.”
- `docs/openagents_network_model.md:43-44` describes progressive verification from local development to private/federated contexts.
- `docs/openagents_network_model.md:51` states transport-agnostic design.
- `workspace/README.md:20-21` maps Workspace backend/frontend to FastAPI + SQLAlchemy and Next.js + React.
- `workspace/README.md:26-31` lists `/v1/events`, `/v1/join`, `/v1/leave`, `/v1/discover`, and the `mod/auth → mod/workspace → mod/persistence` pipeline.
- `workspace/README.md:38` shows workspace-token/firebase auth mode.
- `packages/agent-connector/README.md:3,26,36,66` confirms install/configure/run agent runtime, daemon lifecycle, workspace-token connect, and supported agent catalog.

## Hermes primitive mapping

| OpenAgents primitive | Hermes analogue | Useful transfer | Risk / boundary |
|---|---|---|---|
| Event-native network model | taskpool lifecycle events, gateway hooks, run ledger | Adopt as a **read-only transport bridge vocabulary**: task.created/status_changed/proof.attached mapped to external event envelopes. | Must not replace `brain_task_orchestrator.py` + kanban/task note SSOT. |
| Bounded networks / contexts | brain overlays, project profiles, task lanes, permission tiers | Good naming model for explicit trust/deployment scopes around workers and external adapters. | External workspace boundary must not bypass Hermes permission/evidence gates. |
| Mod pipeline (`auth → workspace → persistence`) | lifecycle gates, proof-check, selfcheck, hook registry | Transfer as an ordered observer/guard pipeline for external events entering Hermes. | Do not accept workspace events as proof without Hermes-side verification artifact. |
| Progressive verification | Hermes local/dev vs provider-backed certification gates | Useful for staged trust: local read-only bridge first, signed/credentialed federation later. | Federated identity is out-of-scope for this tranche. |
| Shared browser/files/tunnels | Hermes tools + gateway/TUI/dashboard artifacts | Potential future UI collaboration surface for non-sensitive artifacts. | No tunnel exposure, browser sharing, token connect, or daemon install in this task. |
| Agent connector/launcher | Hermes CLI/profile/gateway worker bootstrap | Worth watching for catalog UX and external workspace discoverability. | Do not auto-install or delegate execution to third-party agent runtimes. |

## Recommendation

**Adopt selectively, not wholesale.** OpenAgents is valuable as a reference for external multi-agent workspace UX and an event-network/transport vocabulary. Hermes should keep its local brain/taskpool/proof-gate control plane as SSOT and only expose a governed read-only bridge outward.

## Minimal safe integration slice

1. Define a local, default-off `openagents_event_bridge` contract that exports Hermes taskpool events as read-only JSON envelopes.
2. Include only non-sensitive fields: task id/title/lane/status/priority/evidence artifact paths that are already shareable; redact prompts, credentials, local file content, browser state, and private comments.
3. Verify with a deterministic dry-run script that reads current taskpool state and writes an evidence artifact without opening a network listener, tunnel, workspace token, daemon, or external runtime.
4. Keep all inbound events advisory until a separate ratified gate defines identity/auth/proof semantics.

## Closeout judgment for current task

This tranche satisfies the requested source-review/gap-map acceptance artifact and real read-back evidence. It should advance to `review`, not directly to `done`, because no implementation slice or ratified production bridge was requested or safely executed.
