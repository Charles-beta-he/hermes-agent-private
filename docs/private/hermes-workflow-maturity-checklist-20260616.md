# Hermes Workflow Maturity Checklist — Context / Harness / Loop

Source evidence: `/Users/charles/.hermes/runs/douyin-dual-intake-20260616-014142/loop-engineering-lesson/intake-note.md` plus local transcript/contact sheet. This is an advisory evaluation lens, not a new runtime gate.

## Purpose

Use this checklist to classify an agent workflow before promoting it in taskpool/Mission/Hermes governance. The goal is to distinguish: (1) better prompt/context, (2) a runnable/verifiable harness, and (3) a governed loop that can safely continue, stop, or escalate based on evidence.

## Level 1 — Context Engineering

- **Responsibility:** decide what the agent sees: user goal, system rules, project profile, relevant memory, source evidence, task acceptance, red lines, and non-goals.
- **Question it answers:** “Does the agent have the right facts and constraints?”
- **Hermes components:** skills, AGENTS.md/project context, brain/project profiles, task frontmatter, evidence refs, memory/context assembly.
- **Common failure / anti-pattern:** treating richer prompt context as proof of execution; copying facts into multiple non-SSOT locations; letting social/video claims outrank canonical repo/docs.
- **Proof expectation:** source/read-back evidence that the context is durable and ranked, not a claim that the task is complete.

## Level 2 — Harness Engineering

- **Responsibility:** wrap the agent action in runnable surfaces and acceptance checks: tests, dry-runs, fixture reads, script output, browser/curl/read-back, proof-checks, and reproducible artifacts.
- **Question it answers:** “Can a bounded action be exercised and verified?”
- **Hermes components:** terminal/browser/file tools, pytest/vitest/smoke scripts, `brain_task_orchestrator.py proof-check`, selfcheck, dispatch dry-runs, local harness artifacts.
- **Common failure / anti-pattern:** using `py_compile`, hash, or generic selfcheck as feature delivery proof for implementation tasks; running a command without recording the artifact or acceptance mapping.
- **Proof expectation:** command/artifact/read-back evidence tied to task-specific acceptance criteria.

## Level 3 — Loop Engineering

- **Responsibility:** close the observe → select → act → verify → state-update → continue/stop/escalate cycle with durable state and safety boundaries.
- **Question it answers:** “Can the workflow advance repeatedly without idle-spin, duplicate action, fake completion, or unsafe side effects?”
- **Hermes components:** taskpool lifecycle (`pending/claimed/running/review/done/blocked/retry`), dispatch/eligibility, Mission flow signals, stale-claim recovery, envelope human escape, multimodel ratification, proof-governed transitions.
- **Common failure / anti-pattern:** simply rerunning agents on a timer; skipping state write-back; leaving stale claims; treating advisory PASS as delivery; bypassing human/external side-effect gates.
- **Proof expectation:** lifecycle transition evidence, idempotency/duplicate guard, read-back of new state, and an explicit stop/escalation path.

## Quick judgment questions

1. **State write-back:** after the action, is there a durable task/Mission/workflow state update that future runs read, or is progress only in chat/log text?
2. **Proof gate:** does advancement depend on task-specific evidence (test, dry-run, artifact, read-back), or only on generic health checks / model confidence?
3. **Failure and human escape:** when proof fails, quota expires, permission is missing, or external side effects are needed, is there a bounded `blocked/retry/handoff` path instead of silent retry or fake done?
4. **Idempotency:** can the same event/tick run twice without duplicate external actions or conflicting claims?
5. **Authority boundary:** does the loop distinguish advisory/documentation work from implementation/runtime/write-through authority?
6. **SSOT discipline:** is there one canonical task/evidence artifact, with prompts/dashboards derived from it rather than parallel truths?

## Classification shortcut

- If the main improvement is better instructions, retrieved facts, source ranking, or acceptance wording: **Context**.
- If the workflow can run a bounded action and show task-specific verification but still needs a human/operator to decide every next step: **Harness**.
- If the workflow can safely decide the next bounded step from state, execute it, verify it, update state, and stop/escalate on red lines: **Loop**.

## Red lines

- Do not promote this checklist itself into a hard lifecycle gate without a separate ratified design and regression tests.
- Do not use “Loop Engineering” terminology to weaken taskpool proof, permission, Mission envelope, or external side-effect gates.
- Do not treat the 11-second source video as an authoritative taxonomy; it is only a vocabulary signal cross-checked against existing Hermes governance patterns.
