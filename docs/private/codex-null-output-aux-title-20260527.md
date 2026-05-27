# Codex null response.output auxiliary title regression (2026-05-27)

## Trigger

User reported that the main Codex null-output streaming salvage fixed the user-visible turn, but a post-turn warning still appears:

`Auxiliary title generation failed: 'NoneType' object is not iterable`

## Evidence

- Main runtime fix is in `agent/codex_runtime.py`: it handles `response.output is None` and catches the OpenAI SDK streaming `TypeError("'NoneType' object is not iterable")` in `run_codex_stream`.
- Title generation uses `agent/title_generator.py::generate_title`, which calls `agent.auxiliary_client.call_llm(task="title_generation", ...)` and then reads `response.choices[0].message.content`.
- When the main runtime is Codex, auxiliary auto routing may use `agent/auxiliary_client.py::_CodexCompletionsAdapter`, a chat.completions shim over `client.responses.stream(...)`.
- In `_CodexCompletionsAdapter.create`, the current backfill only checks `isinstance(_output, list) and not _output`, then later iterates `for item in getattr(final, "output", [])`. If `final.output is None`, `getattr(..., [])` returns `None`, causing the reported TypeError.
- The same OpenAI SDK eager parser can also raise `TypeError("'NoneType' object is not iterable")` during the auxiliary stream iteration before `get_final_response()` returns.

## Desired fix

Add auxiliary-path regression coverage and make `_CodexCompletionsAdapter` treat `output is None` like empty output, salvaging from collected `response.output_item.done` items or text deltas without reissuing the request. Preserve unrelated TypeError propagation.

## Acceptance

- A focused auxiliary regression test reproduces `final.output is None` and passes after the fix.
- A focused auxiliary regression test reproduces SDK stream TypeError after text deltas and passes after the fix.
- Unrelated TypeError is not swallowed.
- Existing Codex null-output salvage tests still pass.
- Scoped commit contains only files for this request.
