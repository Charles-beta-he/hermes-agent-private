# VoxCPM 技术储备：与 Hermes 对话的本地高质量语音层

## Source evidence

- Repo: https://github.com/OpenBMB/VoxCPM
- Confirmed identity: `OpenBMB/VoxCPM`
- Description: `VoxCPM2: Tokenizer-Free TTS for Multilingual Speech Generation, Creative Voice Design, and True-to-Life Cloning`
- Language: Python
- License: Apache-2.0
- Default branch: `main`
- Observed repo surface: `README.md`, `README_zh.md`, `app.py`, `lora_ft_webui.py`, `conf/voxcpm_v2`, `src/voxcpm/{core.py,cli.py,model,modules,training,utils}`, `scripts/*finetune*`, `tests/*`.
- README capabilities: tokenizer-free TTS, diffusion autoregressive architecture, VoxCPM2 2B, 30 languages, voice design from text, controllable voice cloning, ultimate cloning, 48kHz output, real-time streaming with Nano-vLLM / vLLM-Omni, Python API, CLI, Gradio demo, serving paths.

## Hermes capability mapping

Target capability: make “one day I can talk with you” realistic as a production-grade local/edge voice layer, not just a one-off TTS demo.

Potential Hermes landing surfaces:

1. `tts` tool/provider abstraction
   - Evaluate VoxCPM as an optional TTS provider for Chinese/multilingual high-quality voice output.
   - Keep provider optional; do not add heavyweight GPU/model dependencies to Hermes core.

2. Gateway voice loop
   - Telegram voice bubbles already support native audio delivery via Hermes TTS output paths.
   - Future slice: user speech input → ASR/transcript → Hermes agent → VoxCPM response audio.

3. Local/edge runtime profile
   - VoxCPM likely needs GPU / CUDA for realistic latency; macOS local CPU should be treated as unsuitable until benchmarked.
   - Serving candidates: Nano-vLLM / vLLM-Omni OpenAI-compatible TTS server, or isolated subprocess provider.

4. Voice identity / cloning governance
   - Voice cloning and “ultimate cloning” require explicit consent, provenance, and audit controls.
   - The first Hermes integration should favor voice design or a non-sensitive configured voice, not cloning a real person by default.

## Current Hermes gap

Hermes has a text-to-speech tool, but the durable product gap is conversational voice experience:

- provider contract for long-form Chinese/multilingual voice quality;
- latency / streaming evidence under actual Telegram or gateway flow;
- model/runtime isolation so GPU dependencies do not pollute Hermes core;
- consent and audit policy for cloned/reference voices;
- acceptance proof that audio is actually generated and delivered via the gateway.

## Smallest verifiable integration slice

1. Source-review VoxCPM architecture and serving modes.
2. Define an optional `tts.providers.voxcpm` contract shape without installing the repo into Hermes core.
3. Benchmark one isolated runtime path:
   - either existing VoxCPM CLI/Python API in a disposable run directory;
   - or OpenAI-compatible serving via vLLM-Omni/Nano-vLLM if GPU is available.
4. Generate one short Chinese and one English sample audio from a safe synthetic voice design prompt.
5. Verify Hermes can deliver the generated audio through the current TTS/media gateway path.

## Red lines / anti-drift

- Do not auto-install or run VoxCPM during intake.
- Do not clone real voices without explicit user-provided consent and source provenance.
- Do not store private reference audio or transcripts in task notes, memories, or public docs.
- Do not add PyTorch/CUDA/VoxCPM dependencies to Hermes core unless isolated behind an optional provider/runtime boundary.
- Do not call the task “done” from repo inspection alone; done requires audio artifact + delivery proof + policy note.

## Taskpool shape

Lane: `evolution`
Priority: `P2`
Project: `hermes-agent`
Architecture target: `local-brain`
Parallel boundary: voice/TTS provider reserve only; no automatic install, no voice cloning, no core dependency change during intake.
Acceptance: repo inspection proof, this gap-analysis artifact, optional provider contract proposal, benchmark/delivery artifact before implementation closeout.
