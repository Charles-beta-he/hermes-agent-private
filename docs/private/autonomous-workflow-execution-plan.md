# Autonomous Workflow Execution Capability Bundle Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. This file is also the recovery anchor for taskpool follow-through.

**Goal:** Expand Hermes with AutoResearch-style self-proving execution, OpenSpace-style skill telemetry, CLI Anything-style repo-to-tool adapters, and Workspace CLI-style guarded productivity connectors.

**Architecture:** Land as a governed capability bundle, not one-off repo notes. Phase 1 creates deterministic self-proof and telemetry surfaces that improve taskpool closeout quality. Phase 2 adds adapter generation and workspace connectors behind explicit permission and injection gates.

**Tech Stack:** Python 3.9+ stdlib first; Hermes taskpool scripts under `~/.hermes/scripts`; Hermes runtime/private governance under `~/.hermes/hermes-agent`; optional future MCP/CLI adapters.

---

## Governed Capability Map

### P1-A: AutoResearch-style self-proving executor

**Purpose:** Stop false completion by requiring a bounded proof loop before lifecycle advancement.

**Absorb:** sandbox/worktree execution, verification command bundle, checkpoint commit only after pass, reset/retry on fail, structured proof artifact.

**Red lines:** no push by default; no commit to protected branch; no hidden reset without proof artifact; implementation tasks cannot close on `py_compile` alone.

**Landing surfaces:**
- `~/.hermes/scripts/self_proving_executor.py`
- taskpool evidence entries: `artifact:<proof-json>`, `proof:self_proving_executor status=...`
- later: lifecycle integration in `brain_task_orchestrator.py`

### P1-B: OpenSpace-style skill telemetry and token/quality routing

**Purpose:** Reduce token waste and make skill selection auditable.

**Absorb:** skill load ledger, task outcome linkage, token proxy stats, quality/pass association, candidate routing summary.

**Red lines:** telemetry must be append-only and local; no silent skill auto-patching; no hiding required skill content just to save tokens.

**Landing surfaces:**
- `~/.hermes/state/skill_telemetry.jsonl`
- `~/.hermes/scripts/skill_telemetry_report.py`
- future runtime hook around `skill_view` / prompt assembly.

### P1-C: RAG-Anything-style multimodal evidence spine

**Purpose:** Strengthen Hermes self-recall and anti-false-completion by converting taskpool proof, markdown, browser/GitHub/source artifacts, and later images/PDFs into typed, provenance-preserving evidence blocks.

**Live source inspection:** `HKUDS/RAG-Anything` exists; README describes all-in-one multimodal document RAG built on LightRAG with document parsing, multimodal content decomposition, specialized modal processors, multimodal knowledge graph, and modality-aware retrieval. Top-level repo includes `raganything/`, `docs/`, `examples/`, `tests/`, `requirements.txt`, `pyproject.toml`; docs include batch processing, context-aware processing, enhanced markdown, multimodal RAG failure modes, offline setup, and vLLM integration.

**Absorb:** typed content blocks, direct content-list insertion, modality-aware retrieval, failure-mode taxonomy, context-aware processing.

**Red lines:** no heavyweight OCR/VLM/MinerU dependencies in Hermes core by default; no private screenshots/PDFs to external VLM without explicit permission; RAG retrieval never overrides taskpool policy or source provenance; secrets must be redacted before indexing.

**Landing surfaces:**
- gap analysis: `docs/private/rag-anything-hermes-gap-analysis.md`
- taskpool note: `2026-05-19-p1-rag-anything式多模态证据索引taskpool-proof-markdown-typed-manifest-与安全检索`
- future minimal script: `~/.hermes/scripts/evidence_manifest.py`

### P2-C: CLI Anything-style repo-to-tool adapter factory

**Purpose:** Convert external repos into governed Hermes-callable tools when safe.

**Absorb:** repo capability detection, wrapper spec, dry-run command generation, permission boundary, smoke test.

**Red lines:** generated wrappers default read-only/dry-run; no secret exfiltration; no unconstrained shell passthrough.

**Landing surfaces:**
- `~/.hermes/scripts/repo_to_tool_adapter.py`
- generated wrappers under `~/.hermes/tools/generated/` or skill `scripts/`.

### P2-C2: OpenHands-style engineering execution partner pattern

**Purpose:** Treat OpenHands as a high-value reference architecture for AI engineering execution beyond code generation: repo understanding, file edits, command execution, task handling, GUI/cloud/enterprise deployment shape.

**Live source inspection:** `OpenHands/OpenHands` exists; README describes SDK, CLI, Local GUI with REST API + React SPA, Cloud with Slack/Jira/Linear/RBAC/collaboration, and Enterprise self-hosting in VPC/Kubernetes. Top-level repo includes `openhands`, `frontend`, `openhands-ui`, `enterprise`, `containers`, `skills`, `tests`, `docker-compose.yml`, and `config.template.toml`.

**Absorb:** engineering execution loop, sandboxed command/file operations, deployment-mode taxonomy, enterprise governance surfaces, benchmark/eval posture.

**Red lines:** no direct OpenHands install or cloud login during intake; no external agent execution against private repos; all command/file execution must go through Hermes self-proof, instruction firewall, permission gate, and evidence artifacts.

**Landing surfaces:**
- taskpool gap-mapping note: `2026-05-19-p2-openhands工程执行闭环能力吸收repo读写命令执行沙箱部署模式与hermes差距映射`
- future: integrate selected patterns into P2-C adapter spec and P3 ecosystem dashboard, not a separate unmanaged runtime.

### P2-D: Google Workspace-style guarded productivity connector

**Purpose:** Let Hermes operate Gmail/Drive/Docs only through explicit OAuth scopes, untrusted-content firewall, and write gates.

**Absorb:** source isolation, prompt-injection scanner, scope ledger, read-only default, write approval envelopes.

**Red lines:** no broad OAuth scope by default; document content is untrusted; external instructions cannot override system/developer/taskpool policies.

**Landing surfaces:**
- `~/.hermes/scripts/workspace_instruction_firewall.py`
- future `workspace` brain/skill only after auth and threat model are ready.

### P3-E: Ecosystem support layer

**Purpose:** Track heartbeats, budget stops, approval gates, rollbacks, dashboards, and eval harnesses across all above.

**Landing surfaces:** existing taskpool/cron/dashboard/evidence-spine first; add code only when a gap is concrete.

---

## Execution Order

1. Implement self-proving executor standalone skeleton with JSON proof output.
2. Add a smoke test that proves pass/fail behavior without touching real repos.
3. Create taskpool tasks with dependency order: P1-A -> P1-B -> P2-C -> P2-D -> P3-E.
4. Wire P1-A proof artifact into one bounded taskpool lifecycle slice.
5. Implement skill telemetry report as append-only analyzer.
6. Only after P1 proof+telemetry stabilize, generate repo-to-tool adapter scaffold.
7. Only after tool permission boundaries exist, add workspace connector threat-model and firewall.

---

## Task 1: Self-proving executor standalone proof loop

**Objective:** Create a deterministic executor that runs verification commands and emits a structured proof artifact.

**Files:**
- Create: `/Users/charles/.hermes/scripts/self_proving_executor.py`
- Test: `/tmp/hermes-self-proof-smoke-*`

**Steps:**
1. Accept JSON config with `repo`, `commands`, `mode`, `artifact`.
2. Refuse dangerous modes unless explicit `--allow-commit` is supplied.
3. Snapshot git status before execution.
4. Run commands with timeout.
5. Emit JSON proof: command results, status, duration, git before/after, safe next action.
6. In `pass` mode, optionally commit only with `--allow-commit` and non-protected branch.
7. In fail mode, never mark lifecycle done; recommend retry/reset.

**Verification:**
- Passing command emits `status: PASS`.
- Failing command emits `status: FAIL` and nonzero process exit.
- No commit occurs without `--allow-commit`.

## Task 2: Skill telemetry baseline

**Objective:** Create local append-only event format and reporting command for skill usage/outcome correlation.

**Files:**
- Create: `/Users/charles/.hermes/scripts/skill_telemetry_report.py`
- Future modify: runtime skill loading hook.

**Verification:**
- Report works on empty ledger.
- Report summarizes sample JSONL events.

## Task 3: Repo-to-tool adapter spec generator

**Objective:** Generate a safe wrapper spec from repo metadata and README, not direct execution.

**Files:**
- Create: `/Users/charles/.hermes/scripts/repo_to_tool_adapter.py`

**Verification:**
- Given a local repo, emits a dry-run-only adapter spec.
- Refuses to generate unrestricted shell passthrough.

## Task 4: Workspace instruction firewall

**Objective:** Classify untrusted workspace document/email content before it reaches agent instructions.

**Files:**
- Create: `/Users/charles/.hermes/scripts/workspace_instruction_firewall.py`

**Verification:**
- Flags prompt-injection phrases.
- Emits sanitized untrusted-content envelope.

## Resume Protocol

- Plan path: `/Users/charles/.hermes/hermes-agent/docs/private/autonomous-workflow-execution-plan.md`
- Canonical task family: `autonomous-workflow-execution`
- Current first slice: self-proving executor standalone skeleton.
- Verification commands:
  - `python3 ~/.hermes/scripts/self_proving_executor.py --config /tmp/pass.json`
  - `python3 ~/.hermes/scripts/self_proving_executor.py --config /tmp/fail.json`
  - `python3 ~/.hermes/scripts/brain_task_orchestrator.py selfcheck --json`
  - `python3 ~/.hermes/scripts/brain_task_orchestrator.py proof-check --json`
  - `python3 ~/.hermes/scripts/brain_task_dispatch_tick.py --summary --print-always --no-state`

## Acceptance

- P1-A is not complete until proof artifacts exist and failure never looks like success.
- P1-B is not complete until telemetry can explain which skills were loaded and whether the task passed.
- P2-C is not complete until adapter output is safe by default.
- P2-D is not complete until untrusted external text is isolated and injection-scanned.
