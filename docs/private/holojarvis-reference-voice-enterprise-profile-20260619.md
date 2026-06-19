# HoloJarvis Reference Review → Hermes Enterprise Voice Profile

Date: 2026-06-19
Source repo: https://github.com/wqq64842-commits/holojarvis
Observed commit: 513beb0 (`docs: 仓库改名为 holojarvis（更独特、避免与海量 jarvis 重名）`)
Request: learn the open-source HoloJarvis effect and land it as Hermes-owned production-grade capability without changing Hermes interaction logic.

## Reference evidence

HoloJarvis implements a local desktop voice assistant loop:

- `jarvis/__main__.py`: microphone → ASR → wake/state handling → brain → TTS.
- `jarvis/audio.py`: sounddevice microphone capture and RMS/VAD segmentation.
- `jarvis/asr.py`: local `faster-whisper` Chinese transcription.
- `jarvis/brain.py`: OpenAI-compatible chat completions with tool calling and short spoken replies.
- `jarvis/tools.py`: local OS tools, screenshot/read-screen, WeChat UI automation, shell, trash.
- `jarvis/mcp_bridge.py`: MCP tool discovery and dispatch.
- `jarvis/tts.py`: GPT-SoVITS/system TTS fallback.
- `jarvis/pet.py`: desktop HUD/state display.

Transferable patterns:

1. Voice assistant must be an end-to-end loop, not just transcription.
2. Voice layer must expose visible state cues and TTS feedback-loop protection.
3. Local-first STT and local/private TTS options reduce data exposure.
4. Tool calls must remain governed by Hermes side-effect gates; voice must not become a privilege-escalation path.
5. Production landing should preserve current UX unless explicitly changed: Hermes already uses `/voice on`, configurable record key, VAD auto-restart, and TTS toggles.

Non-transferable / rejected as-is:

- Always-on wake word: changes interaction logic and increases privacy/safety surface.
- Bare LLM-driven `run_shell`: not acceptable as a production voice default.
- UI-automation message sending without readback/confirmation: must stay behind Hermes platform/tool gates.
- Prompt-only confirmation for dangerous actions: insufficient for enterprise production.

## Hermes landing decision

Chosen path: add a read-only enterprise voice readiness profile over existing Hermes voice mode.

Why this path wins:

- Preserves current interaction logic: `/voice on`, record key, VAD loop, `/voice tts`/config TTS.
- Produces a durable capability boundary that tests and docs can reference.
- Avoids wake-word/privacy changes and avoids weakening tool approvals.
- Lets future UI/doctor surfaces report whether Hermes is ready for a Jarvis-like production deployment.

## Implemented artifacts

- `tools/voice_enterprise_profile.py`
  - Pure readiness builder and renderer.
  - Captures required enterprise capabilities:
    - VAD conversation loop
    - local-first STT
    - spoken reply
    - TTS feedback guard
    - audible state cues
    - tool governance
    - no interaction change
  - Blocks on missing audio, STT, TTS, environment failure, and YOLO-governance weakening.
  - Warns on non-local STT, disabled auto-TTS, disabled beeps.

- `tools/voice_mode.py`
  - `check_voice_requirements()` now includes `enterprise_profile` as a derived diagnostics field.
  - This is fail-soft: profile construction errors do not break voice mode.

- `tests/tools/test_voice_enterprise_profile.py`
  - Verifies ready path, blockers, warning-only cases, YOLO blocker, and operator rendering.

## Verification commands

```bash
cd /Users/charles/.hermes/hermes-agent
./venv/bin/python -m py_compile tools/voice_enterprise_profile.py tools/voice_mode.py
./venv/bin/python -m pytest tests/tools/test_voice_enterprise_profile.py tests/tools/test_voice_mode.py -q
```

## Boundaries

Not implemented in this slice:

- Always-on wake word.
- Desktop HUD.
- New `/voice` command grammar.
- Automatic config mutation.
- Production promotion of new side-effect executors.

These are intentionally excluded to honor the user's constraint: enterprise-grade owned capability without changing interaction logic.
