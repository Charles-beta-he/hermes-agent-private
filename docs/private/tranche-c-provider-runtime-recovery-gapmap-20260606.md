# Tranche C — Provider/runtime recovery scoped gap map (2026-06-06)

## Scope

Port only the small provider/runtime recovery gaps still valuable after the upstream realignment. Do not replay the old private branch wholesale.

## Findings

### Codex auxiliary null-output salvage

Current `private/main` already uses the newer event-driven Codex Responses stream consumer in `agent/codex_runtime.py`, and `agent/auxiliary_client.py` routes Codex auxiliary streams through that consumer. Existing tests cover terminal events with null output and final-output-none salvage. No Tranche C code port needed.

### Cron legacy schedule recovery

Gap remained: legacy jobs could persist interval schedules as `{"kind":"cron","expr":"every 180m"}`. Current `croniter` handling treats that as invalid cron and can stall due-job scanning/recovery. Scoped fix: repair only this narrow legacy shape into `{"kind":"interval","minutes":180,"display":"every 180m"}` before computing due jobs / next run.

Explicitly not ported: old missing-id auto-assignment. Current id immutability and output path safety posture should remain fail-closed unless separately reviewed.

### Gemini OAuth / CloudCode overlay

Gaps remained:

- Gemini CloudCode tool result messages lacking `name` used `tool_call_id` as `functionResponse.name` instead of mapping back to the assistant tool call function name.
- Consecutive tool/function result messages were emitted as separate Gemini user turns instead of batching into one turn with multiple `functionResponse` parts.
- The `/model` picker did not include OAuth/external-process overlays such as `google-gemini-cli` when provider-specific auth status reported logged-in but env/auth-store/pool checks were empty.

## Implemented files

- `cron/jobs.py`
- `tests/cron/test_jobs.py`
- `agent/gemini_cloudcode_adapter.py`
- `tests/agent/test_gemini_cloudcode.py`
- `hermes_cli/model_switch.py`
- `tests/hermes_cli/test_gemini_oauth_in_model_picker.py`

## Verification targets

- Cron legacy schedule recovery tests
- Gemini CloudCode tool result mapping/batching tests
- Gemini OAuth model picker test
- Existing Codex null-output / auxiliary recovery tests to ensure no regression
- `git diff --check`, static scan, post-update-check, independent review
