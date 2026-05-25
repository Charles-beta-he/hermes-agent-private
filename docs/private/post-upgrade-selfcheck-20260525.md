# Hermes 升级后架构自检汇报（2026-05-25）

生成时间：2026-05-25 12:33 CST
目标仓库：`/Users/charles/.hermes/hermes-agent`
目标分支：`private/main`

## 1. 结论

本轮升级后的架构基线可用，私仓治理线已经重新对齐：

- `private/main` 已合并最新 `upstream/main`，并已推送到 `origin/private/main`。
- `origin/private/main` 与本地 HEAD 完全一致：`0 0`。
- 相对 `upstream/main`：本地私有线 ahead 25、behind 0；说明已包含 upstream 最新内容，额外 25 个提交是私有治理/overlay/本地硬化层。
- `post-update-check.py --fix` 无 `FIX_FAILED`，无 vendor/private 补丁漂移失败。
- 多脑 registry、私仓治理、overlay registry、continuation guard、conductor runtime、OpenClaw hardening 均通过。
- 当前最大风险不是 upstream 升级本身，而是升级前已存在并已恢复的 WIP dirty worktree，需要后续按 scoped commit 收口。

## 2. Git / 远程拓扑

证据命令：

```bash
hermes --version
git status --short --branch
git log --oneline -1 --decorate
git rev-list --left-right --count HEAD...origin/private/main
git rev-list --left-right --count HEAD...upstream/main
git remote -v
```

关键输出：

```text
Hermes Agent v0.14.0 (2026.5.16)
HEAD: 868f26406 (HEAD -> private/main, origin/private/main, origin/HEAD) Merge remote-tracking branch 'upstream/main' into private/main
origin/private/main divergence: 0 0
upstream/main divergence: 25 0
origin   https://github.com/Charles-beta-he/hermes-agent-private.git
upstream https://github.com/NousResearch/hermes-agent.git
```

判定：

- 私仓 `origin` 正确指向 `Charles-beta-he/hermes-agent-private`。
- 官方 `upstream` 正确指向 `NousResearch/hermes-agent`。
- 当前分支正确：`private/main`。
- 私仓远端已同步；没有未推送的升级 merge commit。

## 3. post-update-check 结果

证据命令：

```bash
python3 ~/.hermes/scripts/post-update-check.py --fix
```

汇总：

```text
16 OK | 0 fixed | 2 need attention
```

通过项：

- `chat_completions_truthiness`: OK
- `reasoning_content_step_ordering`: OK
- `memory_manager_provider_rollback`: OK
- `prompt_builder_ghost_prevention`: OK
- `context_compressor_anti_ghost`: OK
- `mcp_rpc_timeout_guard`: OK
- `launchd_gateway_nofile_limit`: OK
- `gateway_fd_watchdog`: OK，fd=61
- `memory_ghosts`: OK
- `multi_brain_registry`: OK，42 registry/boundary checks
- `private_repo_governance`: OK，branch/origin/upstream/docs/runtime-clean 均通过
- `overlay_registry`: OK，7 overlays registered，7 with verification commands
- `hook_registry_selfcheck`: OK
- `continuation_fragmentation_guard`: OK
- `conductor_runtime_productization_regression`: OK，tests=pytest，conductor=enabled，fugu=fail-soft，worker=ok
- `openclaw_productization_regression`: OK，benchmark 18/18

非失败但需关注：

- `skill_tracking`: `INSTALLED_NO_DATA_YET`。这是观测数据尚未积累，不是升级失败。
- `steward_liveness`: `DORMANT (0 rules triggered)`。当前没有触发 steward 规则，不是故障。
- `skill_quality`: `MISSING (83/228 no tests...)`。这是技能测试覆盖率债务，不阻塞本次升级，但应作为后续治理任务处理。

## 4. 架构边界检查

### 4.1 私仓 / overlay / runtime-state 边界

证据：`post-update-check` + `brainctl --json brains validate`。

判定：通过。

关键点：

- 运行时代码仓库在 `~/.hermes/hermes-agent`。
- runtime branch 为 `private/main`，符合 Hermes 私仓治理预期。
- `docs/private/overlay-registry.md`、`docs/private/upgrade-sop.md`、`docs/private/stable-points.md`、`docs/private/multibrain-architecture.md` 均存在。
- runtime repo 中未发现应隔离的 `state.db` / `.hermes` runtime state 泄漏。
- 多脑 registry root `/Users/charles/.hermes/brains` 存在，registry version `0.6.0`，subbrain count=13。

### 4.2 Continuation / Relay / Handoff 边界

证据：`continuation_fragmentation_guard`。

判定：通过。

含义：

- 未发现新的 ad-hoc continuation/handoff/relay/resume/retry writer 绕过 `agent.continuation`。
- 升级后仍维持“续跑状态统一经 canonical continuation 层”的架构约束。

### 4.3 MCP RPC 超时治理

证据：`mcp_rpc_timeout_guard: OK`。

判定：通过。

含义：

- 升级后本地硬化要求仍成立：MCP RPC 调用未退化回分散 lock/timeout 实现。
- 之前 `hermes update` 初次在 `main` 分支上跑 self-check 时出现过 `FIX_FAILED`，但最终在 `private/main` 合并后重新执行已经恢复为 OK。

### 4.4 Conductor / Fugu / Role-plan 架构

证据：`conductor_runtime_productization_regression: OK`。

判定：通过。

含义：

- `agent/conductor_runtime.py` 存在并可编译。
- conductor runtime 默认可配置启用；Fugu adapter 保持 fail-soft，不因缺 key/禁用而破坏主流程。
- role-plan 与 background worker smoke gate 通过。
- 该层仍符合“default-off scaffold + isolated router call”的架构原则。

### 4.5 OpenClaw / Control-plane hardening

证据：`openclaw_productization_regression: OK (benchmark 18/18)`。

判定：通过。

含义：

- OpenClaw 相关硬化文件和 benchmark 仍在升级后可用。
- activation hints 保持 on。

### 4.6 多脑 registry

证据：

```bash
python3 ~/.hermes/scripts/brainctl.py --json brains validate
```

判定：通过。

关键输出：

```text
passed: true
subbrain count=13
runtime_branch: private/main
runtime_origin_private: Charles-beta-he/hermes-agent-private.git
runtime_upstream_official: NousResearch/hermes-agent.git
routing_default_entry=main
```

## 5. 当前 WIP / 风险面

当前 worktree 不是 clean，这是升级前 WIP 已恢复后的状态。升级基线 commit 已经推送，WIP 未纳入升级 commit。

### 5.1 Modified WIP

```text
agent/auxiliary_client.py
hermes_cli/kanban_db.py
skills/autonomous-ai-agents/hermes-agent/SKILL.md
skills/software-development/writing-plans/SKILL.md
tests/agent/test_auxiliary_client.py
tests/hermes_cli/test_kanban_db.py
tests/run_agent/test_taskpool_dispatch_tick_runtime.py
tools/browser_tool.py
```

Diff stat：

```text
8 files changed, 808 insertions(+), 58 deletions(-)
```

风险判定：中高。

原因：

- `tools/browser_tool.py` 变更量较大（约 556 行），属于工具层高影响面。
- `hermes_cli/kanban_db.py` 是任务池/worker 核心状态层，升级恢复 WIP 时曾发生一次冲突，已手动合并并通过 py_compile/聚焦测试，但仍需最终 scoped commit 前再跑完整相关测试。
- skills 变更属于行为约束/工作流层，需避免未验证 skill 约束污染后续任务。

### 5.2 Untracked WIP

```text
agent/run_controller.py
agent/run_envelope.py
docs/private/*-gap-analysis.md / proposal docs / run-envelope-v1-ledger.md
rowboat_tree.json
tests/agent/test_run_controller.py
tests/agent/test_run_envelope.py
tests/run_agent/test_taskpool_run_envelope.py
tests/tools/test_browser_replay_evidence.py
website/docs/developer-guide/browser-replay-evidence.md
```

风险判定：中高。

原因：

- `agent/run_envelope.py` 已被 `hermes_cli/kanban_db.py` 的 WIP fallback import 引用，但文件仍 untracked；如果只提交 kanban_db 而漏提交 run_envelope，会造成隐性降级或测试/运行路径不完整。
- `agent/run_controller.py` + tests 暗示一组新运行控制面能力，不能拆散提交。
- `website/docs/developer-guide/browser-replay-evidence.md` 与 `tools/browser_tool.py` / `tests/tools/test_browser_replay_evidence.py` 应作为同一功能包评审。

### 5.3 Stash 状态

```text
stash@{0}: On main: post-update-check-main-fixes-20260525-121958
stash@{1}: On private/main: hermes-update-autostash-20260525-041916
```

判定：保留合理。

说明：

- `stash@{1}` 是升级前 WIP 的安全快照；当前 WIP 已恢复，但保留 stash 可用于对照。
- `stash@{0}` 是误入 `main` 分支时 post-update-check 修复的安全快照；最终 `private/main` 已重新 self-check OK，可后续确认无用后清理。

## 6. 已执行的验证补充

### 6.1 编译验证

已对冲突/WIP 触达文件进行 py_compile：

```bash
./venv/bin/python -m py_compile \
  hermes_cli/kanban_db.py \
  agent/auxiliary_client.py \
  tests/agent/test_auxiliary_client.py \
  tests/hermes_cli/test_kanban_db.py \
  tests/run_agent/test_taskpool_dispatch_tick_runtime.py \
  tools/browser_tool.py
```

结果：通过。

### 6.2 聚焦测试

已执行：

```bash
./venv/bin/python -m pytest -q -o addopts='' \
  tests/agent/test_conductor_runtime.py \
  tests/run_agent/test_run_agent.py::TestRunConversation::test_ollama_small_runtime_context_fails_before_api_call \
  tests/run_agent/test_run_agent.py::TestRunConversation::test_cognitive_pipeline_disabled_does_not_run \
  tests/run_agent/test_run_agent.py::TestRunConversation::test_cognitive_pipeline_enabled_runs_once_and_stashes_state \
  tests/run_agent/test_run_agent.py::TestRunConversation::test_cognitive_state_injects_ephemeral_prompt_guidance \
  tests/run_agent/test_run_agent.py::TestRunConversation::test_cognitive_pipeline_exception_is_fail_soft \
  tests/hermes_cli/test_kanban_db.py::test_claim_uses_env_default_ttl \
  tests/hermes_cli/test_kanban_db.py::test_task_session_link_progress_projection_filters_without_transcript \
  tests/hermes_cli/test_kanban_db.py::test_task_session_link_is_idempotent_and_notify_subs_still_work
```

结果：

```text
17 passed, 1 warning in 10.61s
```

## 7. 后续收口建议

按风险分组做 scoped commit，不要把当前 WIP 一锅端：

1. Run Envelope / Run Controller 包
   - `agent/run_envelope.py`
   - `agent/run_controller.py`
   - `tests/agent/test_run_envelope.py`
   - `tests/agent/test_run_controller.py`
   - `tests/run_agent/test_taskpool_run_envelope.py`
   - `docs/private/run-envelope-v1-ledger.md`
   - 涉及 `hermes_cli/kanban_db.py` 的 run envelope 引用需一起评审。

2. Browser replay evidence 包
   - `tools/browser_tool.py`
   - `tests/tools/test_browser_replay_evidence.py`
   - `website/docs/developer-guide/browser-replay-evidence.md`

3. Auxiliary client 包
   - `agent/auxiliary_client.py`
   - `tests/agent/test_auxiliary_client.py`

4. Skill/reporting 文档包
   - `skills/autonomous-ai-agents/hermes-agent/SKILL.md`
   - `skills/software-development/writing-plans/SKILL.md`
   - `docs/private/*gap-analysis.md`
   - `docs/private/*proposal*.md`

5. 清理/确认 stash
   - 先确认当前 WIP 已完成 scoped commits，再删除 `hermes-update-autostash-*`。
   - `post-update-check-main-fixes-*` 可在确认 `private/main` self-check 多次 OK 后清理。

## 8. 当前最终状态

升级架构基线：健康。

遗留事项：

- Dirty WIP 仍在，需要分组提交。
- `skill_quality` 覆盖率债务存在，但不是升级阻断。
- `skill_tracking` / `steward_liveness` 为观测/触发状态，不是错误。

可对外汇报口径：

> Hermes 已完成 upstream 对齐并推送 private/main。升级后自检 16 OK、0 fixed、无 FIX_FAILED；私仓治理、多脑 registry、overlay、MCP timeout、continuation guard、conductor/OpenClaw 回归均通过。当前仅剩 skill 测试覆盖率债务与升级前恢复的 WIP dirty worktree，需要后续按功能包 scoped commit 收口。
