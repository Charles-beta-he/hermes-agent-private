# Budget Relay & Fallback Design

## Problem
When `IterationBudget` or `BudgetMeter` exhausts, `run_conversation()` breaks and asks
for a one-shot summary. Work in progress is lost. No handoff to a successor exists.

## Current State
- `IterationBudget` (L283-326): counter — `consume()`, `refund()`, `remaining`.
- `BudgetMeter` (budget_meter.py): USD gate — `can_afford()`, `record()`, `get_stats()`.
- `_budget_grace_call` (L1423): **prepared but never activated**. Allows one extra iteration.
- `_budget_exhausted_injected` (L1422): declared, **unused**.
- `ResultSpiller`: disk overflow for large tool results (hash → file).
- `delegate_task`: existing subagent dispatch.

## Design: Three Phases

### 1. Early Detection

Add threshold properties to both budget classes:

```python
# budget_meter.py + IterationBudget in run_agent.py
RELAY_WARN_THRESHOLD = 0.15  # 15% remaining

@property
def remaining_pct(self) -> float:
    return self.remaining / max(1, self.max_total)

def should_relay(self) -> bool:
    return self.remaining_pct <= RELAY_WARN_THRESHOLD
```

In the main loop (after L12226), check before the API call:
```python
if self.iteration_budget.should_relay() and not self._relay_packet_built:
    self._build_continuation_packet(messages)
```

### 2. Continuation Packet

New `AIAgent._build_continuation_packet(messages) -> dict`:

```python
packet = {
    "version": 1,
    "original_task": self._original_user_message,
    "conversation_hash": self._spiller.spill(
        json.dumps(messages), tool_name="relay"
    )["hash"],
    "summary": self._relay_summary,        # model-generated 2-3 sentences
    "completed_steps": [...],               # tool names + brief results
    "pending_steps": [...],                 # from last assistant tool_calls
    "model": self.model,
    "budget_used": self.iteration_budget.used,
    "turn_exit_reason": "budget_relay",
    "session_id": self.session_id,
}
```

Stored at `~/.hermes/relay/<session_id>_<ts>.json` (reuses ResultSpiller's pattern).

### 3. Handoff to Successor

**Strategy A — Grace call (CLI default):** Set `_budget_grace_call = True` when
`should_relay()` fires. Existing loop machinery (L12205, L12224) gives the model one
final toolless turn to produce a continuation prompt. Returns with
`turn_exit_reason: "budget_relay"` + packet path.

**Strategy B — Subagent relay (gateway/long tasks):** Before breaking, spawn fresh agent:

```python
if self._relay_config.get("auto_relay"):
    from tools.delegate_tool import delegate_task
    result = delegate_task(
        goal=f"Continue task. Packet: {packet_path}\n{packet['summary']}",
        context=json.dumps(packet),
    )
    final_response = result
```

Successor loads packet, reconstructs context from spilled history, resumes with fresh budget.

## Code Changes

| File | Change | ~Lines |
|------|--------|--------|
| `agent/budget_meter.py` | `remaining_pct`, `should_relay()`, threshold const | +12 |
| `run_agent.py` IterationBudget | `remaining_pct`, `should_relay()` | +8 |
| `run_agent.py` AIAgent.__init__ | `_relay_packet_built`, `_relay_config`, `_spiller` | +5 |
| `run_agent.py` main loop | Relay check after L12226 | +6 |
| `run_agent.py` new method | `_build_continuation_packet()` | +30 |
| `run_agent.py` post-loop L15366 | Branch on `"budget_relay"` to auto-spawn successor | +12 |
| `run_agent.py` return dict | `relay_packet_path` key | +2 |
| `config.yaml` | `budget.relay.{enabled,threshold,auto_relay}` | +3 |

~78 lines total. No new files — reuses `ResultSpiller` + `delegate_task`.

## Invariants
1. Packet built **at most once** per turn (flag-guarded).
2. Grace call is the **only** path allowing iteration after zero budget.
3. Successor gets **fresh** `IterationBudget` — no inheritance.
4. Original history preserved via `ResultSpiller` hash, not duplicated.
5. If relay disabled (default), behavior identical to today.
