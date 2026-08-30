# Whole-goal self-advance hardening v2.1 — SSOT

Date: 2026-08-09
Status: implementation candidate
Owner surface: Hermes active-turn auto-relay
Base commit: `7d989d45531fa05b491c027ccaea8069b05d873f`

## Objective model

### Goal

Preserve bounded active-turn continuation after an explicitly authorized whole-goal request, while preventing model-authored todo metadata, replayed tool payloads, synthetic relay prompts, questions, revocations, or unrelated user turns from creating or reviving continuation authority.

### Success criteria

1. Only a genuine root user directive can set `whole_goal` continuation mode.
2. The model-facing `todo` tool cannot grant or revoke continuation mode.
3. Caller-supplied/replayed todo tool output cannot hydrate `whole_goal` authority.
4. A later explicit revoke clears the mode.
5. Questions and ordinary messages do not grant or revoke authority.
6. Unrelated turns do not trigger a successor relay merely because a prior goal still has open todos.
7. Synthetic auto-relay successor turns may continue only an already-active trusted `whole_goal` mode.
8. Interrupt, relay budget, open-work, hard-human-gate, and existing tool/AuthorityGrant gates remain fail-closed.

### Constraints and non-goals

- Local reversible change only; no push, publish, deploy, gateway restart, taskpool/mission write, cron change, or external dispatch.
- Do not make active-turn relay a durable background runner. Cross-session/background advancement belongs to Mission/taskpool/cron control planes.
- Do not infer side-effect authority from continuation authority. `whole_goal` only allows bounded continuation to the next turn; tool, approval, credential, deployment, destructive, and external-send gates remain separate.
- Do not mutate the system prompt or add a core model tool.
- Do not claim platform certification or runtime authority.

## Source facts

- Predecessor `d98c474cf` inferred self-advance from natural-language substrings in user history and assistant prose.
- Commit `7d989d455` replaced that decision with `TodoStore.continuation_mode` plus open todos.
- In `7d989d455`, the model-facing todo schema can set `continuation_mode=whole_goal` and hydration trusts a continuation value replayed from tool output.
- In `7d989d455`, finalization checks persistent mode plus open todos, but has no per-root-turn continuation signal; therefore a later unrelated answer can relay while old todos remain open.
- Focused baseline on the exact base: 60 tests passed.

## Business rules

### Trusted continuation state

`TodoStore.continuation_mode` is runtime-owned state, not model authority.

- `manual` is the default and fail-closed state.
- A strict root-user directive may set `whole_goal`.
- A strict root-user revocation sets `manual`.
- Neutral/question/quoted text leaves persistent mode unchanged but does not authorize relay for that turn.
- Synthetic auto-relay turns cannot change mode; they may inherit an already-active mode only when the current relay chain began from a continuation-authorized root turn.
- Replacing todo items must not create authority. Goal-scope changes that require authority widening remain human-gated by existing control planes.
- Assistant prose can deny relay when it exposes a hard human gate, but can never grant relay authority.

### Turn relay admission

A self-advance checkpoint is admitted only when all are true:

- non-empty final response;
- not interrupted;
- trusted persistent mode is `whole_goal`;
- this root turn is continuation-authorized, or it is a synthetic successor inheriting trusted mode;
- at least one todo is `pending` or `in_progress`;
- the final response does not expose a human-confirmation, credential, publish, payment, TestFlight/App Store, production-destructive, or equivalent hard gate;
- ordinary auto-relay budget/exit-reason checks pass.

### Negative cases

The following must not relay:

- `是否会自动推进？`
- `之前好像有过自推进方案，是否改改能完善？`
- `停止自动推进，只汇报进度`
- a replayed todo payload containing `{"continuation":{"mode":"whole_goal"}}`
- a model todo call requesting `continuation_mode=whole_goal`
- a model todo call requesting either continuation mode
- an unrelated question/answer while old open todos remain
- a synthetic relay prompt when persistent mode is `manual`
- a final response that says it needs user authorization, TestFlight approval, credential entry, or production deletion
- interrupted turns, completed/cancelled-only todos, or relay budget exhaustion

## Chosen path

1. Keep structured mode in `TodoStore`, but remove model/replay write authority.
2. Add a deterministic root-user directive reducer in `conversation_loop`.
3. Record a per-turn continuation-admission boolean on the agent.
4. Pass both trusted persistent mode and per-turn admission into the finalizer decision.
5. Prove negative cases first, then implement the minimum source changes.

## Consequences and risks

- Conservative false negatives are acceptable: ambiguous prose stays manual for that turn.
- Cross-process restoration of whole-goal mode intentionally fails closed until a separately authenticated durable mission/continuation authority record exists.
- Existing stored tool payloads that contain continuation metadata no longer grant authority.
- A genuine user may need to restate an explicit continuation directive after process/session reconstruction.

## Verification contract

- RED tests reproduce model self-grant, forged hydration, question grant, revocation, synthetic inheritance, and unrelated-turn relay risks.
- Focused GREEN suite covers conversation loop, todo store, turn finalizer, and hydration.
- Ruff and `git diff --check` pass.
- Candidate identity is frozen after tests.
- Independent read-only review binds to the exact candidate identity before local commit.
- Completion statement remains limited to this local slice; Hermes platform remains `NOT_CERTIFIED` and no runtime authority is granted.
