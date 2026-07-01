---
name: agent-eval-dynamic-workflow-harness
description: "Use for Hermes Agent self-architecture work that designs, implements, or reviews Agent Eval / Dynamic Workflow / Harness reliability systems: eval case registry, trajectory capture, hybrid graders, shadow workflow router, feedback ledger, sandboxed harness, and regression gates."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    scope: hermes-agent-self-architecture
    applicability: "Hermes Agent runtime/skills/taskpool/gateway reliability design and evaluation harness work"
    anti_conditions: "ordinary project feature work, simple Q&A, trivial edits, generic non-Hermes agent articles without planned Hermes landing"
    tags: [agent-eval, dynamic-workflow, harness, reliability, regression, workflow-router]
    related_skills: [objective-model-path-verification, hermes-agent, abstraction-governance, experience-ratchet]
---

# Agent Eval + Dynamic Workflow Harness

## Scope / Applicability / Anti-conditions

Scope: **Hermes Agent self-architecture only**.

Applicability: use this skill when designing, implementing, or reviewing Hermes reliability systems around Agent Eval, Dynamic Workflow, Harness, workflow routing, trajectory capture, graders, regression gates, and governed experience ratchets.

Anti-conditions:
- Do not use for ordinary project feature work unless the task explicitly asks to improve Hermes itself.
- Do not use for simple Q&A, trivial edits, or one-off media summaries.
- Do not apply to generic non-Hermes agent articles without a planned Hermes landing boundary.
- Do not let this skill force eval/taskpool ceremony onto evidence-clear product fixes.

## Overview

The goal is not more prompt text. The goal is measurable Hermes reliability through replayable cases, structured trajectories, deterministic graders, shadow routing, and governed feedback loops.

Core principle:

> Hot path minimal. Reliability logic off-path first, then shadow, then gated enforcement.

## Architecture Components

1. **Eval Case Registry**
   - Versioned cases with task input, context, allowed tools, forbidden side effects, expected artifacts, workflow label, risk level, grader set, and acceptance criteria.
   - Cases should be redacted, replayable, and small enough for frequent regression.

2. **Trajectory / Evidence Recorder**
   - Non-blocking capture of router decision, loaded skills, tool calls, tool summaries, objective gate trigger, diffs/tests/logs, final response, side effects, and boundary stops.
   - Start from offline/session-log extraction before adding live hooks.
   - Records must be compact, redacted, hashable, and replay-oriented.

3. **Isolated Eval Harness Core**
   - Independent CLI/script, never synchronous gateway hot path.
   - Runs cases in sandbox/worktree/virtualized state.
   - Default-deny real memory, taskpool, cron, send_message, external publishing, and destructive file writes unless a case explicitly permits them.

4. **Hybrid Grader Suite**
   - Code/Tool Grader first: exit code, schema, file existence, tests, diff shape, HTTP status, tool-call contract.
   - LLM Grader second: semantic quality, evidence sufficiency, hallucination, workflow fit, communication quality; always rubric-based.
   - Human Grader for high-risk, product/UX, memory/skill/runtime changes, and model disagreement.

5. **Shadow Workflow Router**
   - Classifies Direct / Plan / Research / Objective-gated / Incident / Review / Eval.
   - Shadow-only first: log predicted route and confidence without affecting execution.
   - Suggest-mode next: expose route recommendation and verification burden.
   - Limited enforcement only after regression evidence and a rollback switch exist.

6. **Feedback & Improvement Ledger**
   - Eval failures become records: failure type, evidence, root cause, suggested fix layer, before/after score, and whether to add a regression case.
   - Does not directly mutate memory, skills, config, taskpool, or runtime.
   - Routes candidates to skill patch, runtime issue capture, docs/ADR, taskpool candidate, or discard.

7. **Regression Database / Dashboard**
   - Stores eval runs, case hashes, trajectory hashes, grader scores, routing decisions, baseline diffs, and drift alerts.
   - Post-update regression should show no degradation on sacred core cases.

8. **Governed Experience Ratchet**
   - Repeated high-confidence lessons propose skill patches or runtime changes.
   - Human/scoped review remains required for durable behavior changes.
   - Never save task-local outcomes to memory.

## Rollout Pattern

### 0–2 weeks: foundation, no live behavior change

- Define eval case schema and 10–20 seed cases.
- Extract trajectory/evidence from existing session/tool logs where possible.
- Build deterministic Code/Tool Grader prototypes for 3–5 cases.
- Define sandbox policy and prohibited side effects.
- Add one local dry-run smoke command.

Gate: harness distinguishes at least 3 intentionally broken cases from passing cases and writes nothing to real memory/taskpool/cron/send_message.

### 1–2 months: shadow routing and hybrid grading

- Run 30–50 cases with artifacts.
- Add shadow router logs without affecting execution.
- Add rubric-based LLM graders calibrated by human spot checks.
- Store baseline/trend in regression DB.
- Deduplicate failures in feedback ledger.

Gate: router false-direct rate is known; LLM/human agreement is acceptable or rubrics are revised; sacred case pass rate is stable.

### 3–6 months: limited enforcement and self-improvement loop

- Enforce only low-risk route decisions with an escape hatch.
- Require evidence spine for objective-gated tasks.
- Include harness in post-update/self-update regression.
- Let Experience Ratchet propose skill patches from repeated eval findings.
- Publish dashboard/report for regressions, drift, and improvement ROI.

## Verification Strategy

- **Case-level**: run pass/broken pairs; broken cases must fail for the expected reason.
- **Tool-level**: assert tool-call contracts and side-effect boundaries.
- **Router-level**: track false-direct rate, token/time deltas, completion rate by route, and fallback frequency.
- **Grader-level**: compare Code/LLM/Human grader agreement; sample LLM high scores for human review.
- **System-level**: post-update regression on sacred core cases before declaring Hermes healthy.

## Red Lines

- Do not put eval or grader logic in the synchronous Telegram/gateway hot path.
- Do not make LLM-as-judge the primary truth source.
- Do not auto-create taskpool tasks from every eval failure.
- Do not auto-patch skills/memory/config/runtime from grader output.
- Do not build a heavy router that costs more than the task.
- Do not optimize for eval score without measuring real task/user outcomes.
- Do not treat social-media diagrams as official technical evidence.

## Output Shape for Design Reviews

```text
目标: What reliability failure are we reducing?
Case registry: what cases prove it?
Trajectory fields: what must be recorded?
Graders: code/tool vs LLM vs human and why?
Router mode: off / shadow / suggest / limited-enforce?
Sandbox: what side effects are blocked?
Feedback landing: skill / issue / docs / taskpool candidate / discard?
Verification: commands, broken-case checks, regression threshold.
```

## Existing Hermes Implementation

Scope: **Hermes Agent self-architecture only**. Applicability: use these paths only inside a Hermes Agent checkout when evaluating or extending the offline Agent Reliability Harness. Anti-condition: do not apply these commands to unrelated product repositories or non-Hermes agent frameworks.

Default-off/off-hot-path scaffold landed in Hermes Agent:

- Module: `agent/reliability_harness.py`
- Tests: `tests/agent/test_reliability_harness.py`
- Seed cases: `evals/cases/*.json`
- CLI smoke:
  ```bash
  python -m agent.reliability_harness list --cases-dir evals/cases --json
  python -m agent.reliability_harness capabilities --json
  python -m agent.reliability_harness run --cases-dir evals/cases --runs-dir /tmp/hermes-eval-smoke --case direct-basic-answer --response-text OK --json
  ```
- `capabilities` prints the Google agents-cli-inspired local contract: skill-pack IDs plus lifecycle command IDs (`intake`, `scaffold`, `implement`, `eval-generate`, `eval-grade`, `edge-profile`, `edge-benchmark`, `edge-verify`, `edge-activation-packet`, `edge-kill-switch-dry-run`, `edge-status-readback`, `edge-open-boundary-checklist`, `edge-authority-discover`, `deploy-proposal`, `observe`, `ratchet`). It is contract/readback only; it does not load skills, mutate taskpool/memory/cron, or deploy externally.
- `capabilities` also exposes the PicoClaw-inspired `local_edge_worker` edge-runtime profile. Treat it as a local/default-off profile and benchmark contract only: it may record device class, memory/boot budget, profile readback, fixture command simulation, and kill-switch dry run; it must not enable device control, MCP write authority, external dispatch, credential entry, publish/deploy/push, or destructive operations without a later exact authority gate.
- `edge-benchmark --runs-dir <dir> --profile local_edge_worker --json` is the first executable dry-run surface for that profile. It writes a local `hermes_edge_benchmark_receipt_v1` receipt with `memory_rss_mb`, `boot_ms`, profile id, resource budget, and closed gate readback. Applicability: benchmark/readiness evidence only. Anti-condition: it still must not start workers, install PicoClaw, call MCP, control devices, mutate taskpool/cron/gateway, or dispatch externally.
- `edge-verify --receipt <receipt.json> --json` is the read-only promotion-gate verifier for edge benchmark receipts. It writes no receipt, starts no worker, and returns `hermes_edge_benchmark_verification_v1` with pass/blocked checks for schema, known profile, memory/boot targets, and closed side-effect gates. Even when `status=pass`, `promotion_ready` remains false until a later exact runtime activation authority packet exists.
- `edge-activation-packet --verification <verification.json> --target-id <target> --runs-dir <dir> --json` writes a non-executing `hermes_edge_activation_packet_v1` template only when verifier status is `pass`. The packet keeps `execute=false`, requires rollback/kill-switch/readback, and records closed gates. It is a human-gate artifact, not runtime activation.
- `edge-kill-switch-dry-run --activation-packet <packet.json> --runs-dir <dir> --json` writes a local `hermes_edge_kill_switch_dry_run_receipt_v1` only for non-executing activation packets. It simulates disabled readback, records rollback verification, and fails closed on `execute=true`; it does not start/stop/contact any runtime.
- `edge-status-readback --benchmark <receipt> --verification <verification> --activation-packet <packet> --kill-switch-receipt <receipt> --json` is the terminal read-only closed-ladder readback. It returns `hermes_edge_status_readback_v1`, keeps `execute=false` and `promotion_ready=false`, and points to `exact_runtime_activation_packet_execute_true` as the only remaining authority boundary.
- `edge-open-boundary-checklist --status-readback <status.json> --runs-dir <dir> --json` writes a local `hermes_edge_open_boundary_checklist_v1` only when status readback is ready. It is not permission to open the runtime; it records exact-target/rollback/readback/human-ack requirements while preserving closed gates.
- `edge-authority-discover --scan-dir <dir> [--scan-dir <dir> ...] --json` scans existing local artifacts for profile/target/execute=true/readback candidates before asking the user for missing values. It is read-only and reports blockers such as missing or invalid `execute=true` packets.
- Regression command:
  ```bash
  uv run --extra dev pytest tests/agent/test_reliability_harness.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
  ```

Keep this harness offline/default-off unless a later task explicitly authorizes live gateway/taskpool integration.

## Local-Brain Sync

Scope: **Hermes Agent self-architecture only**. Applicability: use when a harness/control-plane landing changes durable structure that future Hermes sessions must retrieve. Anti-condition: do not mirror raw session logs, transient failures, or ordinary product-task progress into the local brain.

Mirror durable harness structure into the local brain as L2 project knowledge plus L6 review evidence. Use `references/local-brain-sync.md` for the note shape, validation commands, and pitfalls.

## Source SSOT

Canonical planning note: `/Users/charles/.hermes/docs/private/agent-eval-dynamic-workflow-longterm-20260609.md`.
Landing note: `/Users/charles/.hermes/docs/private/agent-eval-harness-full-landing-20260609.md`.
