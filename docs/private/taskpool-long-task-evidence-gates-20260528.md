# Taskpool long-task / high-tool-call evidence gates

Task: `2026-05-27-define-long-task-and-high-tool-call-evidence-gates-for-hermes-taskpool-runs`

## Scope

This note defines a narrow, documentation/schema-validation gate for taskpool items that claim long-running autonomy, very high tool-call capacity, resume durability, or similar model/runtime robustness. It intentionally does **not** change provider routing, default models, or leaderboard selection.

## Trigger phrases

A task candidate should be treated as a long-task claim when its title, objective, `capability_delta`, or `acceptance_evidence` claims any of:

- long task / long-running task / long horizon execution;
- high tool-call count, e.g. `1000+ tool calls`, `1000 tool-call`, or equivalent;
- resume/recovery of interrupted agent runs;
- checkpointed multi-hour/multi-step execution;
- model or runtime robustness claims based only on a benchmark/article/report.

## Required evidence slots

A long-task claim is acceptable only when the candidate or evidence bundle includes all five slots below. These slots are deliberately concrete so the taskpool does not accept vague claims such as “Model X can do 1000 tool calls” as implementation proof.

| Slot | Required content | Examples |
|---|---|---|
| `checkpoints` | durable intermediate state or checkpoint markers | checkpoint file paths, run ledger rows, state snapshots |
| `tool_logs` | observable tool-call or command trace | tool-call count summary, command log, worker job log |
| `resume_state` | restart/resume semantics | continuation packet, claim lease cleanup, recovery marker |
| `error_classes` | classified failure modes and retry boundary | timeout/auth/quota/tool-schema/validation classifications |
| `final_verification` | task-specific verification after the long run | pytest/vitest/curl/browser/read-back/regression tied to the claim |

Weak-only evidence remains insufficient: `py_compile`, generic `selfcheck`, proof hashes, model discussion PASS, or unlinked benchmark claims do not satisfy the final slot by themselves.

## Acceptance examples

### Reject: vague leaderboard claim

```json
{
  "title": "Adopt Qwen long-task capability",
  "capability_delta": "Qwen can handle 1000+ tool calls, so Hermes should trust it for long tasks.",
  "acceptance_evidence": "benchmark article says it works"
}
```

Expected result: reject with missing `checkpoints`, `tool_logs`, `resume_state`, `error_classes`, and `final_verification`.

### Accept: checkpointed taskpool evidence claim

```json
{
  "title": "Harden Hermes long-task execution evidence",
  "capability_delta": "Hermes records checkpointed long-task evidence before closing high tool-call claims.",
  "acceptance_evidence": "checkpoint:/tmp/run.state; tool-log:/tmp/tools.jsonl count=1200; resume-state:continuation-packet; error-class:quota_timeout_retryable; final-verification:pytest tests/test_long_task_evidence.py"
}
```

Expected result: accept because every required evidence slot is present and the final verification is task-specific.

## Non-goals / anti-drift

- Do not alter provider defaults based on the Qwen3.7-Max signal.
- Do not treat multimodel PASS, advisory reports, or py_compile/hash proof as product delivery evidence.
- Do not make this fail-closed for every taskpool item; it applies only when the candidate makes long-task/high-tool-call/resume robustness claims.

## Verification in this tranche

- Implementation artifact: `scripts/taskpool_long_task_evidence_validator.py`.
- Regression artifact: `tests/test_taskpool_long_task_evidence_validator.py`.
- The validator rejects the vague example above and accepts the checkpoint/tool-log/resume/error/final-verification example above.
