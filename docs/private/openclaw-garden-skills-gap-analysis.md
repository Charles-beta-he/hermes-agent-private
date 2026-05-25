# SUPERSEDED: OpenClaw Garden Skills / awesome-openclaw-skills → Hermes gap analysis

> Correction 2026-05-21: user clarified the video likely points to `andrej-karpathy-skills`, not this OpenClaw awesome-list repo. This artifact is retained only as historical evidence for the first, incorrect source hypothesis. Use `docs/private/andrej-karpathy-skills-gap-analysis.md` as the corrected analysis.

Source inspected: https://github.com/VoltAgent/awesome-openclaw-skills
Local clone: `/tmp/awesome-openclaw-skills`
Commit inspected: `e9ec040` on `main`
Inspection date: 2026-05-21

## Live source evidence

- GitHub repo: `VoltAgent/awesome-openclaw-skills`
- Description: curated OpenClaw skills collection, 5,400+ skills filtered and categorized from OpenClaw Skills Registry.
- GitHub API snapshot: MIT license, default branch `main`, primary language `null`, stars `49080`, forks `4802`, pushed `2026-05-20T13:49:41Z`.
- README states OpenClaw is a locally-running AI assistant; skills extend capabilities and can be installed via `clawhub install <skill-slug>` or copied into `~/.openclaw/skills/` / project `skills/`.
- README security notice: skills are curated, not audited; review source before installing; risks include prompt injection, tool poisoning, hidden malware payloads, and unsafe data handling.
- The repo is an index/awesome list, not the full skill source tree. Individual skill source review must follow links to `github.com/openclaw/skills/...` or ClawHub pages before any import.

## User-supplied Garden Skills framing

The referenced video describes Garden Skills as a local/private AI skill pack stored under `~/garden/`, with no cloud requirement. Core lanes:

1. web design engineer,
2. video demo production,
3. image-generation prompt optimization,
4. local knowledge-base retrieval.

This is best interpreted as a local-first skill bundle pattern, not as a single upstream skill in the awesome list.

## Candidate clusters discovered in the index

### Web / frontend design

High-signal examples:

- `web-design-pro`: modern web design engineering, design tokens, advanced UI/UX, accessibility.
- `ant-design-skill`: React UI building with Ant Design component/layout/form/table patterns.
- `kj-ui-ux-pro-max`: UI/UX design intelligence and implementation guidance for polished interfaces.
- `landing-page-builder`: single-page landing page from prompt or design brief.
- `anti-slop-design`: production-grade frontend interfaces avoiding generic AI aesthetics.

Hermes overlap: existing `baseline-ui`, `fixing-accessibility`, `ckm:ui-styling`, `ckm:design-system`, `claude-design` already cover much of this. The missing piece is not another generic UI skill; it is a local bundle/router that selects the right UI/design skill and enforces browser-harness evidence.

### Video/demo production

High-signal examples:

- `record`: macOS CLI for recording audio, screen video/screenshot, and camera.
- `video-editor-ai`: edit MP4 by chat, add BGM/subtitles/effects, export directly.
- `youtube-factory`: script, voiceover, stock footage, captions, thumbnail from a prompt.
- `ffmpeg-video-editor`: generate FFmpeg commands from natural language.
- `captions` / `video-captions-reelwords`: transcript/caption lanes.

Hermes overlap: `media` / `youtube-content` exist, but Hermes lacks a first-class local demo-video pipeline that ties browser-harness capture → narration script → ffmpeg/remotion assembly → Telegram delivery.

### Image prompt optimization

High-signal examples:

- `visual-prompt-engine`: diverse image prompts using visual references.
- `ai-prompt-gen`: optimized prompts for ChatGPT/Midjourney/code generation.
- `image-gen`, `eachlabs-image-generation`, `comfyui-imagegen`, `skywork-design`: generation backends/patterns.

Hermes overlap: `stable-diffusion-image-generation`, `image_generate`, `ckm:banner-design`, `ckm:brand` exist. Missing piece is a reusable prompt-quality rubric and provider-neutral prompt optimizer, especially for local/privacy-safe generation workflows.

### Local knowledge base / private retrieval

High-signal examples:

- `agent-brain`: local-first persistent memory, SQLite, retrieve/extract loops, hybrid search.
- `search-memory`: local-first memory search and indexing for OpenClaw.
- `local-file-rag-basic`: high-performance local file RAG suite.
- `hk101-living-rag`: simple RAG over local text/Markdown.
- `zvec-local-rag-service`: always-on local semantic-search service with zvec + Ollama embeddings.
- `knowledge-management`: classify OpenClaw knowledge entries into local folders by content type.

Hermes overlap: Hermes already has session search, memory, taskpool, skills, and brain markdown. Gap is a bounded local `~/garden/`-style workspace profile that can index user-chosen local folders without cloud upload, preserve provenance, and keep retrieval suggestions subordinate to taskpool/user instructions.

## Hermes capability mapping

- Lane: `evolution`
- Project: `hermes-agent`
- Architecture target: `local-brain` + `skill-graph`
- Priority: `P2` initially. Valuable and aligned with long-term local assistant direction, but not currently blocking core correctness.
- Multimodel: recommended before implementation because the work changes skill routing, local data boundaries, and evidence/retrieval behavior.

## Smallest verifiable integration slice

Do not import 5k skills. Build an adapter/indexing path first:

1. Create a local source-ingest inventory for OpenClaw skill links: category, name, description, source URL, risk flags, candidate Hermes mapping.
2. Add a `garden-local-skills` umbrella/reference in Hermes only if it survives trigger/neighbor/forbidden evals; otherwise patch existing umbrellas (`creative`, `media`, `software-development`, `research`) with a reference.
3. Prototype four local-first workflows using existing Hermes capabilities:
   - web design engineer router → UI/design skills + browser-harness proof,
   - demo video producer → browser capture + script + ffmpeg/remotion proof,
   - image prompt optimizer → rubric + provider-neutral prompt template,
   - local KB retrieval → local folder index with provenance and no secret ingestion.
4. Add a security gate: never install/run third-party OpenClaw skills directly during intake; source-review first, then sandbox if needed.
5. Verify with deterministic artifacts: inventory JSON, mapping report, skill routing evals, local-only storage proof, and taskpool selfcheck/proof-check.

## Red lines / anti-drift constraints

- Do not treat the awesome list as audited source code.
- Do not install ClawHub/OpenClaw skills into Hermes automatically.
- Do not upload local `~/garden/` or brain content to cloud services.
- Do not create duplicate Hermes skills when an existing umbrella/reference patch is enough.
- Retrieval must include provenance and must not override user/developer/system instructions.
- Browser/demo validation must use real local execution artifacts, not mocked screenshots or fake videos.

## Recommended taskpool shape

Title: `Absorb OpenClaw Garden Skills patterns into Hermes local skill graph`

Acceptance evidence:

- this gap-analysis artifact,
- source inventory for relevant OpenClaw skills,
- migration decision matrix: patch existing skill vs new skill vs reject,
- one local-first prototype or reference workflow for each of the four Garden lanes,
- security/privacy gate tests or checklist,
- `brain_task_orchestrator.py selfcheck` and `proof-check` outputs.

Dependency order:

1. inventory/security triage,
2. migration matrix,
3. skill graph patch/prototype,
4. deterministic verification,
5. optional multimodel certification before broader rollout.
