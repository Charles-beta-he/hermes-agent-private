# RAG-Anything → Hermes Self-Strengthening Gap Analysis

Source: https://github.com/HKUDS/RAG-Anything
Inspection timestamp: 2026-05-19

## Live source evidence

- Repository: `HKUDS/RAG-Anything`
- Description: `RAG-Anything: All-in-One RAG Framework`
- License: MIT
- Language: Python
- Stars/Forks at intake: 20362 / 2347
- Default branch: `main`
- Top-level surfaces: `raganything/`, `docs/`, `examples/`, `tests/`, `requirements.txt`, `pyproject.toml`, `env.example`, `README_zh.md`.
- Important modules observed: `raganything.py`, `parser.py`, `processor.py`, `modalprocessors.py`, `query.py`, `batch.py`, `batch_parser.py`, `callbacks.py`, `resilience.py`, `prompt_manager.py`, `enhanced_markdown.py`.
- Docs observed: `batch_processing.md`, `context_aware_processing.md`, `enhanced_markdown.md`, `multimodal_rag_failure_modes.md`, `offline_setup.md`, `vllm_integration.md`.
- Tests observed: parser wiring, resilience, callbacks, content-list alias handling, doc status creation, LightRAG API integration, prompt language, OCR/parser tests.

## Core architecture signals

RAG-Anything is not just another text retriever. It extends RAG into a multimodal document intelligence pipeline:

1. **Document parsing stage**
   - Handles PDFs, Office documents, images, and heterogeneous file formats.
   - Uses MinerU and parser alternatives such as Docling/PaddleOCR.
   - Preserves structure/layout before semantic insertion.

2. **Multi-modal content decomposition**
   - Separates text, images, tables, equations, charts and other elements.
   - Supports direct pre-parsed content list insertion.

3. **Specialized modal processors**
   - Dedicated processors for images, tables, math formulas, and mixed content.
   - VLM-enhanced query mode for documents that contain images.

4. **Multimodal knowledge graph on LightRAG**
   - Extends LightRAG-style graph/entity relationship indexing with cross-modal relationships.
   - Bridges textual facts and visual/structured/math elements.

5. **Modality-aware retrieval**
   - Query can span textual and non-textual content.
   - Supports hybrid retrieval and contextual processing.

## Hermes current gap

Hermes already has memory, skills, taskpool, browser/file/terminal tools, cron and evidence governance. The missing layer is a **governed multimodal knowledge ingestion spine**:

- Current brain/taskpool records mostly text notes and tool outputs.
- Screenshots, PDFs, diagrams, tables, Lark/exported docs, browser captures and codebase architecture diagrams do not become first-class indexed knowledge.
- Existing proof artifacts are structured, but not semantically retrievable across modalities.
- Taskpool can store evidence paths, but it cannot ask: "which diagram/table/screenshot explains this blocker?"
- AIHOT/repo/doc ingestion can create tasks, but not a reusable multimodal RAG corpus.

## Capability mapping

### 1. Multimodal Evidence Spine

RAG-Anything pattern:
- document parsing → element decomposition → modal processors → graph retrieval.

Hermes landing:
- Create a local `evidence_ingest` layer for taskpool artifacts.
- Normalize evidence into typed blocks:
  - `text`
  - `image`
  - `table`
  - `code`
  - `diagram`
  - `pdf_page`
  - `browser_snapshot`
  - `proof_json`
- Store a manifest next to each task or in `~/.hermes/state/evidence_index.jsonl`.

Minimal slice:
- Ingest Markdown + JSON proof artifacts first.
- Add image/PDF later after security and dependency review.

### 2. Context-Aware Task Recall

RAG-Anything pattern:
- context configuration module and modality-aware query.

Hermes landing:
- When dispatching a task, select relevant prior evidence by type, recency, and confidence.
- Use this to avoid repeated false starts and stale memories.

Minimal slice:
- Build a deterministic local evidence manifest/report first; no vector DB required.
- Later add embeddings or LightRAG/MemOS bridge only after proof of value.

### 3. Failure Mode Knowledge Base

RAG-Anything has explicit docs for multimodal RAG failure modes.

Hermes landing:
- Convert repeated taskpool failures into searchable failure-mode records:
  - symptom
  - root cause
  - affected subsystem
  - proof artifact
  - recovery action
- This complements existing skill patching and memory constraints.

Minimal slice:
- Extend existing failure taxonomy jobs to emit typed records compatible with the evidence manifest.

### 4. Direct Content List Insertion

RAG-Anything pattern:
- bypass full parsing when external systems already provide structured content lists.

Hermes landing:
- Browser snapshots, Lark task exports, GitHub API responses, taskpool proof JSON, and AIHOT daily data are already structured.
- Do not reparse them as raw text; insert them as typed content blocks with source metadata.

Minimal slice:
- `evidence_ingest.py add --type proof_json|browser_snapshot|github_repo --source ...`.

## Recommended priority

P1 if framed as **taskpool anti-false-completion / evidence recall infrastructure**.
P2 if framed as broad multimodal RAG research.

For this self-strengthening round: classify as **P1/P2 boundary, start P1 slice** because it directly strengthens Hermes evidence-first and false-completion prevention.

## Red lines

- Do not install heavyweight OCR/VLM/MinerU dependencies into Hermes core by default.
- Do not send private local screenshots/docs to external VLM without explicit permission.
- Do not treat embeddings as ground truth; keep source artifact paths and typed provenance.
- Do not make RAG retrieval override taskpool policy, memory gates, or user instructions.
- Do not ingest secrets; redact tokens/API keys before indexing.

## Smallest verifiable integration slice

Create a deterministic, dependency-light `evidence_manifest` prototype:

1. Scan a bounded set of taskpool artifacts and proof JSON files.
2. Emit JSONL typed content blocks with:
   - `source_path`
   - `source_type`
   - `task_id`
   - `content_type`
   - `summary`
   - `hash`
   - `created_at`
   - `redaction_status`
3. Provide `query`/`report` mode using lexical search first.
4. Verify:
   - handles empty corpus
   - indexes proof JSON and markdown task notes
   - refuses/flags likely secrets
   - selfcheck/proof-check/tick remain PASS

## Follow-up task shape

Title: `P1 RAG-Anything式多模态证据索引：taskpool proof/markdown typed manifest 与安全检索`

Acceptance:
- live repo evidence attached
- gap matrix attached
- deterministic prototype exists
- no heavyweight multimodal deps in core
- secret redaction gate present
- dispatch task can cite typed evidence block IDs
