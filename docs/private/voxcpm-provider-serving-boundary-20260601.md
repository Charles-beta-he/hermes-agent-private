# VoxCPM optional provider / serving / voice-loop boundary tranche

Task: `2026-06-01-技术储备评估-openbmb-voxcpm-作为-hermes-对话语音层`
Generated: 2026-06-01T01:20+08:00

## Read-back evidence

- GitHub API read-back: `OpenBMB/VoxCPM`, default branch `main`, license `Apache-2.0`, pushed at `2026-05-22T03:27:10Z`.
- Existing source-review artifact: `docs/private/voxcpm-voice-conversation-gap-analysis.md` records README/pyproject/top-level/source-tree findings and red lines.
- Multimodel advisory artifact: `/Users/charles/.hermes/tick-20260531-165536-900224-p88290-4d17ba75-380dc5c3/discussion_result.json` was PASS_ADVISORY_ONLY / CONTINUE_LOOP; it certified control-plane/task framing, not a delivered TTS feature.

## Optional provider contract sketch

`tts.providers.voxcpm` should be an optional, disabled-by-default provider. Hermes core should depend only on a small provider interface, not on VoxCPM/PyTorch/CUDA packages.

Minimum config shape:

```yaml
tts:
  providers:
    voxcpm:
      enabled: false
      mode: external_process | openai_compatible_tts | pre_generated_artifact
      endpoint: null          # required for openai_compatible_tts
      command: null           # required for external_process
      model_ref: null         # local path or service model id; never bundled in core
      voice_policy: synthetic_design_only
      reference_audio_policy: explicit_consent_required
      output_dir: ~/.hermes/artifacts/tts/voxcpm
      max_seconds: 20
      streaming: false        # can be enabled only after latency evidence
```

Provider interface boundary:

- input: text, language hint, safe synthetic voice-design prompt, optional delivery target metadata;
- output: local audio artifact path, MIME type, duration, provenance metadata, provider log path;
- failure: structured error with `install_missing`, `runtime_unavailable`, `gpu_unavailable`, `consent_missing`, or `delivery_failed` classification;
- no direct gateway send inside provider: gateway/media delivery remains a separate verification step.

## Serving boundary

Recommended serving order:

1. `pre_generated_artifact` smoke: manually generated safe sample outside Hermes core, then read-back + gateway media delivery. This proves the downstream media path before paying GPU/runtime cost.
2. `openai_compatible_tts`: Nano-vLLM/vLLM-Omni or equivalent service only after explicit GPU/service boundary is chosen. Hermes calls an endpoint; service lifecycle is outside core.
3. `external_process`: isolated subprocess wrapper only for local experiments; must enforce timeout, output-dir allowlist, and no reference-audio unless consent artifact exists.

Do not add VoxCPM imports to `run_agent.py`, gateway adapters, or core TTS tools. Runtime integration should live behind plugin/provider discovery or a separately enabled optional tool.

## Gateway voice-loop boundary

Future loop should stay staged:

1. Text response → TTS artifact generation.
2. Artifact read-back: file exists, nonzero duration/bytes, MIME verified.
3. Delivery proof: Telegram/media gateway sends that artifact or a dry-run adapter proves exact payload selection.
4. Only then connect ASR → Hermes → TTS for interactive voice conversation.

The closeout gate for this task should not accept `py_compile`, proof hashes, selfcheck, or multimodel PASS_ADVISORY_ONLY as feature delivery. It needs either:

- real audio artifact + Telegram/media delivery verification; or
- explicit WATCH/REJECT decision preserving this as technology reserve and naming the missing runtime boundary.

## Consent / safety rule

Initial VoxCPM work must use synthetic voice-design text only. Cloning a real speaker requires a separate consent/provenance artifact and must not store private reference audio in task notes, memories, or public docs.

## Current tranche decision

Decision: `WATCH`, not implementation-close.

Reason: the bounded follow-through run can define the provider/serving/governance contract and verify source identity, but it cannot truthfully produce VoxCPM audio or Telegram delivery without an explicit runtime/GPU/provider selection and consent/provenance boundary. The task may move to `review` as a documented watch/reserve artifact, but must not move to `done` as a delivered voice feature.

Next actionable slice if promoted later:

1. choose runtime path: remote GPU/OpenAI-compatible service vs isolated local subprocess;
2. generate one short synthetic Chinese sample and one short English sample;
3. verify audio bytes/MIME/duration;
4. send or dry-run exact media payload through Telegram/gateway;
5. record consent/provenance policy and rollback boundary.
