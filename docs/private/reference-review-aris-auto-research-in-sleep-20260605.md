# ReferenceReview: ARIS / Auto-claude-code-research-in-sleep

- Date: 2026-06-05T17:54:58+08:00
- Source repo: https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep
- Commit/branch surface inspected: GitHub API default branch `main`; metadata `pushed_at=2026-06-05T01:38:05Z`.
- Task: `2026-06-05-评估-aris-全自动科研工具对-hermes-研究自动化-多模型评审-技能化工作流的吸收价值`
- Scope boundary: source-review/gap-mapping only; no clone/install/run; no third-party agent execution; no external model/cloud service use.

## Evidence read-back

### Repo metadata

GitHub API read-back:

```json
{
  "full_name": "wanshuiyin/Auto-claude-code-research-in-sleep",
  "html_url": "https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep",
  "default_branch": "main",
  "pushed_at": "2026-06-05T01:38:05Z",
  "stargazers_count": 11433,
  "forks_count": 1060,
  "license": "MIT License",
  "language": "Python",
  "description": "ARIS ⚔️ (Auto-Research-In-Sleep) — Lightweight Markdown-only skills for autonomous ML research: cross-model review loops, idea discovery, and experiment automation. No framework, no lock-in — works with Claude Code, Codex, OpenClaw, or any LLM agent."
}
```

### Top-level/source surfaces inspected

Top-level contains: `AGENT_GUIDE.md`, `README.md`, `README_CN.md`, `SETUP_GUIDE*.md`, `aris-monitor/`, `docs/`, `mcp-servers/`, `skills/`, `templates/`, `tests/`, `tools/`.

Directory read-backs:

- `skills/`: many plain Markdown skill directories including `auto-review-loop`, `auto-review-loop-llm`, `auto-review-loop-minimax`, `idea-discovery`, `experiment-queue`, `experiment-bridge`, `research-*`, `meta-optimize`, `meta-apply`.
- `docs/`: `SKILLS_CATALOG.md`, `CODEX_CLAUDE_REVIEW_GUIDE.md`, `CODEX_GEMINI_REVIEW_GUIDE.md`, `MANUAL_REVIEW_GUIDE.md`, `MODEL_COMBINATIONS.md`, `PROJECT_FILES_GUIDE.md`, `SESSION_RECOVERY_GUIDE.md`, `WATCHDOG_GUIDE.md`.
- `tools/`: `evidence_check.py`, `research_wiki.py`, `run_state.py`, `watchdog.py`, literature fetchers, install/update scripts, provenance helpers.
- `tests/`: tests for review parsing, manual review integration, evidence check, provenance, run state, watchdog, install/update, skill mirror, research wiki helpers.
- `mcp-servers/`: `claude-review`, `gemini-review`, `manual-review`, `llm-chat`, `minimax-chat`, `feishu-bridge`, `codex-image2`.

### Claim-linked snippets

- README line 16: loop reports to “same cross-model jury + research wiki”; “it can drive, never acquit”.
- README lines 156-160: emphasizes autonomous research while sleeping, plain Markdown, no framework/database/daemon, cross-model collaboration.
- AGENT_GUIDE lines 5-10: ARIS is a research harness; behavior lives in `skills/<name>/SKILL.md`; system contracts live in shared references; `SKILL.md` wins conflicts.
- AGENT_GUIDE lines 92-108: five-layer cross-model audit chain; verdict schema `PASS | WARN | FAIL | BLOCKED | ERROR | NOT_APPLICABLE`; executor must not judge its own integrity; reviewer reads artifacts cold.
- AGENT_GUIDE lines 125-127: skills communicate through plain-text files in known locations.
- `docs/SKILLS_CATALOG.md` lines 27-42: workflow orchestrators include idea discovery, experiment bridge, auto-review loops, paper writing, DSE loop, meta-optimize and meta-apply.
- `skills/auto-review-loop/SKILL.md` lines 23-31 and 236: bounded max rounds, positive threshold requires score >= 6 and verdict in ready/almost.
- `skills/auto-review-loop/SKILL.md` lines 281 and 325: must not fabricate evidence; reviewer must verify author evidence claims by reading referenced files.
- `tools/evidence_check.py` lines 2-7 and 143: deterministic pre-check verifies cited evidence exists, but does not assert that a claim holds.
- `tools/research_wiki.py` lines 3-15: canonical helper for research wiki init, edge creation, query pack/index rebuild, stats/logging, paper ingestion/sync.
- `tools/research_wiki.py` lines 52-56: wiki content is re-injected into agent context and is scanned before persistence; cross-model jury remains correctness gate.
- `docs/CODEX_CLAUDE_REVIEW_GUIDE.md` lines 11-23 and 88-108: reviewer override layer over Codex-native skills; async reviewer flow and project config.
- `docs/MANUAL_REVIEW_GUIDE.md` lines 63-80: supports multi-round manual review and recovery; instructs full response paste to preserve parseable fields.

## Architecture extraction

ARIS is best understood as a lightweight research-control-plane overlay rather than a monolithic framework:

1. **Markdown skill graph**: durable behavior is encoded in per-skill `SKILL.md` contracts plus shared references. This maps well to Hermes skills/brains because contracts are explicit, text-native, and reviewable.
2. **Cross-model jury / adversarial review**: executor work is separated from reviewer judgment. ARIS explicitly states the driver can execute but cannot acquit. This maps to Hermes multimodel ratification and taskpool proof gates.
3. **Research wiki / memory spine**: papers, ideas, experiments, claims, and relationships are persisted as plain files and rebuilt into query/index packs. This maps to Hermes local-brain/evidence spine, but would need strict provenance normalization.
4. **Experiment queue and watchdog**: ARIS has ML experiment queue/watchdog primitives, useful as a reference for Hermes bounded executor proof bundles, but not directly importable without domain-specific safety gates.
5. **Meta-optimize / meta-apply split**: ARIS separates proposal generation from privileged landing. This is a strong Hermes pattern match: advisory self-optimization must not directly mutate core without SSOT/evidence/ratification.
6. **Provider/model overlays**: ARIS supports reviewer variants through MCP/server overlays and CLI skill mirrors. Hermes can absorb this as overlay-aware participant routing rather than copying provider-specific installs.

## Hermes primitive mapping

| ARIS pattern | Hermes primitive | Fit | Notes |
|---|---|---:|---|
| Markdown-only skill contracts | `skills/`, brain overlays, SSOT docs | High | Directly transferable as a governance pattern; do not import third-party skills blindly. |
| Driver cannot acquit / cross-model jury | taskpool lifecycle gates, `consult_models`, multimodel ratification | High | Reinforces existing Hermes rule: advisory PASS is not closeout evidence. |
| Evidence pre-check only proves citation existence | proof-check / evidence-spine distinction | High | Useful wording for Hermes false-positive-done prevention. |
| Research wiki | local-brain notes + evidence refs + query packs | Medium-High | Add as a pattern for claim/idea/paper graph, not a separate task pool. |
| Experiment queue/watchdog | self-proving executor proof pattern | Medium | Transfer bounded command bundle + PASS/FAIL artifact pattern; avoid ML-specific assumptions. |
| Meta-optimize vs meta-apply | Hermes self-optimization SSOT + review/done gates | High | Strong fit for safe self-evolution: proposals separate from privileged landing. |
| MCP reviewer overlays | provider/quorum/router metadata | Medium | Reuse architecture idea; implementation details are ARIS/Claude/Codex-specific. |

## Gap list / non-adoption risks

1. ARIS is research/ML-paper oriented; Hermes taskpool is broader (work tasks, cron, gateway, local-brain, project profiles). Direct skill import would overfit Hermes to paper workflows.
2. ARIS has rich external reviewer/model integration; Hermes must preserve provider/static routing rules, budget gates, OAuth/API-key boundaries, and no-unapproved expensive autorun.
3. Research wiki graph is valuable but could become a competing memory/task pool if imported naively. It must be implemented as evidence/brain overlay inside the canonical `brain_task_orchestrator.py` + `kanban.db` pool.
4. ARIS tests are useful as a proof pattern, but the review here did not clone/run tests, per task boundary. This is source-level evidence, not runtime certification.
5. README release claims are numerous; this tranche uses only claim-linked file/line evidence and does not certify all project claims.

## Minimal verifiable integration slice

Recommended first Hermes slice: **Reference-review evidence pack template + self-optimization gate wording**.

Concrete next step:

- Add or update a Hermes private governance note/skill reference that states: “executor can drive, never acquit”; deterministic evidence checks prove cited artifacts exist, not that delivery is correct; privileged self-optimization landing must be separate from proposal generation.
- Verification: read-back of the updated Hermes skill/reference; focused proof-check/selfcheck; one taskpool dry-run showing advisory source-review tasks still require implementation-specific evidence before review/done.

This avoids installing ARIS or running third-party agents while still absorbing the highest-value governance pattern.

## Decision

**ADOPT as governance/reference pattern, not as direct install.**

ARIS is valuable for Hermes in three areas: (1) Markdown skill contract graph, (2) driver-vs-reviewer separation, (3) research wiki/evidence spine and self-evolution proposal-vs-landing split. Do not auto-install ARIS skills or create a separate ARIS task pool. Use it to harden Hermes taskpool/evidence/multimodel wording and future reference-review templates.

## Closeout evidence for task

- Source metadata verified via GitHub API.
- README/AGENT_GUIDE/docs/skills/tools/tests surfaces inspected through GitHub contents/raw APIs.
- Architecture mapping, gap list, minimal integration slice, and adopt/watch/reject decision recorded in this artifact.
- Boundary respected: no clone, no install, no third-party agent run, no external model fanout.
