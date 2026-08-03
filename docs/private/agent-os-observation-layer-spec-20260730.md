# Agent OS Observation Layer — Spec Brief

Status: implementation candidate
Risk: local reversible UI/readback only

## Goal

Turn the existing `/workbench` visual prototype into a live, read-only Agent OS observation layer using the existing authenticated `GET /api/agent-os/status` response.

## Source facts

- The reference image contributes layered swimlanes, directional paths, semantic colors, and drill-down structure; it is not runtime proof.
- `/workbench` currently hardcodes nodes, edges, confidence percentages, visible-gate counts, and `risk mode=closed`.
- `/system` already consumes `GET /api/agent-os/status` and displays trusted read-only procedure validation.
- Fresh CLI readback on 2026-07-30 reported 299 projection nodes, doctor `ok=true`, 6 shadow records, 12 required procedures, 9 fully connected, 3 stale, operational health `degraded`, and certification not eligible.

## Business rules

1. Agent OS status is the owner of operational truth; the UI must not invent confidence or gate state.
2. Engineering coverage, operational health, procedure validation, and certification remain separate axes.
3. The observation surface is read-only. It may select/filter/project local explanatory text but must not invoke tools, adapters, lifecycle writes, external dispatch, deploy, credentials, payment, destructive actions, or device effects.
4. Configured capability, current authority, execution occurrence, and readback/certification are distinct concepts.
5. Missing or malformed status fails visibly as unavailable/unknown, never as healthy or closed.

## Non-goals

- No new backend execution endpoint.
- No taskpool/cron/lifecycle mutation.
- No external send, clipboard copy, download, publish, deploy, or credential handling.
- No claim of global certification or production acceptance.
- No skeuomorphic 3D construction artwork.

## Acceptance criteria

1. A pure tested adapter derives four observation layers: Intake, Governance, Systems, Evidence.
2. Live metrics replace hardcoded confidence percentages and fixed gate counts.
3. Procedure freshness and stale/failed/blocked states are represented from the status response.
4. Workbench loads `api.getAgentOsStatus()` and shows loading, unavailable, and live states honestly.
5. Selecting a node updates a read-only detail panel with state, source, and evidence/findings.
6. Existing projection command remains local-only and the UI contains no new side-effect API call.
7. Focused unit tests, web typecheck/build, and real browser interaction pass with zero console errors.

## Required evidence

- RED then GREEN test output for the observation adapter.
- Scoped diff and `git diff --check`.
- Web test, typecheck, and build output.
- Browser route load, live metrics, node selection, and console readback.
- Independent read-only review of the final scoped diff.
