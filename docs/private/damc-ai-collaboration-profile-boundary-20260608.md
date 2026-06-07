# DAMC-style AI collaboration profile — safety and MVP boundary — 2026-06-08

## Scope

Task: `2026-06-07-damc-风格个人-ai-协作画像蒸馏价值-抗蒸馏-ai驾驭能力评估`

Input evidence is video/description-level only: four dimensions were captured as 蒸馏价值、抗蒸馏能力、AI驾驭能力、AI时代职业规划, plus Sell Skills / team ranking hints. No canonical source repository or formal spec has been confirmed.

## Decision

Treat DAMC as a class-level framework idea, not as a product/source implementation. The safe Hermes absorption is a privacy-preserving evaluation/report template, not an automatic personality/profile gate.

## Candidate metric classes

1. Work artifact leverage: reusable components, tests, docs, review comments, design decisions.
2. Skill graph leverage: where user/Hermes workflows repeatedly succeed and should become skills.
3. Prompt/control-plane leverage: ability to give clear acceptance criteria, evidence boundaries, rollback lines.
4. AI orchestration leverage: knowing when to use main agent, subagent, multimodel review, browser, terminal, or no tool.
5. Anti-distillation moat: domain judgment, private context, taste, responsibility boundaries, production constraints.
6. Career/market mapping: role direction, portfolio assets, remote-work evidence, finance/blockchain relevance.

## Red lines

- Do not write personality/MBTI-style labels into global mandatory rules.
- Do not upload private conversations, code, or employment material to third-party scoring systems.
- Do not rank users/teams without explicit consent and visible rubric.
- Do not make the profile a hidden gate that changes task execution without auditability.
- Do not persist raw sessions; use aggregated, user-visible evidence only.

## Hermes MVP template

A safe local-only report should contain:

- Inputs used: explicit files/sessions/tasks selected by user.
- Exclusions: private data omitted, no third-party upload, no hidden scoring.
- Evidence summary: public artifacts or local taskpool/session facts with paths/IDs.
- Scores as advisory ranges, not identity labels.
- Improvement actions: candidate skills, workflow changes, portfolio proof items.
- Delete/export controls: report path and how to remove it.

## Acceptance boundary

This task can close as advisory only after the above boundary is recorded. Any later implementation requires separate consent and a separate task with:

- explicit allowed input set,
- local-only script or notebook,
- no secret/session dump in prompts,
- user-readable generated report,
- delete path.

## Decision

Advisory evaluation is complete enough for task closeout. No implementation or durable user profile write should happen from this task.
