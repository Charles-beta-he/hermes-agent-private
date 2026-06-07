# Data Agent L0-L5 source resolution — 2026-06-08

## Scope

Task: `2026-06-07-data-agent-l0-l5港科大开源项目源解析与-hermes-数据代理能力分层评估`

User source: WeChat Channels URL `https://weixin.qq.com/sph/AyY9OlX4Xt`; prior browser/vision evidence showed title text roughly “数据Agent到底是什么 港科大568星开源项目彻底讲透 从L0到L5的进化路径曝光”.

## Search performed

GitHub repository/code searches performed without installing or running third-party code:

```text
gh search repos 'HKUST data agent SIGMOD 2026'
gh search repos 'HKUST Data Agent L0 L5'
gh search repos '"Data Agent" "SIGMOD 2026"'
gh search repos '"L0" "L5" "data agent"'
gh search repos '"数据代理" "L0" "L5"'
gh search repos '"港科大" "SIGMOD 2026" "Agent"'
gh search repos '"Data Agent" "568" github'
gh search code  with the same query families
```

Result: no reliable GitHub repository/code hit matching the full clue set: HKUST/港科大 + Data Agent + 568 stars + SIGMOD2026 + L0-L5.

## Candidate rejected / watchlist

Candidate observed during broader search:

- Repository: `zwq2018/Data-Copilot`
- URL: https://github.com/zwq2018/Data-Copilot
- Description: “Data-Copilot: Bridging Billions of Data and Humans with Autonomous Workflow”
- License: MIT
- Default branch: `main`
- Stars at inspection: 1514
- Forks at inspection: 152
- README/paper: arXiv `2306.07209`, Chinese financial-market data workflow, autonomous interface/tool design and dispatch.

Why this is not accepted as the video source:

- Star count is materially different from the video clue (`568` vs observed `1514`).
- README inspection did not surface `HKUST`, `SIGMOD`, `L0`, `L5`, or `568` terms.
- The repo may still be useful as a generic data-copilot reference, but it does not satisfy the source identity gate for this task.

## Current decision

The source identity remains unresolved. The task must not be considered implementation-ready based only on the video title. Any lifecycle closeout should be interpreted as “source-resolution attempt recorded / no install/no execution / unresolved requires human or stronger source evidence”, not as “the HKUST project was confirmed”.

## Hermes mechanism-level observations that can still be reused safely

Even without canonical source identity, the visible L0-L5 framing can be kept as a hypothesis for later review:

1. L0: passive data lookup / query answering.
2. L1: tool-mediated data retrieval.
3. L2: cleaning/transformation/visualization pipeline.
4. L3: multi-step analysis workflow with evidence.
5. L4: autonomous planning and tool orchestration.
6. L5: policy-gated long-running data agent with provenance, rollback, and human review.

Hermes mapping, if a source is later confirmed:

- local-brain: provenance-preserving retrieval and replay fixtures.
- tool routing: explicit tool schema, fail-closed unknown tool handling.
- taskpool: evidence-first data tasks, ratification before durable asset promotion.
- proof gate: deterministic fixture with known input/output and drift alarms.

## Anti-drift boundary

- Do not install or execute any candidate data-agent repo during intake.
- Do not treat `zwq2018/Data-Copilot` as the HKUST/SIGMOD/L0-L5 project without stronger evidence.
- Do not close future implementation tasks from WeChat visual text alone.
- Require one of: canonical GitHub URL from video/comment/source, paper URL with authors/affiliation matching the claim, or a reproducible public page connecting all clue terms.
