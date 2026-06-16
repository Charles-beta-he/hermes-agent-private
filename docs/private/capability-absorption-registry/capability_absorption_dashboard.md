# Capability Absorption progress

Generated: `2026-06-16T07:42:24.852513+00:00`

## Summary

- Records: **220**
- Open: **33**
- Status: `{"active": 1, "archived": 35, "candidate": 31, "done": 151, "pending": 1, "ready": 1}`
- Layers: `{"module": 197, "workflow_pattern": 23}`
- Target surfaces: `{"core": 11, "local-brain": 116, "provider-quota": 2, "sidecar": 1, "skill": 1, "skill-graph": 33, "taskpool": 56}`

## Open absorption tasks

- `candidate` `P1` **antigravity-artifact-bundle** — Complex tasks should emit reviewable artifacts: plan, diff, tests, screenshots/video, evidence, risks.
- `candidate` `P1` **antigravity-browser-loop** — User-facing web/UI work should validate through browser DOM/screenshot/console evidence, not text-only claims.
- `candidate` `P1` **antigravity-sandbox-terminal** — Risky shell execution should run in bounded workspace/sandbox mode with file/network damage limits.
- `candidate` `P1` **antigravity-unified-permissions** — Agent actions should pass a single permission model for files, shell, network, browser, git, cron, and subagents.
- `candidate` `P1` **google-cloud-context-engineering** — Context should be assembled from docs, preferences, links, history, and constraints with scoping/compression rules.
- `candidate` `P1` **google-secure-agent-controller** — Every high-power agent/module should declare its human controller and approval boundary.
- `candidate` `P1` **google-secure-agent-limited-powers** — Agent powers must be carefully limited through deterministic controls plus dynamic reasoning defenses.
- `candidate` `P1` **google-secure-agent-observability** — Agent planning and actions must be observable through logs, traces, artifacts, and evidence contracts.
- `candidate` `P1` **kiro-agent-hooks** — Pre/post tool, file, prompt, and task hooks should support quality/security checks without uncontrolled side effects.
- `candidate` `P1` **kiro-hook-risk-policy** — Hooks need allow/deny/dry-run policy by event type, command class, workspace, and permission lease.
- `candidate` `P1` **kiro-spec-triplet** — Complex Hermes tasks should materialize requirements.md/design.md/tasks.md equivalents before implementation.
- `candidate` `P1` **kiro-steering-hierarchy** — Hermes project rules should distinguish global/workspace/task/manual/auto inclusion with workspace overriding global ...
- `candidate` `P1` **openspec-proposal-delta** — Hermes should review intent deltas, not only code diffs, for complex capability changes.
- `candidate` `P1` **superpowers-root-cause-debugging** — Bug work should prove root cause before fixes; mitigation-only changes must be labeled as such.
- `candidate` `P1` **superpowers-skill-router** — Tasks should load relevant skills before response/execution, but bounded to avoid recursion and system-safety override.
- `candidate` `P1` **superpowers-tdd-enforcer** — Behavior changes should prefer failing-test-first verification and delete/rework code that bypasses the test gate.
- `candidate` `P1` **superpowers-verification-before-completion** — No completion claim without fresh verification command/output in the same work slice.
- `ready` `P2` **2026-05-18-分析-sakana-ai-多模型指挥官理念并提炼-hermes-conductor-融入方案** — 让多模型编排从静态 tier/quorum 升级为证据化、角色化、模型画像驱动的 conductor 层：按模型强弱项分配 planner/critic/verifier/arbiter，并为每个模型注入短小稳定的 model-spe...
- `candidate` `P2` **antigravity-agent-manager** — Manage multiple local agents by project/workspace with isolated context, permissions, outputs, and synthesis.
- `candidate` `P2` **antigravity-visual-comments** — Screenshots/artifacts should accept targeted user feedback and map it back to task deltas.
- `candidate` `P2` **google-adk-fanout-gather** — Parallel specialist agents should write to unique keys and aggregate through a synthesizer.
- `candidate` `P2` **google-pair-explainability** — Hermes progress/explanations should give the user enough decision evidence without dumping internal reasoning.
- `candidate` `P2` **kiro-agents-md-compat** — Project AGENTS.md rules should feed Hermes project context as scoped, untrusted workspace guidance, not global memory.
- `candidate` `P2` **kiro-bug-analysis-spec** — Costly regressions should start from bug analysis, reproduction, design, and task artifact before code changes.
- `candidate` `P2` **openspec-capability-spec-store** — Long-lived specs should live by capability next to code/docs so context survives chat/session boundaries.
- `candidate` `P2` **openspec-opsx-actions** — Spec work should support propose/apply/update/archive as fluid actions rather than rigid waterfall phases.
- `candidate` `P2` **openspec-project-config** — Project context/rules should be injected by artifact type and matching rules instead of global prompt dumping.
- `candidate` `P2` **speckit-constitution** — Projects should have governing principles that constrain later specs/plans/tasks.
- `candidate` `P2` **speckit-executable-spec** — Specs should become executable implementation inputs with traceable plan/tasks/implementation lineage.
- `candidate` `P2` **superpowers-brainstorming-gate** — Creative/feature work should clarify intent and present design before implementation unless low-risk direct action is...
- `candidate` `P2` **superpowers-fresh-subagent-per-task** — Independent plan tasks can use fresh subagents plus spec-compliance and code-quality review before merge.
- `pending` `P2` **2026-06-16-评估-computer-use-agent-沙箱-基准设施对-hermes-浏览器自动化主链的可维护接入门** — 若通过验收，Hermes 可在浏览器/桌面自动化任务上获得更好的隔离实验、标准化评测与失败归因能力；若不通过，也能沉淀明确的拒绝依据和回退门。
- `candidate` `P3` **antigravity-scheduled-agent-messages** — Scheduled messages can drive routine agent work if tied to evidence ledgers and human gates.

## Recent done

- `done` `P2` **2026-06-16-从-niuma-openclaw-视频提炼-agent-role-topology-v1-并接入-workflow-plan-v1-设计** — 把视频中的 Planner/Coder/Tester/Reviewer/Deploy 拓扑沉淀为可验证 workflow_plan_v1 角色模板
- `done` `P2` **2026-06-16-评估吸收-web-to-design-md-为-hermes-网站风格提取模块** — repo_capability_absorption:website-style-to-design-md-module->skill:evaluate
- `done` `P2` **2026-06-15-吸收-agent-native-五层闭环为-hermes-workflow-kernel-架构验收镜头** — 把 Douyin「Agent-native 架构课 05」的五层闭环作为 Hermes Agent Builder / workflow kernel 的评估镜头：Surface 只表达意图，Host 负责治理，Capability ...
- `done` `P2` **2026-06-15-分析抖音图文作品泽轩604vibe-coding大赏migo** — Task - 分析抖音图文作品｜泽轩604「Vibe Coding大赏｜Migo」
- `done` `P2` **2026-06-14-agent-memory-选型决策树吸收记忆落点分类与反幽灵门禁到-hermes-memory-governance** — 把 Agent Memory 的选型决策树、四类记忆、四种设计模式转化为 Hermes 记忆落点分类器：短期上下文、长期 profile、实体/项目 profile、情景/session_search/taskpool、技能沉淀、RA...
- `done` `P2` **2026-06-14-douyin-davinci-autoedit-agenthermes-自动剪辑-workflow-与成本门禁吸收** — 将 DaVinci-AutoEdit-Agent 的素材只读、阶段确认、VLM素材分析、文案/TTS、剪辑蓝图、DaVinci Resolve/MCP 写入审计、补拍建议流程，吸收到 Hermes 自动剪辑 Workflow Pack...
- `done` `P2` **2026-06-14-douyin-dcar-opensource吸收多频闭环控制与物理执行安全门禁到-hermes-workflow-external-control** — 将 Dcar_OpenSource 惯导麦轮小车仓库中的多频调度、闭环反馈、协议ACK/timeout、物理执行安全边界与教学资产包装方式，吸收为 Hermes Workflow Kernel / taskpool / externa...
- `done` `P2` **2026-06-14-harness-engineering审计-hermes-模型外控制系统并建立可拆卸-harness-组件治理** — 将 Harness Engineering 的上下文架构、架构约束、自验证循环、上下文隔离、自我治理、可拆卸性转化为 Hermes harness 组件审计与治理：每个 skill/tool/hook/gate 必须有失败证据、成本、...
- `done` `P2` **2026-06-14-sam-3d-comfyui制造脑-photo-to-3d-candidate-工作流与-draft-only-门禁评估** — 将 SAM 3D Objects/Body 与 ComfyUI image-to-3D 工作流吸收到制造脑 photo-to-3D candidate 管线：生成资产只进入 draft_only/studio_preview/cand...
- `done` `P2` **2026-06-14-补证闭环task---agent-memory-选型决策树吸收记忆落点分类与反幽灵门禁到-hermes-memory-governancemissing-rat** — review gap repair for missing_ratification_or_selfcheck
- `done` `P2` **2026-06-14-wolfpack-ensemble-risk-pattern吸收多引擎低相关性与回撤门禁到-hermes-trading-read-only-evaluator** — 将多引擎 Wolfpack、低相关性、策略生态位、引擎强度/相关性/锦标表和 profit-vs-drawdown 冲突吸收到 Hermes 交易 read-only evaluator 的风险门设计。
- `done` `P2` **2026-06-14-better-t-stack-json-first-scaffold-pattern吸收到-hermes-workflow-agent-builder-编译器** — 将 Better-T-Stack 的 JSON-first create-json/add-json、runtime schema introspection、MCP plan-before-create、dry-run、compat...
- `done` `P2` **2026-06-14-tradingagents-决策委员会模式吸收多角色证据辩论与风险门禁到-hermes-goal-calibration** — 把 TradingAgents 的多源证据、分析师、牛熊辩论、研究经理、交易提案、风险/组合经理抽象为 Hermes 重大决策委员会工作流，强化执行前风险门禁和证据日志。
- `done` `P2` **2026-06-14-impeccable吸收-ai-ui-反土味检测到-hermes-uimax-视觉品味门禁** — 将 Impeccable 的 AI UI slop taxonomy、41条确定性检测、23个设计命令、PRODUCT/DESIGN 上下文包和可选 detect CLI，吸收到 Hermes UIMax/设计验收中，补齐“界面很土但...
- `done` `P2` **2026-06-14-nexsandglass吸收轻量本地记忆与偏好漂移检测到-hermes-memory-governance** — 吸收 NexSandglass 的轻量本地记忆、倒排索引、时间线检索、偏好漂移检测思路，转化为 Hermes 记忆治理/goal-calibration 的 stale-memory 与 preference-shift 自检设计。

## Safety boundary

- registry projection only; no task lifecycle mutation, no external repo execution, no skill/memory writes
