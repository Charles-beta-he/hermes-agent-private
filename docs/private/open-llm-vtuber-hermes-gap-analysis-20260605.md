# Open-LLM-VTuber → Hermes gap analysis intake (2026-06-05)

## Live source evidence

- Repository: https://github.com/Open-LLM-VTuber/Open-LLM-VTuber
- GitHub API identity: `Open-LLM-VTuber/Open-LLM-VTuber`
- Description: Talk to any LLM with hands-free voice interaction, voice interruption, and Live2D taking face running locally across platforms.
- Default branch: `main`
- Primary language: Python
- Stars/forks at intake: 9889 / 1178
- Homepage/docs: https://open-llm-vtuber.github.io/
- Last pushed at intake: 2026-05-15T07:18:04Z
- License signal: GitHub reports `NOASSERTION`; README notes Live2D sample assets have separate Live2D licensing terms.

## Source architecture signals inspected

Top-level surfaces observed via GitHub API / README:

- `src/open_llm_vtuber/server.py`: FastAPI/WebSocket server surface.
- `src/open_llm_vtuber/service_context.py`: central dependency/context assembly.
- `src/open_llm_vtuber/websocket_handler.py`: message routing and conversational state.
- `src/open_llm_vtuber/agent/`: agent factory plus stateless LLM implementations.
- `src/open_llm_vtuber/asr/`: ASR factory and backends: sherpa-onnx, FunASR, Faster-Whisper, OpenAI Whisper, Groq Whisper, Azure ASR, Whisper.cpp.
- `src/open_llm_vtuber/tts/`: TTS factory and many backends: Azure, Edge, MeloTTS, CosyVoice, GPT-SoVITS, Bark, Fish Audio, OpenAI, Piper, etc.
- `src/open_llm_vtuber/vad/`: Silero VAD, used for speech activity / interruption boundaries.
- `src/open_llm_vtuber/config_manager/`: typed configuration classes for modules.
- Desktop/web Live2D and pet-mode assets: `assets/`, `avatars/`, `live2d-models/`, `characters/`, `backgrounds/`, `web_tool/`.
- `CLAUDE.md` confirms factory-pattern modular engine system, WebSocket runtime, config templates, and development commands.

User-provided product signals also match README:

- hands-free voice interaction;
- real-time voice interruption;
- arbitrary LLM / OpenAI-compatible / local model backends;
- Live2D virtual avatar desktop companion;
- Windows/macOS/Linux support and offline/local model mode.

## Hermes current gap

Hermes already has text-first task orchestration, TTS, Telegram/media delivery, browser/vision, taskpool/event governance, and external-control/smart-home brains. The missing class-level capability is a low-latency local conversational presence layer:

1. always-on or push-to-talk microphone session with VAD-based turn detection;
2. barge-in / interrupt semantics that cancel or pause TTS and agent response cleanly;
3. WebSocket session contract for streaming ASR → LLM/tool agent → TTS/avatar events;
4. optional local desktop companion UI that is evidence-safe and never bypasses permission gates;
5. provider-agnostic LLM/ASR/TTS module registry that maps into Hermes providers/tools without storing secrets in artifacts.

## Absorbable mechanisms

1. **Voice session state machine**
   - Map Open-LLM-VTuber's ASR/VAD/TTS factories into a Hermes `voice-conversation-session` contract.
   - Required states: idle, listening, transcribing, thinking, speaking, interrupted, cancelled, completed.
   - Verification: deterministic event-log fixture for interruption and cancelled TTS.

2. **Barge-in / interruption semantics**
   - Absorb the product pattern, not necessarily code: VAD detects user speech during assistant output; TTS playback and in-flight response must be cancellable.
   - Hermes gate: interruption must not discard uncommitted side effects silently; side-effectful tool calls need explicit confirmation or audit trail.

3. **Provider-modular ASR/TTS/LLM registry**
   - Map factory-pattern module choices to Hermes configured providers rather than hardcoding keys.
   - No credentials in task notes, docs, prompt logs, or generated configs.

4. **Live2D/avatar event layer**
   - Treat Live2D as a presentation plugin: emotion/thought/speaking/listening events from Hermes runtime, not a new decision authority.
   - Avoid bundling Live2D sample assets into Hermes without license review.

5. **Cross-platform local runtime packaging**
   - Open-LLM-VTuber's uv/pixi/Docker/cross-platform setup is a useful reference for optional local desktop companion packaging.

## Red lines / anti-drift constraints

- Do not clone/install/run the external project during intake.
- Do not copy Live2D sample assets or model files into Hermes without license clearance.
- Do not create a second taskpool or autonomous planner inside a voice companion process.
- Do not let voice/avatar UX bypass Hermes permission gates, taskpool lifecycle, or side-effect confirmation.
- Do not persist microphone transcripts or screenshots into memory without explicit retention policy and redaction.
- Do not store API keys, provider tokens, or local model paths in evidence artifacts.

## Smallest verifiable integration slice

Create a Hermes voice-conversation protocol spike, not a UI clone:

1. define a JSON event contract for ASR partial/final, user_interrupt, assistant_delta, tts_started, tts_cancelled, tts_finished, avatar_expression;
2. implement a local stdlib or lightweight test harness that simulates VAD interruption while TTS is speaking;
3. prove that interrupt cancels speech and leaves side-effecting tool actions in a safe pending/confirm state;
4. map provider selection to existing Hermes config rather than new secrets.

## Taskpool shape

- Lane: `evolution`
- Project: `hermes-agent`
- Architecture target: `local-brain`
- Priority: `P2`
- Multimodel triage: advisory / architecture review before implementation
- Acceptance evidence: this intake artifact + source review + voice-session protocol fixture + proof/selfcheck before any code landing.
