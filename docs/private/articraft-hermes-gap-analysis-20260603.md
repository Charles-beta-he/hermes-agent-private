# Articraft → Hermes 制造脑 / 3D 资产工作台参考评估

日期: 2026-06-03
来源: https://github.com/mattzh72/articraft
固定版本: `0ea5c9890f6e1c17ff89838a0485830e0b2b9e87`
本地只读证据目录: `/Users/charles/.hermes/runs/articraft-reference-review-20260603`

## 1. Live source evidence

GitHub API 元数据（2026-06-03 抓取）:
- `full_name`: `mattzh72/articraft`
- description: `An Agentic System for Scalable Articulated 3D Asset Generation`
- language: Python
- stars/forks: 1205 / 147
- default branch: `main`
- license: Apache-2.0
- pushed_at: `2026-06-02T22:10:59Z`

已读源码/文档:
- `README.md`: Articraft 将 articulated 3D asset creation 转为 LLM 代码生成工作流；支持文本/图片输入、dataset/workbench、Three.js viewer；安全提示明确 `model.py` 编译会执行 Python。
- `AGENTS.md` / `CLAUDE.md`: 给出 repo 结构、CLI、batch、viewer、external-agent data workflow、测试/commit 安全约束。
- `docs/architecture.md`: 明确 `agent/`, `storage/`, `sdk/`, `viewer/`, `cli/`, `tests/` 分层。
- `EXTERNAL_AGENT_DATA.md`: 外部 agent 必须走 `uv run articraft external init/fork/check/finalize`，禁止手工写 records/traces；要求 read SDK docs/examples、compile/check 循环、只编辑 CLI 打印的 active `model.py`。
- `docs/image_conditioned_generation.md`: 图片输入可进入 generate / dataset run / fork / draft，但 batch CSV v1 不支持 `image_path`。
- `docs/dataset_generation.md`: batch spec CSV + stable `row_id` + resume state + concurrency + cost cap。
- `SECURITY.md`: `data/records/**/model.py` 是可执行 Python；不可信记录必须 sandbox/VM/container。
- `pyproject.toml`: Python `>=3.11,<3.13`；关键依赖包括 CadQuery, trimesh, manifold3d, python-fcl, FastAPI, Three.js viewer 支撑。

## 2. Architecture signals extracted from source

### 2.1 Pipeline

Articraft 的核心流水线是：
1. Prompt / local reference image 进入 `articraft generate`, `dataset run`, `fork`, `draft` 或 external workflow。
2. `agent/` 构建 provider-specific requests，运行多轮 tool loop。
3. 生成/编辑 `model.py`。
4. `agent/compiler.py` 执行 `model.py`，导出 URDF/mesh/viewer assets，并返回 compile/test/geometry 信号。
5. `storage/` 按 canonical record layout 持久化 record/revision/provenance/cost/input/cache/search。
6. `viewer/api` + `viewer/web` 提供本地 visual inspection、dataset metadata、records/history/search。

### 2.2 分层

- `agent/`: 生成 runtime、provider adapters、prompt compiler、tools、cost tracking、batch runner。
- `agent/tools/`: `read_file`, `write_code`, `apply_patch/replace`, `compile_model`, `probe_model`, `find_examples`；工具集随 provider 调整。
- `sdk/` / `sdk/_core/`: articulated-object SDK 和 CadQuery/mesh/URDF/geometry/QC 基础。
- `storage/`: records, revisions, categories, datasets, batch specs, manifests, LFS, search index。
- `viewer/`: FastAPI + React/TypeScript/Three.js 本地审查界面。
- `cli/`: `articraft` 顶层入口，统一 generate/fork/compile/dataset/external/data/viewer。
- `plugins/articraft/skills`: repo 自带面向 agent 的 skills（authoring/viewer/dataset）。

### 2.3 可吸收机制

- **Record/revision/provenance/cost/input/cache/search 一体化数据模型**：比 Hermes 当前制造脑零散 evidence packets 更适合承载“候选模型 → 预览 → mesh QA → slice → external-control packet”的完整可追溯链。
- **Compile/probe feedback loop**：把生成代码的失败信号压缩回 agent，避免只靠最终成品判定。
- **External-agent harness contract**：允许外部 agent 参与数据创作，但必须通过命令入口生成 record、只改 active model、保留 provenance、禁止手工写 traces。
- **Dataset batch + resume + stable row_id**：适合 Hermes 未来批量生成/评估 3D 资产候选、但不能直接套到真实打印动作。
- **Viewer-first local review**：把 Three.js viewer 作为视觉证据界面，适合接入 Hermes browser-harness / vision_analyze / manufacturing QA。
- **Security note explicitness**：把 `model.py` 执行定义为安全边界，正好对应 Hermes 不可信代码执行/物理动作红线。

## 3. Hermes current gap

Hermes 已有制造脑、mesh QA、Bambu/X2D dispatch gate、Blender/MCP studio bridge、external-control boundary，但缺少一个 Articraft 风格的本地 3D 资产工作台 SSOT：

- 候选模型、生成提示、来源/license、hash、预览图、mesh QA、slice guard、用户审批、外部控制 packet 目前分散在脚本输出和 task notes 中。
- 有安全 gate，但没有统一的 record/revision/provenance 数据模型来表达候选资产生命周期。
- 有 `manufacturing_preflight.py`, `manufacturing_mesh_qa.py`, `bambu_3mf_preprint_guard.py`，但没有类似 viewer 的 review surface 绑定 evidence hash 与 human approval。
- 有 taskpool 和多模型，但 3D 资产生成/修改任务缺少外部 agent authoring contract，容易让 agent 直接写 STL/3MF 或跳过 evidence record。

## 4. Mapping to Hermes primitives

- Articraft `storage/records/<id>/record.json` → Hermes `manufacturing_asset_record` / `candidate_model_record`。
- Articraft `revisions/<rev>/model.py + provenance.json + cost.json + inputs/` → Hermes model candidate revision: source, prompt/image, local artifact path, sha256, provider/model, human approval state。
- Articraft `compile_model` → Hermes compile/mesh-QA/slicer-preview/preprint-guard feedback adapters。
- Articraft viewer → Hermes local review surface: browser/Three.js/vision evidence; not a physical-action gate。
- Articraft external workflow → Hermes external-agent authoring contract for generated 3D assets: init/fork/check/finalize, never hand-write traces, never mutate unrelated records。
- Articraft batch/resume → Hermes taskpool batch asset evaluation with stable row IDs; event-driven taskpool should trigger from task/record status transitions, not cron polling.

## 5. Smallest verifiable integration slice

Do **not** integrate Articraft directly first. Build a Hermes-native, read-only prototype:

1. Define `manufacturing_asset_record_v0` JSON schema:
   - `record_id`, `revision_id`, `prompt`, `source_image`, `source_repo/url`, `license/provenance`, `local_artifacts[]`, `sha256`, `artifact_kind` (`stl`, `3mf`, `glb`, `model_py`, `slicer_report`, `preview_image`), `status`, `approvals[]`, `safety_flags[]`。
2. Add a pure-local manifest writer around existing Hermes scripts; no execution of untrusted external code.
3. Add compile/mesh/slicer/preprint guard result refs as evidence fields, not hidden logs。
4. Add viewer-proof adapter that records screenshot path + vision findings + human approval ref。
5. Taskpool acceptance requires record manifest + proof refs + manufacturing verdict + external-control packet only when a physical action is separately approved。

## 6. Red lines / anti-drift constraints

- 不直接导入 Articraft 数据集或执行其 `data/records/**/model.py`。
- 不把 successful compile / viewer render / LLM answer 当作可打印或可运动批准。
- 不绕过 Hermes manufacturing/electrical/external-control brain。
- 不把 external agent 的自述当作 proof；必须读回 manifest、hash、QA report、preview evidence。
- 不把 batch/resume 机制用于真实设备自动执行；真实动作只由 fresh telemetry + external-control packet + confirmation token 触发。
- 不把 Articraft SDK/依赖（CadQuery/Vtk 等）直接塞入 Hermes core；先保持适配器/manifest 层。

## 7. Suggested taskpool shape

任务: `评估 Articraft 对 Hermes 制造脑/3D 资产工作台的参考价值`

- lane: `evolution`
- project: `hermes-agent`
- architecture_target: `local-brain`
- priority: `P2`
- requires_multimodel: yes
- capability_delta: 3D asset record/workbench + compile/viewer evidence spine + external-agent authoring contract
- non-goal: 不集成 Articraft、不运行生成、不真实打印、不改 external-control dispatch
- acceptance: gap matrix + `manufacturing_asset_record_v0` schema/prototype + proof gate checklist + multi-model pass

## 8. Follow-up questions for implementation round

- Hermes 的资产 record 应落在 `~/.hermes/brains/manufacturing/records/` 还是 project-local runs + indexed manifests？
- Viewer proof 是复用 browser-harness 截图，还是做一个轻量 FastAPI/Three.js viewer？
- `model.py`/CAD generation 是否需要单独 sandbox profile，而不是复用 terminal 默认环境？
- external agent authoring contract 应该是 taskpool adapter、skill、还是 manufacturing CLI 子命令？
