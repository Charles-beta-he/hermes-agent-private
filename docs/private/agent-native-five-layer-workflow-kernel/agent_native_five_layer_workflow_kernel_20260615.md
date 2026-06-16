# Agent-native five-layer lens → Hermes workflow/kernel acceptance

Task: `2026-06-15-吸收-agent-native-五层闭环为-hermes-workflow-kernel-架构验收镜头`

## Source and evidence boundary

This note absorbs the Douyin "agent-native 架构课 05" material only as an architecture/evaluation lens, not as proof that Hermes should change runtime behavior.

Evidence refs:

- `artifact:/Users/charles/.hermes/runs/douyin-agent-native-arch-20260615-212503/intake-note.md`
- `artifact:/Users/charles/.hermes/runs/douyin-agent-native-arch-20260615-212503/contact_sheet.jpg`
- `artifact:/Users/charles/.hermes/runs/douyin-agent-native-arch-20260615-212503/transcript.txt`

Uncertainty note: the transcript is local ASR and contains visible errors such as `跳用` for `调用`, `避还` for `闭环`, and `最舌权限` for `最终权限`. The normalized intake note and visual contact sheet are the higher-confidence evidence for layer labels and rules. None of the social-post claims are treated as engineering proof.

## Five-layer mapping

| Agent-native layer | Hermes current/target component | Acceptance implication | Red line |
|---|---|---|---|
| Application Surface | CLI, gateway platforms, cron triggers, dashboard, API/MCP/taskpool event ingress | Surfaces capture intent, render state, and expose read-back. They must not own privileged lifecycle mutation directly. | Surface 不做特权操作 |
| Runtime Host | `run_agent.py` loop, taskpool orchestrator, Mission ledger, permission gates, event hooks, diagnostics | Host governs session, authority, tool availability, event flow, verification, and durable artifact accounting. | Host 不伪装模型 |
| Prompt & Capability Stack | System/developer/user rules, skills, project profiles, memory, plugins, toolsets, workflow contracts | Capability is explicit, precedence-aware, and reviewable before execution. Skill/plugin convenience cannot bypass higher-priority policy. | Skill 不覆盖系统规则 |
| Agent Runtime | Provider adapters, Claude/Codex/Gemini/worker profiles, `agent_team_controller`, multimodel conductor | Runtime is replaceable behind adapter contracts: capability, launch method, IO format, failure classification. | Agent 不拥有最终权限 |
| Tool & Artifact Loop | Tool calls, tests, dry-runs, read-backs, proof logs, task notes, `docs/private` artifacts | Execution must end in durable artifacts that can be recovered, exported, edited/reviewed, and linked back into taskpool evidence. | Artifact 不只是临时附件 |

## Reusable acceptance checklist

Use this checklist when reviewing a Hermes Agent Builder / workflow-kernel feature for agent-native closure:

1. **Surface boundary** — The entrypoint expresses user intent and shows results, but every privileged write/lifecycle action is delegated to a governed host path.
2. **Host identity** — The host routes, governs, verifies, and records. It does not claim model reasoning as its own and does not silently skip model/provider failure evidence.
3. **Capability precedence** — System/developer/user rules, skills, plugins, memory, and project profiles have explicit precedence and non-goals; lower-priority capabilities cannot override higher-priority instructions.
4. **Runtime adapter contract** — Each model/agent runtime declares capability, launch method, compatibility format, failure modes, and fallback behavior.
5. **Authority separation** — Agent runtimes propose or execute bounded steps; taskpool/Mission/permission gates retain final authority over lifecycle, side effects, and closeout.
6. **Durable artifact loop** — The run writes durable artifacts (task note evidence, docs/private artifact, receipt JSON, test output, or read-back) rather than only previews or temporary attachments.
7. **Evidence bundle** — Intake note, contact sheet, transcript, implementation artifact, dry-run output, and read-back are linked with structured evidence refs.
8. **No external side effects by default** — Dry-runs and harnesses must keep `external_side_effects=false` until a separate human-gated runtime/open-gate packet authorizes more.

## Dry-run harness/example

Implemented local-only harness:

- Script: `scripts/workflow_kernel_five_layer_dry_run.py`
- Durable receipt from this tranche: `/Users/charles/.hermes/runs/workflow-kernel-five-layer/agent_native_five_layer_receipt_20260615T1531Z.json`
- Verification command: `python3 scripts/workflow_kernel_five_layer_dry_run.py --out /Users/charles/.hermes/runs/workflow-kernel-five-layer/agent_native_five_layer_receipt_20260615T1531Z.json`

The harness demonstrates the closure path:

`intent → governance → capability → execution → artifact`

It is intentionally local-only: no provider calls, no external adapter writes, no write-through integrations, and no production side effects.

## Closeout assessment

This tranche satisfies the task's implementation-ready evidence boundary:

- Mapping covers all five named layers and maps each to Hermes components.
- Checklist includes the explicit video rules: Surface/Host/Agent/Skill/Artifact red lines.
- Dry-run harness produces readable JSON plus a durable receipt under `~/.hermes/runs/`.
- Evidence bundle links back to intake note, contact sheet, and transcript, with ASR/OCR uncertainty recorded.

Residual risk: this is a governed acceptance lens and dry-run harness. It does not authorize runtime workflow-kernel mutation, external side effects, or model/provider dispatch changes.
