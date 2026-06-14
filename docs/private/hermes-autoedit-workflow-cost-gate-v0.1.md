# Hermes AutoEdit Workflow Pack + Cost Gate v0.1

Status: drafted locally for mission slice 2026-06-14; blueprint/spec only.

Source evidence read-back:
- Douyin intake note: `/Users/charles/.hermes/docs/private/douyin-davinci-agent-edit-7650479331671690548/intake-analysis.md`.
- Canonical repo facts recorded in that note: `liuluhaixiu/DaVinci-AutoEdit-Agent`, Python, MIT, README workflow `brief -> media path/topic/goal -> pacing rules -> LLM/TTS/Resolve config check -> scan media -> extract frames/review/tags -> confirm review -> script -> optional TTS -> edit blueprint -> Resolve project/timeline -> audit -> pickup-shot report`.
- Cost clue recorded in that note: demo VLM/API analysis around `$0.06`; cost grows with duration, frame sampling, VLM/TTS calls, and iterations.

## Non-executable red lines

This pack is a Workflow Pack design artifact only. It does not install or invoke DaVinci, MCP, FFmpeg, VLM, TTS, social-media downloaders, or paid APIs. It must not modify source media, write DaVinci timelines, render outputs, push code, write credentials, or contact external services without explicit later authorization.

## Stage gates

| Stage | Inputs | Output artifact | Gate |
|---|---|---|---|
| 0. Brief intake | user goal, target platform, tone, duration, source permission statement | `brief.v0` | If source rights/permission are unclear, stop at brief clarification. |
| 1. Media manifest read-only scan | local source paths or approved media references; optional hashes/duration | `media_manifest.v0` | Source paths are read-only; no downloads or rewrites in default mode. |
| 2. Cost estimate before dispatch | duration, frame cadence, VLM price table, TTS char estimate, iteration cap, write/render flags | `cost_estimate.v0` | Human budget approval token required before any billable VLM/TTS/API fanout. |
| 3. Material review | manifest plus approved non-billable metadata or cached samples | `material_review.v0` | Review must cite frame/sample/transcript evidence; missing evidence blocks claims. |
| 4. Script and TTS plan | material review, user brief, optional voice policy | `script_plan.v0`, `tts_plan.v0` | TTS defaults off; voice clone requires explicit consent and budget. |
| 5. Blueprint-only dry run | script, shot list, pacing rules | `edit_blueprint.v0`, EDL-like shot plan, pickup-shot report | Must be useful without Resolve/MCP/API writes. This is the default safe completion. |
| 6. Resolve write intent (future gated) | approved blueprint, explicit target project/timeline, write authorization | `resolve_write_intent.v0` | Requires separate human approval; API/MCP success alone is not proof. |
| 7. Resolve read-back audit (future gated) | write log, timeline read-back snapshot | `resolve_write_audit.v0` | Pass only if read-back diff matches clip count, durations, source bounds, no duplicate/gap policy violations. |

## `media_manifest.v0` shape

```json
{
  "schema": "media_manifest.v0",
  "run_id": "local-draft",
  "source_immutable": true,
  "sources": [
    {
      "source_id": "clip_001",
      "uri": "file:///approved/local/path.mp4",
      "permission_basis": "user_provided_or_explicitly_approved",
      "sha256": "required_before_paid_or_write_phase",
      "duration_seconds": 0,
      "media_type": "video|audio|image",
      "sampling_policy": {
        "mode": "sample_first",
        "fps": 0.2,
        "max_frames": 60,
        "resolution": "low_proxy_default"
      },
      "write_policy": "read_only_source_never_mutate"
    }
  ]
}
```

## `cost_estimate.v0` shape

```json
{
  "schema": "cost_estimate.v0",
  "currency": "USD",
  "budget_mode": "dry_run|low|normal|high",
  "hard_ceiling": 0.0,
  "requires_human_approval": true,
  "approval_token": null,
  "drivers": {
    "duration_seconds": 0,
    "frame_sample_count": 0,
    "vlm_calls": 0,
    "tts_characters": 0,
    "llm_iterations": 1,
    "resolve_write_enabled": false,
    "render_enabled": false
  },
  "estimate": {
    "min": 0.0,
    "expected": 0.0,
    "max": 0.0,
    "unknown_price_table_fields": []
  },
  "fail_closed_conditions": [
    "missing price table for a billable adapter",
    "expected or max exceeds hard ceiling",
    "approval_token absent for billable dispatch",
    "write/render flag true without explicit write authorization"
  ]
}
```

## `edit_blueprint.v0` safe fallback

The blueprint-only mode outputs:
- ordered shot list with source IDs/time ranges;
- script/caption draft grounded in material review IDs;
- B-roll and music placeholders;
- optional TTS placeholders, not generated audio;
- pickup/补拍 report listing missing shots, unclear narration, or quality gaps;
- Resolve export hints, but no MCP/API call and no timeline write.

## Future `resolve_write_audit.v0` contract

A future write phase must persist:
1. `resolve_write_intent.v0`: target project/timeline, expected clip count, expected duration, source bounds, authorization token.
2. Adapter response log: MCP/API method, status, identifiers, stderr/stdout paths.
3. Timeline read-back snapshot: actual clip list, time ranges, durations, media IDs, subtitles/audio tracks.
4. Deterministic diff verdict: `PASS` only when expected and actual timeline shape match within tolerance; otherwise `FAIL_CLOSED` with rollback/manual repair recommendation.

## Verification checklist for this slice

- [x] Evidence note read back locally.
- [x] README workflow facts are represented from the evidence note.
- [x] Cost gate precedes billable dispatch and includes duration, frame sampling, VLM, TTS, iterations, spend ceiling, approval token.
- [x] Blueprint-only default does not require DaVinci/MCP/paid APIs/source writes/rendering.
- [x] Resolve write audit requires read-back, not adapter success only.
- [x] Remaining implementation is blocked on explicit budget/write authorization.
