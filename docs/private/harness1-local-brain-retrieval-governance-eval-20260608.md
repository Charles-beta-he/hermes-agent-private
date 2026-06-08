# Harness-1 式候选池 / 证据图用于 Hermes local-brain 检索治理 — 设计评估

Date: 2026-06-08
Status: proposed
Task: t_e5522fd7

## 1. 结论先行

结论：值得吸收，但应作为“检索治理层”和“证据化 closeout 辅助层”来吸收，而不是重写 Hermes 当前检索栈。

最合理的落点不是新造一个 Harness-1 式独立检索子系统，而是把其五个核心概念映射到 Hermes 已有的：
1. 候选池生成
2. 候选重要性/可信度标注
3. 证据图 / 证据引用
4. 验证记录
5. 停止策略

Hermes 当前已经具备这些概念的分散实现：
- 会话检索：`tools/session_search_tool.py`
- 大结果回捞：`agent/result_spiller.py`
- 记忆候选检索：`plugins/memory/holographic/retrieval.py`
- 候选准入 proof gate：`agent/recall_proof_gate.py`
- 证据 / artifact / maturity / stale 标记：`docs/private/hermes-control-tower-ledger-schema-20260607.md`
- taskpool/kanban 外部状态写回必须走受控 write-through：`hermes_cli/control_tower.py`

因此最优策略是：先把 Harness-1 的“有状态检索”思想变成 Hermes 的 read-only retrieval governance packet，再根据真实 retrieval miss 证据决定是否需要更重的实现。

## 2. 输入证据与范围

### 外部灵感来源

AIHOT 日报对 Harness-1 的摘要：
- `"Harness-1 维护候选池、重要性标注集、证据图和验证记录，由策略决定搜索、筛选、验证和停止时机。"`
- 来源：`/Users/charles/.hermes/notes/reports/aihot-daily-2026-06-08.md:22`

### 本次核对的 Hermes 现状

1. `agent/recall_proof_gate.py`
2. `tests/fixtures/recall_proof_gate/sample_cases.json`
3. `tests/agent/test_recall_proof_gate.py`
4. `tools/session_search_tool.py`
5. `agent/result_spiller.py`
6. `plugins/memory/holographic/retrieval.py`
7. `agent/memory_manager.py`
8. `docs/private/hermes-control-tower-ledger-schema-20260607.md`
9. `docs/private/hermes-control-tower-adr-20260607.md`
10. `docs/private/hermes-control-tower-implementation-packet-20260607.md`
11. `hermes_cli/control_tower.py`
12. taskpool 任务 note `/Users/charles/Documents/brain/05-实践与项目/tasks/2026-06-08-评估-harness-1-式候选池-证据图用于-hermes-local-brain-检索治理.md`

## 3. 当前 Hermes 检索治理面

### 3.1 候选池生成：已经存在，但分散

Hermes 当前不是“没有候选池”，而是候选池分散在多个 surface：

1. `tools/session_search_tool.py`
   - 通过 SQLite FTS5 做 session discovery / scroll / browse。
   - 候选来自会话消息窗口、bookend、anchor message。
2. `plugins/memory/holographic/retrieval.py`
   - 先用 FTS5 取 `limit*3` 候选，再做 Jaccard / HRR / trust weighting rerank。
   - 这已经是典型的“粗召回候选池 + 重排序”结构。
3. `agent/result_spiller.py`
   - 对长工具输出做 spill，再支持 head/tail/lines/query 形式回捞。
   - 这不是语义检索，但它是 tool-output 层的候选池与定向回捞面。
4. `agent/memory_manager.py`
   - 汇总 built-in + 至多一个外部 memory provider 的 prefetch 结果。
   - 当前是 provider fan-in，而不是统一候选图。

判断：Hermes 缺的不是“能不能产生候选”，而是“如何把跨来源候选统一成可比较、可拒绝、可证明的 packet”。

### 3.2 候选重要性标注：有局部信号，缺统一 schema

当前系统已有多种“重要性 / 可信度 / 质量”字段：
- recall proof gate 的 `confidence`、`quality_score`
- holographic retrieval 的 `fts_rank`、`jaccard`、`trust_score`、`score`
- Control Tower evidence projection 的 `maturity`、`stale_status`

判断：Hermes 已有足够多的打分原语，但没有一个统一的 retrieval-candidate schema 把它们汇总为：
- relevance
- provenance quality
- freshness
- decision-grade eligibility
- stale/conflict flags

这正是 Harness-1 “重要性标注集”最适合迁移的地方：不是加新模型，而是统一 annotation contract。

### 3.3 证据图：Hermes 已有强 SSOT 方向，但尚未前移到检索入口

Control Tower ADR 与 schema 已明确提出：
- Kanban / taskpool / brain markdown / dashboard / gateway 都只能是 adapter 或 projection，不得成为独立事实源。
- ledger 中已有：
  - `tasks.evidence_refs_json`
  - `workflow_steps.evidence_refs_json`
  - `worker_runs.evidence_refs_json`
  - `evidence` 表，包含 `maturity`、`artifact_uri`、`artifact_hash`、`acceptance_refs_json`、`stale_status`

这说明 Hermes 在 closeout / workflow governance 上，已经具备“证据图骨架”。

但该骨架当前主要在 workflow / closeout 一侧；检索入口一侧还没有统一的“candidate -> evidence refs -> gate decision -> chosen winner / rejected set”链路。

判断：Harness-1 的“证据图”不该被实现成另一套图数据库；更应该作为 Control Tower ledger / recall packet 的补充视图。

### 3.4 验证记录：现有 proof gate 已经是最强吸收点

`agent/recall_proof_gate.py` 已把 recall 检索从“凭感觉挑记忆”变成了 replayable gate：
- 要求 `source_kind`
- 要求 durable provenance：`source_path` / `session_id`+`message_id` / `artifact_path`
- 要求 same-scope（默认）
- 要求 `confidence`
- decision-grade 默认拒绝 `reference_only`
- drift-sensitive 文本要求 `last_verified_at`

而 `tests/fixtures/recall_proof_gate/sample_cases.json` + `tests/agent/test_recall_proof_gate.py` 已经把这些规则固定成可回放样例。

判断：Hermes 当前最像 Harness-1 “验证记录”的，不是 memory provider 本身，而是 recall proof gate + fixture corpus + JSON report。

### 3.5 停止策略：已有 fail-closed 方向，但还没有 retrieval policy packet

当前 stop policy 是分散表达的：
- recall candidate 若无 admissible winner，则不应成为 decision-grade proof
- Control Tower closeout 必须 evidence-gated
- taskpool note 明确要求 fail-closed：缺 proof / permission / ratification 不得推进生命周期

判断：Hermes 已有 stop policy 的红线，但还缺一个专门针对 retrieval 的“何时继续搜、何时停止、何时升级人工/多模型/更多验证”的 packet。

## 4. Harness-1 五个概念到 Hermes 的映射

| Harness-1 概念 | Hermes 当前等价物 | 当前状态 | 评估 |
| --- | --- | --- | --- |
| 候选池 | `session_search`、memory provider recall、holographic retrieval、result_spiller query | 已存在但分散 | 可吸收为统一 candidate packet，而非重写召回器 |
| 重要性标注集 | `confidence`、`quality_score`、`trust_score`、`fts_rank`、`maturity`、`stale_status` | 有字段，缺统一 schema | 高价值补强点 |
| 证据图 | Control Tower ledger 的 `evidence` / `evidence_refs_json` / artifact metadata | closeout 侧较强，检索入口较弱 | 应接到 recall packet，不应另起炉灶 |
| 验证记录 | `recall_proof_gate` fixture + JSON report + pytest | 已成型 | 当前最适合扩展的 surface |
| 停止策略 | admissible winner / no admissible / fail-closed gate / human gate | 分散在 proof/closeout/taskpool 中 | 适合补一个 retrieval policy contract |

## 5. 至少 3 个真实或 fixture retrieval miss 案例

本任务 acceptance 要求至少 3 个 retrieval miss。当前仓内已有可回放 fixture，可作为设计输入：

### Miss 1：cross-scope + reference-only session recall 被拒绝

来源：`tests/fixtures/recall_proof_gate/sample_cases.json`
- case: `session_reference_only_rejected`
- candidate: `session_reference_only`
- source_kind: `session_search`

拒绝原因：
1. `scope mismatch: current='project:hermes-agent' candidate='project:uvisa-app-2.0'`
2. `reference-only recall cannot be used as decision-grade proof`

意义：
- 说明 Hermes 已经遇到“会话摘要能搜到，但不能拿来驱动当前实现”的问题。
- Harness-1 风格 candidate packet 可以保留这个候选，但必须把它标记为 non-decision-grade，而不是直接扔给 agent 当可执行事实。

### Miss 2：缺 provenance 的 memory/provider 总结被拒绝

来源：`tests/fixtures/recall_proof_gate/sample_cases.json`
- case: `walrus_memory_rejected_without_provenance`
- candidate: `walrus_orphan`
- source_kind: `walrus_memory`

拒绝原因：
- `missing provenance: require source_kind plus source_path/session_id/message_id/artifact_path`

意义：
- 说明 Hermes 已经明确拒绝“只有 provider 总结、没有 durable anchor”的候选。
- Harness-1 风格 evidence graph 不应放宽这一点；相反应把 provenance 缺失做成 candidate graph 上的 first-class failure edge。

### Miss 3：drift-sensitive fact 缺 freshness metadata 被拒绝

来源：`tests/fixtures/recall_proof_gate/sample_cases.json`
- case: `stale_branch_fact_rejected`
- candidate: `branch_fact_without_ttl`
- source_kind: `memory`

拒绝原因：
1. scope mismatch
2. `drift-sensitive recall missing last_verified_at`

意义：
- 说明 Hermes 已经意识到 branch / PR / next step / status 这类事实极易漂移。
- Harness-1 的“验证记录 + 停止策略”若要落地 Hermes，必须把 freshness / TTL / revalidation 明确化，而不是只做更强召回。

### Miss 4：local-brain task note 只能做 boundary evidence，不能越 project 复用

来源：`tests/fixtures/recall_proof_gate/sample_cases.json`
- case: `local_brain_task_boundary`
- admitted: `brain_boundary`
- rejected: `brain_unrelated_project`

拒绝原因：
- unrelated project scope mismatch

意义：
- 说明 local-brain task note 最适合做“边界/约束证据”，而不是无边界 implementation proof。
- 这与本任务 red line 完全一致：不要把 synthesized envelope 或跨项目 note 当作实现证据。

## 6. 本次实际验证结果

### Recall proof gate CLI

已执行：
`./venv/bin/python -m agent.recall_proof_gate tests/fixtures/recall_proof_gate/sample_cases.json --now 2026-06-07T00:00:00Z --output /Users/charles/.hermes/kanban/workspaces/t_e5522fd7/recall-proof-gate-report.json`

结果：
- `gate_active: true`
- `total_cases: 6`
- `passed_cases: 6`
- `failed_cases: 0`
- `verdict: pass`

### Focused pytest

已执行：
`./venv/bin/python -m pytest -q -o addopts='' tests/agent/test_recall_proof_gate.py`

结果：
- `4 passed in 0.76s`

附注：
- `./scripts/run_tests.sh tests/agent/test_recall_proof_gate.py` 在当前 `.venv` 下失败，原因是 `.venv/bin/python` 缺少 `pytest`；改用仓库 `./venv` 并清空默认 `addopts` 后验证通过。
- 这属于环境差异，不改变本次设计评估结论。

## 7. 设计判断：应该吸收什么，不该吸收什么

### 应吸收

1. 统一 retrieval candidate packet
   - 包含：candidate_id、source_kind、scope、provenance anchors、confidence、freshness、decision_grade、stale/conflict flags、evidence_refs
2. 在 recall proof gate 之上增加 packet-level report
   - 不替换 gate，只把多来源候选统一收口
3. 把 candidate winner / rejected candidates 显式接到 Control Tower evidence refs
4. 明确 retrieval stop policy
   - 有 admissible winner 时停止继续搜索
   - 无 admissible winner 时升级更多检索、更多验证或人工 gate

### 选择性试点

1. 为 local-brain/taskpool 检索增加 read-only candidate collection report
2. 为 retrieval miss 建 fixture corpus
3. 为 drift-sensitive candidate 增加 TTL / revalidation 建议

### 明确不做

1. 不重写 session_search / memory provider / result_spiller / taskpool 为新的独立检索框架
2. 不引入第二套事实源或新图数据库来复制 Control Tower ledger
3. 不把模型生成摘要视为 implementation proof
4. 不因为“召回更多”而放松 same-scope / provenance / freshness / decision-grade gate

## 8. 最小可测后续步骤

推荐的下一 bounded step：

### Step 1：只做 read-only retrieval governance packet

新增一个只读脚本或文档化 packet 生成器，输入：
- query / task objective
- `session_search` 候选
- local-brain/task note 候选
- memory provider 候选
- optional result_spiller query hits

输出统一 JSON：
- candidates[]
- annotations
- rejected[] with reasons
- winner / no_winner
- suggested_stop_action
- evidence_refs

### Step 2：补 3-5 个 retrieval miss fixtures

优先覆盖：
- cross-scope compaction summary
- no-provenance provider summary
- drift-sensitive branch/status fact
- boundary-only task note 被误当成 implementation proof

### Step 3：仅在 read-only packet 证明有稳定收益后，再考虑 runtime integration

如果 packet 不能显著提升：
- retrieval miss 可解释性
- false-positive recall 拒绝质量
- closeout evidence completeness

则停止，不进入更重实现。

## 9. No-go 条件

出现以下任一情况，应停止实施：

1. 需要新增高维护存储层，重复 Control Tower ledger 能力
2. 候选 packet 不能减少误用 cross-scope / stale / provenance-missing recall
3. 运行成本、存储成本、fixture 维护成本明显高于收益
4. 方案会削弱 taskpool proof / permission / mission-envelope gates
5. 方案把 synthesized summary 或 model consensus 偷渡成 implementation proof

## 10. 最终结论

Harness-1 对 Hermes 的真正启发，不是“训练一个更强检索子模型”，而是把检索过程拆成：
- 候选池
- 标注
- 证据图
- 验证记录
- 停止策略

Hermes 当前已经拥有这些部分的大半原语。最有价值的吸收路径是：
- 用 recall proof gate 作为核心准入门
- 用 Control Tower ledger 作为证据图 SSOT
- 用统一 retrieval governance packet 把分散候选面收口
- 用 fixture retrieval misses 决定是否继续演化

因此建议：采纳为“治理层增强”方向，拒绝作为“检索栈重写”方向。