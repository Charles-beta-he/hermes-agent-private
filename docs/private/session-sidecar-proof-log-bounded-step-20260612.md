# Session sidecar / Proof Log bounded step — 2026-06-12

## Objective model

- Success criteria
  - A real Hermes runtime path writes a durable session sidecar/Proof Log evidence object.
  - The emitted evidence is consumable by existing selfcheck / finding machinery.
  - Focused tests verify the behavior.
- Constraints / non-goals
  - Bounded step only; do not wire every entrypoint in one patch.
  - Reuse existing `session_sidecar` + `agent.selfcheck_operator` seams.
  - Preserve redaction-safe behavior and avoid hot-path broadening.
- Current state before this patch
  - `session_sidecar/` already defines schema, producers, aggregation, report bridges, and tests.
  - `observe_cron_job_result(...)` already writes selfcheck findings for cron outcomes.
  - No runtime path was writing a sidecar for actual cron executions yet.
  - `sidecar_evidence_for_selfcheck(date_prefix=...)` computed a prefix but did not actually apply it to emitted proof refs.
- Viable paths considered
  1. Wire all browser/cron/script entrypoints now — too broad.
  2. Docs-only reconciliation — no acceptance progress.
  3. Cron-first bounded runtime integration, then feed sidecar refs into selfcheck — chosen.

## Chosen bounded step

Implement cron-first sidecar emission in `cron/scheduler.py` and thread the resulting sidecar evidence refs into `observe_cron_job_result(...)` so Proof Log evidence shows up in both stored sidecar files and selfcheck findings.

## Scope landed

- Added `_emit_cron_session_sidecar(...)` in `cron/scheduler.py`.
- Scheduler now emits a sidecar after each cron run is saved/delivery-evaluated, including:
  - run status (`succeeded` / `degraded` / `failed`)
  - cron job metadata
  - output artifact ref
  - no-agent/script metadata when present
  - `proof:session-sidecar:run:<run_id>` evidence ref
- `observe_cron_job_result(...)` now accepts optional sidecar metadata/evidence refs and records them into selfcheck evidence.
- `session_sidecar.consumer.sidecar_evidence_for_selfcheck(...)` now correctly applies the `date_prefix` to emitted proof refs.

## Why this is the right next step

This patch advances the mainline architecture without inventing a parallel Proof Log system: it proves the existing sidecar schema can be produced by a live runtime chain, then consumed by selfcheck as durable evidence. Cron is the lowest-risk first chain because it already had an explicit observation seam and stable output persistence.

## Follow-on candidates

- Browser automation runtime emission using the same sidecar schema.
- Standalone script path emission outside cron.
- User-facing docs once more than one runtime entrypoint is wired.
