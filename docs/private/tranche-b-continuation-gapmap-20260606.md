# Tranche B continuation / relay / handoff gap map — 2026-06-06

## Scope

Ratchet old private-line continuation reliability features from preserved backup `origin/backup/origin-private-main-before-upstream-align-20260606` into current upstream-aligned `private/main`, without wholesale cherry-pick.

Old commits considered:

- `4049c25b4 feat(private): add continuation and result spill overlays`
- `3b3e7cdc2 feat(continuation): add MAX_ACTIVE_CONTINUATIONS backpressure to cap active backlog`
- `54dd0b4ca Add goal-bound continuation handoff gates`
- `340a65176 fix: reset auto relay quota per root turn`
- `f60a24807 fix: let kanban workers auto-relay before blocking`
- `40d5e28a3 feat(private): harden background review and relay runtime`

## Old vs current classification

### 1. Structured continuation packet primitives

- Old surface: `agent/continuation.py`, `tests/agent/test_continuation.py`.
- Current state before Tranche B: absent; only context-compressor handoff text exists.
- Classification: still valuable.
- Port decision: restore dependency-light packet primitives with schema, reason normalization, active/completed/failed/archived state directories, status summary, goal-bound eligibility, clean-session handoff builder.
- Guardrails: summary is explicitly non-authoritative; automatic continuation requires durable fields; open-ended boundaries refuse.

### 2. Active backlog cap

- Old surface: `MAX_ACTIVE_CONTINUATIONS` in `agent/continuation.py`.
- Current state before Tranche B: absent.
- Classification: still valuable.
- Port decision: restore cap, with stricter `>=` archive threshold so a new packet never exceeds the configured active limit.
- Guardrails: overflow packets are diverted to `archived/` and tagged `metadata.overflow_archived=true`; existing packets are not deleted.

### 3. Auto-relay on budget/max-iteration exhaustion

- Old surface: `agent/conversation_loop.py`, `agent/agent_init.py`, `tests/run_agent/test_auto_relay_continuation.py`.
- Current state before Tranche B: turn ends with max-iteration summary/explainer; no bounded self-relay.
- Classification: still valuable.
- Port decision: restore bounded auto-relay helpers, relay packet writing, root-turn quota reset, recursive relay prompt, and result annotation.
- Guardrails: config-driven (`delegation.auto_relay`, `delegation.max_relay_count`), bounded by `max_relay_count`, no relay on interrupts/blank summaries/non-budget exits, quota resets only on human/root turns.
- Execution-path dependency: delegated child agents must inherit relay flags from `tools/delegate_tool.py`; otherwise parent/CLI wiring advertises relay support but real delegated work silently falls back to child defaults.

### 4. Kanban worker relay before timeout block

- Old surface: `conversation_loop.py` ordering change.
- Current state before Tranche B: budget-exhausted kanban worker records timed-out failure immediately after summary.
- Classification: still valuable but must preserve taskpool circuit-breaker semantics.
- Port decision: defer kanban timeout failure until after bounded auto-relay declines or exhausts cap.
- Guardrails: if no relay is possible, `_record_task_failure(outcome="timed_out")` still runs with existing failure-limit counters and releases the claim.

### 5. Background review relay/runtime hardening

- Old surface: large `agent/background_review.py` and CLI/gateway changes in `40d5e28a3`.
- Current state before Tranche B: current upstream has its own background review implementation.
- Classification: partially stale / too broad for this tranche.
- Port decision: not replayed in this scoped patch. Needs separate evidence-first tranche if gaps remain.

### 6. Result spill overlays

- Old surface: `agent/result_spiller.py`, `tools/retrieve_result_tool.py`, result-spill tests.
- Current state before Tranche B: `agent/result_spiller.py` already exists; retrieve tool surface not part of continuation relay closure.
- Classification: partially absorbed / out of scope for Tranche B.
- Port decision: not changed in this tranche.

## Verification gates for this tranche

Minimum gates:

- `python -m py_compile agent/continuation.py agent/conversation_loop.py agent/agent_init.py`
- `python -m pytest -o addopts='' tests/agent/test_continuation.py tests/run_agent/test_auto_relay_continuation.py -q`
- Existing nearby background-review/run-agent tests as feasible.
- `git diff --check`
- static added-line secret/eval scan
- `python3 ~/.hermes/scripts/post-update-check.py --fix`
- independent review before commit
