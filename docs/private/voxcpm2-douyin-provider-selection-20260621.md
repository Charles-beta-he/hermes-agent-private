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

Status after 2026-06-21 follow-up: **selectable only after local consent/provenance gate passes**.

Reason:
- Strong identity/consent risk remains, so C is never default-on and never external-send by default.
- The safe preparation layer is now landed as `agent.voice_clone_policy` plus `scripts/voice_clone_gate.py`.
- C becomes selectable when a manifest validates with `ok=true` and status `selectable_for_local_clone_prototype`.
- The manifest must include explicit consent, `tts_voice_clone` scope, a local consent record, regular-file reference audio, output label, purpose, and `allowed_delivery: ["local_file"]`.
- Telegram/external delivery is intentionally rejected by this gate and requires a separate send gate after a local artifact exists.

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

The next safe implementation step is **not** immediate synthesis. It is the consent/provenance gate that makes C selectable without running a clone:

1. Validate a local manifest with `scripts/voice_clone_gate.py --json <manifest>`.
2. Require `allowed_delivery: ["local_file"]` for the first clone attempt.
3. Reject Telegram/external delivery until a local artifact exists and a separate send gate is granted.
4. Keep provider disabled by default and keep PyTorch/CUDA/VoxCPM outside Hermes core.
5. Only after the gate returns `selectable_for_local_clone_prototype`, choose the runtime path for model download/GPU/cloud.

A no-clone provider adapter contract + dry-run config validator remains the safest default before model execution:

1. Add a `voxcpm2` provider schema with `enabled=false`.
2. Add validator that rejects `voice_clone_allowed=true` unless consent metadata exists.
3. Add docs/help text and proof fixture.
4. Stop before model download/audio generation unless runtime boundary is explicitly selected.

## Voice-clone readiness command

```bash
python scripts/voice_clone_gate.py --template
python scripts/voice_clone_gate.py --json /path/to/voice-clone-manifest.json
```

Template artifact:

```text
/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/voice-clone-manifest.template.json
```

## Current stop boundary

Boundary class: runtime/cost/identity-safety.

Reason:
- Running VoxCPM2 requires model weight/runtime selection and may require GPU/cloud resources.
- Voice cloning requires explicit consent/provenance.
- The safe local work completed here is source verification, evidence bundle, taskpool intake, and provider-selection contract.

## 2026-06-21 LOCCENT C provider rollout slice

User approved the normalized local LOCCENT C audio sample. The safe follow-up was implemented as a selectable Hermes command TTS provider, not as global/default activation.

Artifacts:
- Provider script: `/Users/charles/.hermes/scripts/mimo_loccent_c_tts_command.py`
- Config provider key: `tts.providers.hermes_loccent_c`
- Config backup: `/Users/charles/.hermes/config.yaml.bak-loccent-c-20260621-182755`
- Consent/provenance manifest: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/c-enabled-loccent/voice-clone-manifest.json`
- Integration readback: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/c-enabled-loccent/provider-integration-readback-20260621.md`
- Smoke output: `/Users/charles/.hermes/docs/private/douyin-ai-voice-7645194197032553791/c-enabled-loccent/provider-smoke-20260621/hermes-loccent-c-provider-smoke.m4a`

Verification:
- `py_compile` passed for provider script + voice clone gate.
- `pytest -o addopts='' tests/agent/test_voice_clone_policy.py -q` → 4 passed.
- Direct command provider smoke generated decodable AAC M4A, 24kHz mono, duration 10.5s.

Boundary:
- Default provider remains `hermes_bt_unit`; `hermes_loccent_c` is selectable/configured.
- No real-person clone, no external media delivery, no push/deploy, no VoxCPM2 model download or heavy core dependency.
