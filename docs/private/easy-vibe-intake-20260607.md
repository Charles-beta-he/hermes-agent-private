# EasyVibe intake note (2026-06-07) — Hermes skill-graph absorption assessment

Status: evidence-grounded intake only; no third-party code installed or executed.
Source repo: https://github.com/datawhalechina/easy-vibe
Reviewed checkout: `/tmp/agent-team-2026-06-07-easyvibe-1f4e62749ffd/easy-vibe`
Reviewed commit: `12cddfe15f7763df78827cc8be9872c2b0377e4c`
Evidence summary: `/tmp/agent-team-2026-06-07-easyvibe-1f4e62749ffd/evidence/easy-vibe-evidence-summary.json`

## 1. Source identity and provenance

| Check | Evidence | Assessment |
|---|---|---|
| Repository identity | Git remote `https://github.com/datawhalechina/easy-vibe.git`; GitHub API `full_name=datawhalechina/easy-vibe`, id `1124249918` | Source identity confirmed. |
| Revision pinned | `git rev-parse HEAD` -> `12cddfe15f7763df78827cc8be9872c2b0377e4c` | Intake is reproducible against this commit. |
| Activity and popularity | GitHub API: default branch `main`, pushed `2026-06-03T05:40:18Z`, language `JavaScript`, stars `16443`, forks `1547` at intake | Active education repo; popularity is signal, not proof of quality. |
| Topic surface | GitHub topics include `agent`, `ai`, `mcp`, `nextjs`, `no-code`, `tutorial`, `vibe-coding`, `workflow`, `vscode` | Relevant to Hermes skill-graph/workflow research. |
| Project shape | Root has `README.md`, `AGENTS.md`, `CLAUDE.md`, `llms.txt`, `docs/`, `docs-readme/`, `package.json` | Documentation/course project, not a directly importable skill library. |

## 2. License, provenance, and security gate

Gate decision: ADOPT_PATTERNS_ONLY_WITH_ATTRIBUTION; DO_NOT_COPY_CONTENT.

| Gate | Evidence | Risk | Decision |
|---|---|---|---|
| Programmatic GitHub license | GitHub API `license=null`; no root `LICENSE`/`LICENSE.md` found in checkout | GitHub cannot validate license file; README/package still declare license | Treat as licensed but not license-file clean. |
| Declared license | `package.json` line 29: `CC-BY-NC-SA-4.0`; README lines 41 and 431-445 show CC BY-NC-SA 4.0 badge/text | Non-commercial/share-alike license is incompatible with verbatim reuse in Hermes commercial contexts | Do not copy tutorial text, code, diagrams, or templates into Hermes. Abstract only. |
| Third-party execution | `package.json` scripts include VitePress build/dev/test/lint/verify; dependencies include Vue/VitePress/Element Plus/Mermaid/etc. | Running would execute unreviewed Node dependency tree | Not run. Read-only git/API/file inspection only. |
| Agent-local config | Root has `.trae`, `.vscode`, `AGENTS.md`, `CLAUDE.md`, `llms.txt` | Tool-specific assumptions may not apply to Hermes | Use as packaging pattern only, not command source. |
| Private data boundary | Evidence gathered locally under `/tmp/...`; no private brain/project files uploaded | Safe | No red-line breach observed. |

Security note: absence of a root LICENSE file plus a non-commercial README/package declaration means the safe route is stricter than ordinary attribution: no copied prose/code/assets; only high-level patterns and independently written Hermes artifacts.

## 3. README / docs / llms / AGENTS / CLAUDE evidence

| Artifact | Observed evidence | Hermes relevance | Absorption boundary |
|---|---|---|---|
| `README.md` | 31,576 bytes. Headings include learning paths, study suggestions, content navigation, beginner/full-stack/advanced developer sections, run locally, license. Lines 208-210 point to advanced Claude Code/agent workflows; lines 317-326 list MCP, Skills, long-running tasks, agent teams, spec coding. | Strong human-facing curriculum map and learning-path front door. | Recreate routing concepts in Hermes words; do not copy tables/text. |
| `AGENTS.md` | 2,200 bytes. Sections: project structure, build/test commands, coding style, testing, commits/PRs, config/deployment notes. | Compact repo-specific agent contract pattern. | Hermes already uses AGENTS.md; can borrow the idea of short operator contract + exact commands, not content. |
| `CLAUDE.md` | 12,222 bytes. Sections cover project overview, development commands, architecture, sidebar/content conventions, interactive components, multi-language support, permissions, key context. | Medium/high value as “deep agent guide” layer complementary to short AGENTS.md. | Tool name is Claude-specific; translate to Hermes “developer guide” pattern. |
| `llms.txt` | 51,093 bytes. Starts as AI navigation; includes high-level architecture, quick decision tree, directory quick reference, detailed article index, stage routing. | High value as LLM-readable index/graph snapshot. | Too large to load wholesale; use generated, bounded, evidence-linked indices for Hermes skill graph. |
| `docs/` | 10 locale directories plus `.vitepress`, `assets`, `public`; supported content locales observed: `ar-sa`, `de-de`, `en`, `es-es`, `fr-fr`, `ja-jp`, `ko-kr`, `vi-vn`, `zh-cn`, `zh-tw`. Stage folders exist for zh-cn/en/zh-tw. | Good model for localized curriculum tree and per-stage navigation. | Do not import VitePress stack; Docusaurus/Markdown conventions remain Hermes source of truth. |
| `docs-readme/` | 10 translated README directories: `ar-SA`, `de-DE`, `en-US`, `es-ES`, `fr-FR`, `ja-JP`, `ko-KR`, `vi-VN`, `zh-CN`, `zh-TW`. | Useful for localized landing pages and top-level docs summaries. | Adopt structure only if Hermes has an i18n owner/gate. |
| `package.json` | VitePress/Vue documentation project, `type=module`, Node `>=18`, scripts for dev/build/test/lint/verify/sitemap, license `CC-BY-NC-SA-4.0`. | Confirms repo is documentation/tutorial infrastructure, not a Hermes-compatible runtime package. | Do not run or vendor dependencies for this task. |

Correction from preliminary note: the checkout has 10 content locales; the count of 13 under `docs/` includes `.vitepress`, `assets`, and `public`, which are not languages.

## 4. Tutorial / workflow cluster matrix

| Cluster | Evidence in EasyVibe | Workflow implied | Hermes skill-graph mapping | Adoptability |
|---|---|---|---|---|
| Stage 0 / fast first win | README learning path “fast first win”; `docs/*/stage-0` | beginner motivation and first AI coding exposure | Optional onboarding examples only | Low. Too basic for Hermes engineering users. |
| Stage 1 / product-prototype | README learning path “turn an idea into a product prototype”; stage tree: `learning-map`, `introduction-to-ai-ide`, `finding-great-idea`, `building-prototype`, `integrating-ai-capabilities`, `complete-project-practice`, product/idea/mom-test/JTBD appendices | idea discovery -> prototype -> AI capability -> complete practice | `goal-calibration`, `ideation`, `writing-plans`, `workflow-orchestrator`, beginner skill-authoring path | High as a decomposition pattern. |
| Stage 2 / full-stack delivery | README full-stack path; stage tree has `frontend`, `backend`, `ai-capabilities`, `assignments`; README lines 272-305 cover frontend/backend/projects/AI appendix | UI/design -> API/db -> git/CLI -> payment/deploy -> assignments | `subagent-driven-development`, `test-driven-development`, `mobile/frontend delivery`, `api-and-interface-design`, `devops` | High as task breakdown model; stack-specific tooling stays out. |
| Stage 3 / agent-native engineering | README advanced path; lines 317-326 list Claude Code basics, MCP, Skills, long-running tasks, Agent Teams, superpowers, workflow, mobile, SDK, spec coding | agent skills -> tool protocols -> long-running tasks -> multi-agent coordination -> spec coding | `hermes-agent-skill-authoring`, `native-mcp`, `subagent-driven-development`, `planning-with-files`, `spec-stateful-sdd-workflow`, `kanban-worker` | High conceptually; rewrite for Hermes semantics. |
| Stage 3 / cross-platform expansion | Stage tree `cross-platform` plus README cross-platform chapter table | full-stack app -> platform-specific delivery | `mobile-frontend-delivery`, `browser-harness-integration`, platform-specific skills | Medium. Useful as routing taxonomy, not immediate core graph. |
| AI advanced appendix | Stage tree `ai-advanced` with RAG/LangGraph advanced RAG | full-stack -> AI retrieval/workflow extension | `rag-knowledge-management`, `mlops`, `mcp` | Medium. Needs separate technical audit before adoption. |
| Multilingual docs | README news line 154 says Stage 3 multilingual across 10 locales; `docs-readme/` has 10 translated readmes | localize landing pages and curriculum nodes | Hermes docs/i18n governance candidate | Medium/high but requires i18n ownership. |
| Agent context packaging | Root `AGENTS.md` + `CLAUDE.md` + `llms.txt` | short repo contract -> deep dev guide -> machine index | Hermes skill graph should expose index/core/accessory layers | High. Best transferable pattern. |

## 5. Hermes skill-graph mapping

| EasyVibe pattern | Hermes target | Transferable abstraction | Do not do |
|---|---|---|---|
| “会说话就会做应用” pedagogy | User teaching / chat-to-task onboarding | Convert user natural language into staged artifacts: goal, prototype, implementation slice, verification, handoff | Do not weaken proof gates by treating conversational clarity as done evidence. |
| Learning paths before content table | Skill graph entry routing | Use intent-first routing: “first win”, “prototype”, “full-stack”, “agent-native”, “cross-platform” | Do not create a monolithic mega-skill that loads all content. |
| AGENTS + CLAUDE + llms trio | Skill authoring and repo docs | Three-layer packaging: human overview, concise agent contract, generated LLM index | Do not copy Claude-specific commands or VitePress assumptions. |
| llms quick decision tree | Skill discovery | Generate a bounded `llms`/index artifact from Hermes skills with line-linked evidence | Do not hand-maintain large stale context dumps. |
| Stage 1 prototype workflow | Task decomposition | Template: idea framing -> user/problem evidence -> prototype slice -> AI capability -> completion criteria | Do not skip user/problem validation or treat demo as product. |
| Stage 2 delivery workflow | Project implementation | Template: frontend -> backend/API/db -> payment/deploy -> assignments/tests | Do not absorb Stripe/Supabase/Zeabur as Hermes defaults. |
| Stage 3 agent workflows | Hermes advanced automation | Map MCP/skills/long-running/agent teams/spec coding into existing Hermes skills and taskpool gates | Do not import OpenClaw/Claude Code branding as Hermes architecture. |
| Multilingual structure | Docs governance | Locale-aware docs/readme structure with explicit source language and stale translation markers | Do not claim i18n readiness without owners, tests, and stale detection. |
| Interactive tutorials | Skill eval/demo | Use interactive examples as inspiration for verifier-friendly examples | Do not vendor Vue components or execute demo code. |

## 6. OpenClaw association

Observed links:
- README lines 22-23 link `datawhalechina/hello-claw` as “Learn OpenClaw / 学习 OpenClaw”.
- README line 164 says `llms.txt` was added for OpenClaw and other AI agents to understand repo structure and find tutorial content.
- `llms.txt` line 3 directly addresses AI agents including OpenClaw, Claude, Cursor, Trae, GPT-4, Kimi, Tongyi, Wenxin.

Assessment:
- OpenClaw is an adjacent consumer/companion context, not proof that EasyVibe is an audited OpenClaw framework or skill source.
- The useful Hermes pattern is AI-agent-readable context packaging, not OpenClaw-specific workflow adoption.
- Any OpenClaw/hello-claw intake should be a separate source review with its own license/provenance/security gate.

## 7. Recommendation

Recommendation: ADOPT_AS_REFERENCE, not direct import.

Adopt now as patterns:
1. Three-layer documentation packaging: human overview -> short agent contract -> generated LLM index.
2. Intent-first learning path routing before detailed content tables.
3. Stage-gated workflow decomposition from idea/prototype to full-stack to agent-native/cross-platform work.
4. Generated skill-graph index pattern with decision-tree routing and line-linked evidence.
5. Minimal multilingual landing-page structure as a future docs governance candidate.

Temporarily defer:
1. Interactive Vue/VitePress demo embedding; Hermes docs stack differs and the code is license/stack-bound.
2. RAG/LangGraph course content; needs separate technical review and license-safe abstraction.
3. Cross-platform app tutorial taxonomy; useful later but not first Hermes skill-graph slice.
4. Any OpenClaw-specific patterns; separate repo review required.

Reject / do not absorb:
1. Verbatim tutorial prose, tables, snippets, diagrams, or component code due to CC BY-NC-SA 4.0 non-commercial/share-alike boundary.
2. Treating popularity, README claims, or multi-model advisory as implementation proof.
3. Installing/running EasyVibe dependencies as part of this intake.
4. Replacing Hermes AGENTS/skill conventions with Claude Code/VitePress-specific conventions.

## 8. Minimum verifiable slices

| Slice | Scope | Deterministic verification | Expected output |
|---|---|---|---|
| Slice A — Hermes skill-graph generated index | Build a small generated index over 5-10 existing Hermes skills: name, trigger, related skills, owner path, last modified, and route hints | Script reads local skill files only; output has stable JSON/Markdown; tests assert all listed paths exist and descriptions are <=1024 chars | `docs/private/hermes-skill-graph-index-prototype.md` plus JSON evidence. |
| Slice B — three-layer packaging template | For one existing non-critical skill family, draft independent `README`/`AGENTS`/`llms-index` style docs using Hermes wording | Read-back verifies no EasyVibe text copied; links resolve; skill_view still loads target skill | A template under Hermes docs/private or a new reference file, not a shipped skill change yet. |
| Slice C — prototype-to-fullstack task template | Create a Hermes workflow template: idea -> prototype -> implementation slice -> verification -> handoff | Run a dry synthetic task through the template; proof JSON covers each stage and red lines | Candidate workflow reference for `workflow-orchestrator`/`planning-with-files`. |
| Slice D — multilingual docs smoke | Choose one Hermes docs page and design metadata for source locale, translation status, stale source hash | Static checker detects missing/stale locale files without generating translations | i18n governance design note; no broad translation work. |

Recommended next bounded step: Slice A only. It is license-safe, internal, deterministic, and directly advances Hermes skill-graph without importing EasyVibe content.

## 9. Residual risks

- License ambiguity is stricter than the README badge alone because no root LICENSE file exists; keep all future work in “abstracted pattern” mode unless legal review says otherwise.
- `llms.txt` is valuable but can become stale/context-heavy; Hermes should generate indexes from source skills rather than hand-maintain them.
- EasyVibe’s audience is broad beginners; Hermes needs expert/operator proof gates, so pedagogy must be filtered through verification and red-line boundaries.
- Current intake did not audit `hello-claw`; OpenClaw association remains contextual only.

## 10. Red-line status

| Red line | Status | Evidence |
|---|---|---|
| 不安装/运行未审查第三方代码 | SAFE | Only `git clone`, GitHub API/file reads, and local static inspection were used; npm/node scripts were not run. |
| 不复制未核许可内容 | SAFE | This note paraphrases and cites patterns; no tutorial/body code copied. |
| 不把 tutorial repo 当已审计技能库 | SAFE | Recommendation is reference-only, with separate minimum slices. |
| 不上传私有 brain/project 文件到第三方服务 | SAFE | Evidence stored locally under `/tmp/...`; no private files uploaded. |
| do not weaken taskpool proof / permission / mission-envelope gates | SAFE | No lifecycle completion attempted by worker; proof is limited to completed static review. |

## 11. Verification commands/evidence

- `git -C /Users/charles/.hermes/hermes-agent status --short --branch` before editing showed unrelated dirty files plus this intake doc untracked.
- `git clone --depth=1 https://github.com/datawhalechina/easy-vibe.git /tmp/agent-team-2026-06-07-easyvibe-1f4e62749ffd/easy-vibe` completed.
- `git -C /tmp/agent-team-2026-06-07-easyvibe-1f4e62749ffd/easy-vibe rev-parse HEAD` -> `12cddfe15f7763df78827cc8be9872c2b0377e4c`.
- GitHub API evidence written to `/tmp/agent-team-2026-06-07-easyvibe-1f4e62749ffd/evidence/easy-vibe-evidence-summary.json`.
- Static file evidence: README/AGENTS/CLAUDE/llms/package/docs/docs-readme inspected from the pinned checkout.
