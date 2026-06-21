# VoxCPM2 Douyin Signal → Hermes Provider-Selection Gate

Captured: 2026-06-21T16:49:05+0800

## Verdict

Decision: **WATCH → prototype only behind explicit runtime/GPU and consent gates**.

The Douyin clip is useful as a market/product signal for creator voiceover demand, but it is not a new technical source of truth. Hermes already has two completed VoxCPM tasks. The new actionable slice is not another repo review; it is a bounded provider-selection gate for whether VoxCPM2 should become an optional Hermes TTS provider later.

## Evidence

- Douyin canonical URL: https://www.douyin.com/video/7645194197032553791
- Local analysis: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/analysis.md`
- Evidence bundle: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/evidence-bundle.json`
- Local video: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/video.mp4`
- Contact sheet: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/contact_sheet.jpg`
- GitHub verified: `OpenBMB/VoxCPM`, Apache-2.0, 31127 stars, 3507 forks, updated 2026-06-21T08:44:17Z.
- README verified: VoxCPM2 is a 2B tokenizer-free TTS model trained on 2M+ hours; supports 30 languages, Voice Design, Controllable Voice Cloning, Ultimate Cloning, 48kHz output, real-time streaming on RTX 4090 / Nano-vLLM / vLLM-Omni.

## Current Hermes context

Existing tasks:

1. `2026-06-01-技术储备评估-openbmb-voxcpm-作为-hermes-对话语音层.md`
   - Status: done.
   - Decision: WATCH reserve, not delivered feature.
   - Boundary: no install, no audio artifact, no Telegram delivery without explicit runtime/provider boundary.

2. `2026-06-07-voxcpm2-openbmb多语言-tts-与声音克隆安全边界二次评估.md`
   - Status: done.
   - Focus: voice-cloning consent/audit/risk gates.

New task created:

- `2026-06-21-douyin-ai-配音工具信号voxcpm2-provider-selection-prototype-gate.md`

## Provider-selection matrix

### A. Keep WATCH only

Pros:
- No local model download / GPU burden.
- No voice-cloning abuse surface.
- Preserves existing Hermes TTS path.

Cons:
- Does not test whether VoxCPM2 can meet the user's preferred mature Chinese LOCCENT/control-room Hermes voice.

Recommended if:
- No GPU/runtime boundary is available.
- No explicit synthetic-only/consented sample boundary is approved.

### B. Prototype synthetic-only Voice Design

Pros:
- Tests voice style design without cloning a real person.
- Aligns with user's Hermes voice preference if prompts can produce mature professional Chinese control-room cadence.
- Avoids reference-audio consent risk.

Cons:
- Requires model weight download and local runtime/GPU or cloud runtime.
- Still needs audio safety labeling and output provenance.

Required gates:
- Runtime choice: local GPU / remote GPU / no-run.
- Cost/compute estimate.
- No real-person reference audio.
- Output must be labeled synthetic.
- Provider must remain optional/off by default.

### C. Prototype controllable cloning

Rejected for now.

Reason:
- Strong identity/consent risk.
- Not needed for Hermes default voice identity.
- Would require explicit reference-audio provenance and user confirmation.

## Proposed Hermes provider contract

```yaml
tts:
  providers:
    voxcpm2:
      enabled: false
      mode: local_or_remote_explicit_only
      model_source: openbmb/VoxCPM2
      output_label: synthetic
      voice_design_allowed: true
      voice_clone_allowed: false
      require_reference_audio_consent: true
      require_audit_log: true
      max_chars_per_request: TBD_after_runtime_test
      delivery:
        telegram_voice: gated
        local_file: allowed
      red_lines:
        - no_real_person_clone_without_explicit_consent
        - no_default_on
        - no_core_dependency
        - no_external_send_without_gate
```

## Production boundary

Do **not** move this to production/default Hermes voice until all are true:

- Runtime dependency isolation exists: no PyTorch/CUDA heavy dependency in Hermes core.
- Provider can be disabled/fallback cleanly.
- Synthetic-only smoke produces real local audio artifact.
- Audio artifact is checked for duration/loudness/format.
- Telegram/media delivery proof exists or is explicitly out of scope.
- Consent/audit gate is implemented for any future reference-audio path.

## Next safe slice

The next safe implementation step is **not** voice cloning. It is a no-clone provider adapter contract + dry-run config validator:

1. Add a `voxcpm2` provider schema with `enabled=false`.
2. Add validator that rejects `voice_clone_allowed=true` unless consent metadata exists.
3. Add docs/help text and proof fixture.
4. Stop before model download/audio generation unless runtime boundary is explicitly selected.

## Current stop boundary

Boundary class: runtime/cost/identity-safety.

Reason:
- Running VoxCPM2 requires model weight/runtime selection and may require GPU/cloud resources.
- Voice cloning requires explicit consent/provenance.
- The safe local work completed here is source verification, evidence bundle, taskpool intake, and provider-selection contract.
