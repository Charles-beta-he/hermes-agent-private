# Hermes Governed Workflow Kernel v1：长期稳定冻结方案

状态：F0 Architecture Frozen（用户已认可并冻结架构；运行时尚未达到 F1–F5 认证）
冻结授权：2026-08-01，范围为 HGWK v1 架构、SSOT 所有权、核心合同和安全不变量；不自动开放外部发送、发布、部署、破坏、支付或凭证边界。

## 1. 最终决策

长期方案不是引入 Google ADK 作为第二套 Runtime，也不是另造 Taskpool、Mission、Session 或事件总线。

最终冻结方向是：

> 以现有 `workflow_kernel` 为唯一受治理工作流内核，冻结合同、所有权和安全不变量；从 ADK 只吸收 Graph 验证、deferred task、interrupt/resume、trajectory eval、session revision 等机制，通过本地 Adapter 和 Projection 扩展，绝不让外部框架成为生命周期或权限 SSOT。

架构名称：`Hermes Governed Workflow Kernel v1`（HGWK v1）。

## 2. 成功标准与非目标

### 成功标准

1. 同一实体只有一个生命周期所有者，不出现平行 SSOT。
2. Workflow Definition、Runtime Manifest、Run Event、Leaf Packet、Grant、Receipt 都有版本和 digest。
3. 所有状态推进可回放；相同稳定事件得到相同终态投影。
4. crash/retry/resume 不重复高副作用操作。
5. 动态节点不能扩大 Authority、Budget、Target 或 Evidence 边界。
6. required evidence/metric 缺失时 fail-closed。
7. 完成必须由结构化输出、Evidence Receipt、Verifier 和 readback 共同证明。
8. Telemetry 可失败而不影响执行，但绝不能被提升为不可丢失证据。
9. 外部框架、模型、Provider、CLI 和 UI 可替换，不改变内核不变量。
10. 升级和回滚都以 schema/version/digest 为边界。

### 非目标

- 不把 ADK、LangGraph、A2A 或任一 Provider 设为 Hermes 核心依赖。
- 不建立第二个 Taskpool、Mission Store、Session Store、Approval Store 或 Workflow Event Bus。
- 不把 LLM Judge、多模型投票、CLI exit code、日志文案当作完成认证。
- 不把 token delta、调试 span、临时 session state 持久化为生命周期事实。
- 不在 v1 冻结时开放任意外部写、支付、凭证输入或广泛 fan-out。

## 3. 唯一所有权矩阵

| 对象 | 唯一所有者/SSOT | Workflow Kernel 的角色 |
|---|---|---|
| Workflow Definition | `workflow_artifact_ledger` | 通过 revision + digest 读取不可变快照 |
| Product Workflow Run 状态（`proposed/claimed/running/review/done`） | `workflow_run_event_ledger` | 既有产品/控制面状态域；只能经显式 Adapter 映射到 HGWK，不能直接证明执行完成 |
| HGWK Governed Execution Run 生命周期（`accepted/started/.../run_verified/run_completed`） | `workflow_execution_ledger` | HGWK 执行生命周期唯一权威与回放来源 |
| Workflow Loop Shadow Session | `workflow_loop_event_ledger` | authority-neutral、non-executable 的局部观察/控制循环状态域；不是 Execution Run 或 Product Run 终态权威 |
| Runtime Manifest | 不可变输入合同 | 只读，不保存可变状态 |
| Taskpool Task | 现有 Taskpool/Kanban SSOT | 仅通过 Adapter 引用、claim、推进、readback |
| Mission | 现有 Mission Store | 引用 mission ID；不复制 Mission 状态机 |
| Conversation/Session | 现有 Session DB | 保存对话事实，不成为 Workflow 生命周期权威 |
| Human Decision | 现有 Mission/Decision 或统一 Interrupt Adapter | Kernel 保存 interrupt/ref，不复制审批实体 |
| Authority | fresh grant +既有 Authority policy | Kernel 只消费精确、短命、单次授权 |
| Evidence 生命周期 | `workflow_execution_ledger` 中的 Evidence Receipt | 内容本体放内容寻址存储；Receipt 是认证事实 |
| Telemetry | Sidecar/OTel | 观察投影，不参与完成门禁 |
| 外部 A2A Task | A2A Adapter/远端协议 | 显式状态映射，不与内部 NodeStatus 同构 |
| ADK Workflow/Task | 可选执行 Adapter | 不能拥有 Hermes 状态、权限或完成判定 |

关键命名边界：现有 `workflow_artifact_ledger` 是“可编辑 Workflow Definition 的作者态账本”，不是运行证据文件仓库。证据内容应由独立的内容寻址 Blob Store 保存，但其生命周期认证仍写入 `workflow_execution_ledger`，从而避免第二个证据生命周期 SSOT。

### 3.1 Ledger 状态域消歧与重建合同

| Ledger | 权威级别 | Source / Writer | Consumer | 可删除重建 | 禁止事项 |
|---|---|---|---|---|---|
| `workflow_execution_ledger` | HGWK Execution Run canonical | Governed Runtime 的受治理事件追加路径 | execution projection、resume、verification/readback | 否；只能按 ledger recovery 合同做 CAS 保护修复 | 不得从 Product Run/Loop 状态反推或覆盖 canonical 终态 |
| `workflow_run_event_ledger` | Product Workflow Run canonical；HGWK 外部域 | 既有 product/control-plane transition writer | 旧 product run projection 与兼容 API | 否；按其独立恢复合同处理 | `done` 不等于 HGWK `run_completed`；不得直接铸造 Evidence Receipt 或 ExecuteGrant |
| `workflow_loop_event_ledger` | Local-shadow session signal log，不是 execution/product 终态权威 | `observe_workflow_loop_shadow_v1`，且 registry 必须保持 default-off、authority-neutral、non-executable | local observation report 与 loop snapshot | 是；仅当 canonical 输入 signal 集与 plan digest 可重新取得时可重放重建；否则删除意味着丢弃观察，不得补写 canonical 事实 | 不得执行 task、写 adapter、推进 Product Run/HGWK Execution Run 终态或提供完成认证 |

跨域只允许显式 Adapter 产生带 provenance 与 source digest 的候选映射；目标域必须重新执行自身 admission/authority/evidence gate。三种状态枚举、事件 ID 与终态语义保持不同构，禁止共享 transition helper 形成隐式状态桥。

Schema 单向所有权：`apps/workflow-contracts/src/*.ts` 是 authored contract；根 `schemas/*.schema.json` 是生成发布面；`workflow_kernel/schemas/*.schema.json` 是由根 schema 同步的 packaged read-only copy。生成脚本与 `schema:check` 是唯一合法传播路径，禁止手改 JSON schema 或 packaged copy。

## 4. 冻结后的分层架构

```text
┌─────────────────────────────────────────────────────────────┐
│  Surface Layer                                              │
│  CLI / TUI / Gateway / Dashboard / Cron                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ Intent / readback
┌──────────────────────────▼──────────────────────────────────┐
│  Governance Control Plane                                  │
│  Mission / Taskpool / Authority / Decision / Budget         │
│  owns: objective, task ownership, grants, human decisions   │
└──────────────────────────┬──────────────────────────────────┘
                           │ exact references + grants
┌──────────────────────────▼──────────────────────────────────┐
│  HGWK Contract & Compiler                                   │
│  WorkflowDefinitionV1 → validation → immutable manifest     │
│  graph/schema/authority/evidence/budget/concurrency checks   │
└──────────────────────────┬──────────────────────────────────┘
                           │ admitted run
┌──────────────────────────▼──────────────────────────────────┐
│  HGWK Governed Runtime                                      │
│  bounded scheduler / NodeRun / interrupt / retry / replay    │
│  fresh one-shot ExecuteGrant / default-off adapters          │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
        ┌───────▼────────┐             ┌───────▼────────────┐
        │ Adapter Plane   │             │ Execution Ledger   │
        │ local pure      │             │ append-only SSOT   │
        │ tool/delegate   │             │ events/projection  │
        │ taskpool/A2A    │             │ receipts/readback  │
        │ optional ADK    │             └───────┬────────────┘
        └───────┬────────┘                     │
                │                              │
        ┌───────▼──────────────────────────────▼────────────┐
        │ Evidence & Evaluation Plane                       │
        │ content-addressed blobs + receipts + deterministic│
        │ checks + trajectory eval + advisory semantic eval │
        └──────────────────────┬────────────────────────────┘
                               │ projections only
        ┌──────────────────────▼────────────────────────────┐
        │ Observation Plane: Sidecar / OTel / Reports       │
        └───────────────────────────────────────────────────┘
```

## 5. 冻结的核心合同

### 5.1 WorkflowDefinitionV1

冻结字段语义：

- definition ID、revision、digest；
- nodes/tasks、dependencies/routes；
- input/output schema；
- workflow authority upper bound；
- node authority intersection；
- budget、stop conditions、concurrency；
- acceptance/evidence contract；
- human-gate requirement；
- dynamic-expansion policy。

编译器必须是纯函数：无文件写、工具调用、模型调用、Adapter 调用、注册或授权。

### 5.2 RuntimeManifestV1

Manifest 是不可变执行输入，不是状态存储。至少绑定：

- definition ID/revision/digest；
- compiler/schema revision；
- Adapter registry revision；
- exact resource budgets；
- concurrency policy；
- isolation policy；
- allowed targets/capabilities；
- required evaluators/evidence；
- rollout mode：shadow/local/gated。

Manifest 内容变化必须产生新 digest 和新 run，不允许原地变更。

### 5.3 NodeRunV1

```text
node_run_id
workflow_run_id
node_definition_id
parent_node_run_id?
attempt
status
input_digest
output_digest?
authority_projection
budget_reservation
interrupt_refs[]
evidence_receipt_refs[]
effect_receipt_refs[]
started_at / terminal_at
error_class?
```

状态冻结为：

```text
PENDING → RUNNING → WAITING | SUCCEEDED_CANDIDATE | FAILED | CANCELLED
WAITING → RUNNING | FAILED | CANCELLED
SUCCEEDED_CANDIDATE → VERIFIED | REJECTED
VERIFIED → COMPLETED
```

`COMPLETED` 是认证终态；Adapter/Agent 不能直接写入。

### 5.4 TaskEnvelopeV1

Task Envelope 是委托合同，不取代 Taskpool Task：

```text
task_ref / parent_task_ref
delegation_call_id
target_capability
objective
context_refs
input_schema_version / input_digest
authority_grant_ref
budget / deadline
idempotency_key
expected_evidence
prohibited_actions
isolation_scope
```

Task Completion：

```text
candidate_status
typed_output / output_digest
evidence_refs
effect_receipts
negative_evidence
readback
unmet_requirements
```

采用 deferred-response 模式：委托先持久化；Child 完成认证后，再生成匹配 `delegation_call_id` 的响应。未配对 call/response 是恢复线索，但不能单独证明 Task 状态。

### 5.5 InterruptV1

```text
interrupt_id
node_run_id
reason_kind
question
allowed_response_schema
authority_needed
expires_at
single_use
resume_target
response_digest?
```

禁止把“用户说已完成”直接转换为 resume success；只能消费匹配、未过期、单次的结构化 Interrupt Response。

### 5.6 EvidenceReceiptV1

```text
evidence_id
kind
content_uri
content_sha256
content_length
mime_type
producer_invocation
source_input_digests
tool/model/version
created_at
storage_generation
redaction_class
retention_policy
```

内容先以临时对象写入，计算 digest、校验长度后原子提交；Execution Ledger 再追加 Receipt。半成品或未记账 Blob 不可作为证据。

### 5.7 GateDecisionV1

```text
gate_id
target_run/node
gate_policy_revision
required_metric_results
deterministic_evidence_refs
semantic_review_refs
negative_requirement_results
decision: PASS | FAIL | ERROR | MISSING_EVIDENCE
reason_codes
created_at
```

只有 `PASS` 可推进；`ERROR`、`NOT_EVALUATED`、`MISSING_EVIDENCE` 对 required 项全部阻断。LLM Judge 只能提供 advisory result，不能单独产生 PASS。

### 5.8 EffectReceiptV1

所有可变副作用必须绑定：

- exact adapter/operation/target；
- payload digest；
- authority grant；
- idempotency key；
- preflight readback；
- execution receipt；
- post-apply readback；
- rollback/compensation；
- result classification：applied/not_applied/unknown。

`unknown` 只能进入 reconcile，禁止盲重试。

## 6. 十五条不可变架构约束

1. Execution Ledger 是 Workflow Run 唯一生命周期权威。
2. Runtime Manifest 永远不可变、不可承载状态。
3. Workflow Definition 的作者态变更只经 revision + base digest CAS。
4. 权限只能单调收窄：child authority = parent ∩ workflow ∩ node ∩ fresh grant。
5. Grant 短命、精确、单次消费，不能序列化成可恢复 bearer authority。
6. 任一高副作用节点必须拥有 idempotency、receipt、readback、rollback/compensation。
7. replay 只重建状态，不重复已确认副作用。
8. required evidence/metric 未执行、异常或缺失时 fail-closed。
9. Agent 的文本、`finish_task`、进程退出码、日志和 OTel span 都不是完成证明。
10. Telemetry 与 Evidence Ledger 分离。
11. 动态扩展必须重新验证，且不能扩大 authority/budget/target/concurrency 上界。
12. 默认并发必须有界，并按 Provider、Browser、Tool Pool、Adapter 分层限制。
13. Taskpool、Mission、Session、A2A 状态只通过 Adapter 映射，不复制状态机。
14. 稳定状态转换与 receipt 持久化；token delta 和调试流默认短暂。
15. 每次 schema/contract 破坏性变化必须升大版本；v1 只允许向后兼容字段扩展。

## 7. ADK 的长期位置

ADK 只能有三种身份：

1. Reference：源码与测试作为模式参考；
2. Harness：运行兼容性/评估实验；
3. Optional Executor Adapter：执行纯计算或受控节点。

ADK 永远不能：

- 直接写 Hermes Execution Ledger；
- 持有 Hermes AuthorityGrant；
- 决定 Taskpool/Mission 状态；
- 将 `finish_task` 或 ADK Eval PASS 提升为 Hermes COMPLETED；
- 直接开放外部写；
- 以 ADK Session/Artifact 替代 Hermes SSOT。

ADK Adapter 输出必须降格为 Candidate Packet，再由 Hermes verifier 认证。

## 8. 可冻结面与可演进面

### 冻结 3–5 年的部分

- SSOT 所有权矩阵；
- 15 条不可变约束；
- Authority 单调收窄；
- Event/Receipt 的版本化与内容绑定；
- Candidate → Verified → Completed 语义；
- Telemetry/Evidence 分离；
- Task/Node/A2A 显式适配；
- 动态扩展重新认证；
- required metric fail-closed；
- 外部副作用的 idempotency/readback/rollback。

### 允许持续演进的部分

- Node/Adapter 实现；
- 模型与 Provider；
- ADK/LangGraph/A2A 版本；
- Eval 指标插件；
- 存储后端；
- Scheduler 算法；
- UI/Dashboard；
- Telemetry exporter；
- concurrency 数值与预算策略。

演进只能通过版本化 Adapter/Policy/Schema，不能改变冻结不变量。

## 9. 分阶段冻结与认证

### F0：Architecture Freeze

冻结本文的分层、SSOT、合同和不变量。

门禁：

- 本地所有者映射有源码证据；
- 无第二 Taskpool/Event Bus/Approval Store；
- ADR 明确 ADK 只作 Adapter/Reference；
- 明确哪些是现有实现、哪些是待实现。

### F1：Contract Freeze

冻结所有 v1 JSON Schema、枚举、digest 规则和错误码。

门禁：

- Schema meta-validation；
- canonical JSON 跨 Python/TS fixture parity；
- forward/backward compatibility fixtures；
- duplicate key、NaN、时区、路径、secret negative tests；
- authority widening 和 missing evidence 必须失败。

### F2：Shadow Freeze

所有现有执行路径只额外生成 Run/Event/Receipt Projection，不改变行为。

门禁：

- 原路径与 Shadow 的 terminal/result parity；
- 无额外外部副作用；
- Projection 可删除并重建；
- Telemetry 丢失不影响 Evidence；
- crash/restart replay fixtures 通过。

### F3：Single-Lane Runtime Freeze

仅迁移一条纯本地、无副作用的消费者到 HGWK Runtime。

门禁：

- deterministic replay；
- bounded concurrency；
- cancel/timeout/interrupt/resume；
- retry budget 持久；
- candidate/verifier/completion 分离；
- rollback 到旧路径一键可用。

### F4：Governed Side-Effect Freeze

按 side-effect tier 逐级开放 local_write、external_read、exact external_write。

门禁：

- exact target/payload/grant；
- idempotency；
- pre/post readback；
- unknown-result reconcile；
- compensation/rollback receipt；
- kill switch；
- 独立审验；
- 高风险负面用例全部通过。

### F5：Long-Term Stable Freeze

只有以下证据齐全才可标记 `HGWK v1 LTS`：

- exact tree/archive parity；
- 全量 schema/contract tests；
- replay and crash matrix；
- Taskpool/Mission/Session/A2A adapter parity；
- side-effect duplicate-prevention tests；
- required-metric fail-closed truth table；
- artifact digest/atomicity/concurrency tests；
- CLI/API terminal-state parity；
- fresh deployment/readback/rollback smoke；
- 独立 verifier review；
- 旧路径移除清单与恢复包。

## 10. 回滚模型

每阶段必须保留：

- feature flag/kill switch；
- 原执行路径；
- schema migration rollback；
- ledger read-only replay 工具；
- Adapter disable switch；
- exact revision/digest 回退；
- target-specific compensation；
- 未完成 run 的 pause/cancel/reconcile，而不是强行迁移。

升级失败时只回滚 Adapter/Policy/Consumer，不回写或删除已有 Ledger Events。

## 11. 当前实现的差异分类

### Preserve

- `workflow_definition.py`：纯编译、digest、authority narrowing；
- `workflow_execution_ledger.py`：唯一生命周期权威、append-only、schema validation；
- `workflow_artifact_ledger.py`：作者态与执行态分离、revision-bound mutation；
- `runtime_state.py`：profile-scoped 私有存储、锁、no-follow 路径保护；
- `governed_runtime.py`：default-off、fresh one-shot ExecuteGrant、trusted pure local Adapter。

### Extend

- Graph Validator：route、条件环、节点输入输出 schema 兼容性；
- NodeRun/Interrupt/Task Envelope 明确 schema；
- persistent retry attempt ledger；
- required-metric fail-closed gate；
- Evidence Receipt + content-addressed Blob；
- deterministic trajectory evaluator；
- Taskpool/Mission/A2A 显式 Adapter 状态映射；
- side-effect EffectReceipt 与 reconcile。

### Reject

- 新建第二套 Workflow 生命周期存储；
- 直接嵌入 ADK Runtime 成为内核；
- 以 Session/Sidecar/OTel 替代 Execution Ledger；
- 以 Agent 完成文案或 Eval 分数直接推进终态；
- 默认无限并发与恢复后重置 retry；
- 动态 fire-and-forget 高副作用节点。

## 12. 最终冻结句

> Hermes 长期稳定架构冻结为：一个治理控制面、一个 Workflow Definition 作者态账本、一个 Workflow Execution 生命周期账本、一个默认关闭且受 fresh grant 约束的执行内核、一个内容寻址证据层，以及可替换的 Adapter/Provider/UI 生态。外部框架只能提供候选执行能力，不能拥有状态、权限或完成判定。
