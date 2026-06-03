# Odysseus reference review → Hermes taskpool intake

Date: 2026-06-03
Target repo: `apexEvan/odysseus`
Repo URL: https://github.com/apexEvan/odysseus
Reference commit inspected: `29fa50fec94b98e6ba0805883292ce7fceca1a95`
Local inspection path: `/Users/charles/.hermes/runs/odysseus-reference-review-20260603`

## Boundary

Odysseus is a reference architecture only. Do not integrate, vendor, run privileged tools, start sidecars, copy secrets, or make it a Hermes runtime dependency during intake.

## Live source evidence

GitHub API metadata checked on 2026-06-03:

- `full_name`: `apexEvan/odysseus`
- description: self-hosted AI workspace for chat, agents, documents, research, memory, and local model management
- language: JavaScript
- license: MIT
- default branch: `main`
- stars/forks at inspection: 0/0
- clone HEAD: `29fa50fec94b98e6ba0805883292ce7fceca1a95`

Inspected surfaces:

- `README.md`: features, deployment, LLM backend compatibility, security notes, architecture sketch, data layout.
- `ROADMAP.md`: current quality gaps and help-wanted areas.
- `SECURITY.md`: privileged local-tool deployment guidance.
- `docs/DEVELOPER.md`: runtime architecture, Browser MCP opt-in resource warning, status visibility principles.
- `src/tool_security.py`: non-admin blocked tool list and MCP tool blocking.
- `src/event_bus.py`: event-triggered scheduled task handoff.
- `src/task_scheduler.py`: scheduled/background task defaults, singleflight cache, owner-scoped notifications, serial execution.
- `services/memory/skill_extractor.py`: conservative post-run skill extraction.

## Architecture signals worth absorbing

1. **Local-first workspace packaging**
   - FastAPI app + static UI + local `data/` directory + optional sidecars.
   - Explicit `scripts/bootstrap`, `scripts/start`, `scripts/status` user journey.
   - Hermes already has CLI/gateway/taskpool, but the user-facing local workspace/status surface is fragmented.

2. **Privilege-class tool gating**
   - Odysseus blocks non-admin access to shell, Python, file read/write, MCP, tokens, webhooks, app settings, email, vault, model serving, etc.
   - Hermes has permissions and toolsets, but can benefit from a generated privilege taxonomy that is visible in taskpool proof and gateway/tool routing.

3. **Heavyweight capability opt-in + status visibility**
   - Browser MCP is explicitly disabled by default because it can spawn Playwright/headless browser and consume large memory.
   - Developer notes state: “Add visibility before adding automation.”
   - Hermes should preserve this as a general control-plane invariant for browser/MCP/model-serving/sidecar/background-worker surfaces.

4. **Event-triggered housekeeping tasks**
   - Odysseus has `fire_event(event_name, owner)` that routes event triggers to scheduled tasks with counters and persistence via `next_run`.
   - This aligns with the user preference for event-driven taskpool automation over cron polling.
   - Hermes already has taskpool hooks; the useful gap is a normalized `task.created/status_changed/dependency_met` event contract with observable counters and idempotent dispatch.

5. **Scheduled task resilience and deduplication**
   - `TaskScheduler` uses a shared TTL singleflight cache for repeated data fetches and a semaphore to avoid duplicate concurrent runs.
   - Hermes workers have durable job records, but reference can strengthen a “dedupe shared fetch / in-flight run” policy for background jobs and recurring tasks.

6. **Conservative automatic skill extraction**
   - Odysseus extracts skills only after complex runs, rejects one-offs, uses a confidence floor, dedupes, and fires `skill_added` events.
   - Hermes already has stronger `skill_manage`, but this reference reinforces a governed “candidate → review → skill patch” path rather than silent skill bloat.

7. **Degraded optional service reporting**
   - Roadmap calls out degraded-state reporting for ChromaDB, SearXNG, email, ntfy, and provider probes.
   - Hermes can use the same pattern for MCP, gateway platforms, model providers, cron, taskpool workers, and sidecars.

## Hermes gaps

- No single generated “local assistant control-plane status” that maps gateway, taskpool, cron, MCP, sidecars, workers, provider probes, and optional heavy tools into one user-readable health surface.
- Tool privilege policy exists in multiple places but is not exposed as a compact, auditable taxonomy for taskpool/gateway verification.
- Event-driven taskpool architecture exists, but task lifecycle events and counters need a normalized, testable contract to avoid falling back to polling.
- Skill review is skill-based and manual/governed, but external and auto-learned skill candidates need safer first-class candidate artifacts with security/provenance metadata.

## Smallest verifiable Hermes integration slice

Create a default-off Hermes control-plane reference slice, not an Odysseus dependency:

1. Generate a local `capability_surface` inventory for Hermes:
   - tools, MCP servers, gateway platforms, cron jobs, taskpool workers, provider probes, optional sidecars.
2. Classify each capability by risk:
   - read-only, file write, shell/process, network outbound, credential/token, external message, persistent state, physical/world action.
3. Add an event-driven taskpool probe contract:
   - `task.created`, `task.status_changed`, `dependency_met`, `worker.finished`, `skill.added`, `gateway.delivery_failed`.
4. Add a status/proof artifact command or script that emits JSON and human summary.
5. Wire taskpool acceptance around deterministic proof only; no live Odysseus install/run required.

## Red lines / anti-drift constraints

- Do not import Odysseus as a dependency.
- Do not clone/run/start Odysseus during task execution except read-only reference inspection unless explicitly approved.
- Do not expose Hermes shell/MCP/file/token/message tools to lower-trust contexts.
- Do not add cron polling where an event hook can trigger directly.
- Do not auto-promote learned/external skills into active skills without source, risk, and review evidence.
- Do not treat optional service degraded state as success; report explicitly with recovery hints.

## Proposed taskpool item

Title: `Absorb Odysseus local-first control-plane patterns into Hermes taskpool governance`

Lane: `evolution`
Project: `hermes-agent`
Priority: `P2`
Architecture target: `local-brain`
Requires multimodel: yes, because this touches tool privilege taxonomy, event-driven taskpool behavior, and local assistant control-plane governance.

Acceptance evidence:

- this gap analysis artifact exists and references live source commit;
- deterministic inventory/probe script or doc artifact exists for Hermes capability surfaces;
- event-driven taskpool contract has at least one test or dry-run proof;
- privilege taxonomy maps high-risk tool classes without widening permissions;
- taskpool `selfcheck` and `proof-check` remain healthy;
- no Odysseus runtime dependency added.
