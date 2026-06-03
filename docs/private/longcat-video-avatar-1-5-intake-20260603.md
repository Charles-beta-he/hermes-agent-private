# LongCat-Video-Avatar 1.5 → Hermes media/video generation intake (2026-06-03)

## Source identity

- Project page repo: https://github.com/MeiGen-AI/LongCat-Video-Avatar-1.5-Page
- Project page inspected commit: `b4bc0daacb7385e9cc872a0cbedf624aab09c746`
- Official code repo: https://github.com/meituan-longcat/LongCat-Video
- Code repo inspected commit: `6b3f4b8582a8bc3f20f795735f5383716c4ba794`
- Model weights: https://huggingface.co/meituan-longcat/LongCat-Video-Avatar-1.5
- ModelScope mirror: https://www.modelscope.cn/models/meituan-longcat/LongCat-Video-Avatar-1.5
- Technique report: project page links arXiv `2605.26486`; code repo also links `assets/LongCat-Video-Avatar-1.5-Tech-Report.pdf`
- License: MIT in both inspected GitHub repositories

## User-provided summary

LongCat-Video-Avatar 1.5 can generate synchronized talking-avatar/digital-human video from a photo and audio.

## Live source evidence inspected

### Project page / README signals

The project page README states that LongCat-Video-Avatar 1.5 is an upgraded open-source framework for audio-driven human video generation. Claimed changes/features:

- Replaces Wav2Vec2 with Whisper-Large for smoother/natural lip dynamics.
- Production-ready physical rationality, temporal stability, long-video generation, and identity consistency.
- Generalizes to stylized domains: anime, animals, multi-person interactions, object handling, complex real-world conditions.
- Supports single-stream and multi-stream audio inputs.
- Uses DMD2-style step distillation to accelerate inference to 8 steps.
- Human evaluation benchmark covers 6 application scenarios, 2 languages, 2 visual styles, 508 image-audio pairs, 770 crowd evaluators, 13,240 judgments, plus 10 expert raters across physical rationality, harmony, temporal stability, identity consistency.

### Official code repo signals

The official code repo has ~4k GitHub stars at intake and includes:

- `requirements.txt`: PyTorch 2.6, diffusers, transformers, flash-attn, OpenCV, Streamlit, imageio/ffmpeg.
- `requirements_avatar.txt`: audio-processing stack including librosa, soundfile, soxr, audio-separator, onnx/onnxruntime, pyloudnorm.
- `run_demo_avatar_single_audio_to_video.py`: single-photo/single-audio generation path.
  - Reads JSON input fields: `prompt`, `cond_image`, `cond_audio.person1`.
  - Loads image with `diffusers.utils.load_image`.
  - Extracts vocals with `audio_separator` when possible; falls back to raw audio on failure.
  - v1.5 path uses `whisper-large-v3` audio encoder; older path uses `chinese-wav2vec2-base`.
  - Supports 480×832 or 768×1280 generation, save FPS 16 or 25 depending version.
  - Saves generated video with original/raw speech via ffmpeg.
  - Supports INT8 quantized DiT and DMD distillation path.
- `run_demo_avatar_multi_audio_to_video.py`: two-speaker / multi-audio path.
  - Reads `cond_audio.person1`, `cond_audio.person2`, `bbox`/speaker regions, and optional background silent audio.
  - Builds left/right audio embeddings and speaker masks.
- `longcat_video/pipeline_longcat_video_avatar.py`: core avatar pipeline.
  - Accepts image or video conditioning plus prompt/negative prompt.
  - `get_audio_embedding()` dispatches to Wav2Vec2 or Whisper; Whisper path creates `[T, 5, D]` embeddings by chunking Whisper encoder hidden states.
  - Audio classifier-free guidance combines text and audio guidance.
  - Supports continuation/long-video generation with reference image and mask handling.

## Hermes capability mapping

### What this enables conceptually

LongCat-Video-Avatar 1.5 is a candidate reference/model backend for **photo + audio → talking digital-human video**. For Hermes, the useful product capability is not only local inference. It is a governed media-generation workflow:

1. accept photo/audio/prompt;
2. validate consent, identity, and media safety;
3. choose local/remote backend based on GPU/model availability;
4. run or enqueue generation;
5. return video artifact through Telegram/gateway;
6. persist provenance/evidence and safety metadata.

### Hermes landing zones

- Tool surface: video/media generation tool or plugin, not core agent loop.
- Gateway delivery: native Telegram video attachment via `MEDIA:/path/to/video.mp4`.
- Runtime control: long-running process/taskpool, not foreground chat loop.
- MLOps: GPU backend selection, model download/cache, quantized/INT8 path, remote GPU fallback.
- Safety/governance: explicit consent/identity/deepfake policy gate before generation.
- Evidence/provenance: record input hashes, model repo/commit/weights version, prompt, output path, and safety approval.

## Gap assessment

### Gap 1 — Hermes media generation mostly handles images/TTS, not governed avatar video

Hermes has image generation and TTS tools, and can send videos as media attachments, but there is no first-class governed `photo + audio → talking-avatar video` workflow.

Impact: user-facing media capability gap; useful for product demos,口播, avatar explainers, and Telegram-delivered generated clips.

### Gap 2 — LongCat is heavyweight GPU inference, not a safe default local install

The official repo depends on PyTorch 2.6, flash-attn, CUDA-oriented multi-GPU logic, audio separation, Whisper-Large, and large model weights. This is not suitable as a default Hermes dependency.

Impact: initial integration should be a **backend adapter/evaluation task**, not an install/run task.

### Gap 3 — Deepfake/consent boundary must be explicit

Photo+audio avatar generation can impersonate real people. Hermes must not expose it as a generic tool without safety constraints.

Impact: task acceptance must include consent/ownership policy, watermark/provenance recommendation, and refusal paths.

### Gap 4 — Long-running generation lifecycle

Avatar generation is long-running and GPU-bound. It must use taskpool/background process semantics with progress/error/status, not a synchronous chat-turn tool.

Impact: pair with process lifecycle control-plane and taskpool evidence gates.

## Smallest verifiable integration slice

Do **not** install/run LongCat in first pass. First task should produce a source-grounded feasibility and adapter plan:

1. Verify official repo, project page, weights, and license.
2. Map exact input/output contract from demo scripts:
   - JSON input schema: prompt, cond_image, cond_audio, resolution, inference steps, guidance, speaker bbox for multi-audio.
   - Output artifact: mp4 generated via ffmpeg with original/merged audio.
3. Define a Hermes `video_avatar_generate` backend contract:
   - inputs: image path, audio path(s), text prompt, consent flag, optional speaker regions, resolution, duration cap;
   - outputs: media path, metadata/provenance JSON, model/backend identity;
   - lifecycle: queued/running/progress/done/failed;
   - safety: consent + deepfake policy gate.
4. Decide backend options:
   - local CUDA host only when available;
   - remote GPU/Modal-style execution for heavy runs;
   - HF/Space/API if an official hosted endpoint exists;
   - no default install into Hermes core.
5. Produce one prototype candidate only after feasibility:
   - no-model dry-run adapter that validates schema and emits expected command plan; or
   - remote-GPU wrapper spike with explicit user approval.

## Red lines / anti-drift

- Do not download model weights or run CUDA inference during intake.
- Do not add PyTorch/flash-attn/audio-separator as Hermes core dependencies.
- Do not generate avatar videos of real people without explicit consent/authorization.
- Do not hide provenance; generated output must carry model/source metadata.
- Do not block Hermes main loop on long video generation; use taskpool/background process semantics.
- Do not treat unofficial WebUIs/wrappers as authoritative over the official repo and model card.

## Recommended taskpool classification

- Lane: `evolution`
- Project: `hermes-agent`
- Architecture target: `local-brain`
- Priority: `P2`
- Multimodel triage: `advisory`
- Requires multimodel later: yes if implementing consent policy or runtime video-generation tool
- Parallel boundary: source verification + feasibility + adapter contract only; no model download/inference in first task

## Acceptance evidence for task closeout

- Source evidence from official project page/code repo/model card recorded.
- Input/output schema extracted from demo scripts.
- Hermes adapter contract written with lifecycle/safety/provenance fields.
- Backend strategy chosen: local GPU vs remote GPU vs hosted endpoint vs reject/watch.
- If implementation follows: no core dependency pollution; tests cover schema validation and consent gate.
