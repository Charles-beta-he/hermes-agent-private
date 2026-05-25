# CodeGraph local evaluation for Hermes context-location / tool-call reduction

Source candidate: https://github.com/colbymchenry/codegraph
Checked: 2026-05-21
Local clone: `/tmp/codegraph`
Commit checked: `4329a52becbef247a5641f6342d525dffd17192c`
Package: `@colbymchenry/codegraph` v0.8.0
Hermes checkout indexed: `/Users/charles/.hermes/hermes-agent`
Raw evaluation output: `/tmp/codegraph-eval/codegraph-output.txt`

## Environment notes

- Default system Node was `v25.8.1`; CodeGraph hard-refused Node 25 because of its documented V8/WASM risk.
- Evaluation used existing Node `~/.nvm/versions/node/v24.14.0/bin/node`, which is within CodeGraph's declared `<25` engine range.
- `better-sqlite3` native binding was unavailable for the evaluation Node; CodeGraph fell back to WASM SQLite.
- Indexing completed, but with the slower backend warning.

## Index evidence

`codegraph init -i /Users/charles/.hermes/hermes-agent` completed:

- Files: 2,188
- Nodes: 65,013
- Edges: 151,236
- DB size: 135.88 MB
- Backend: `wasm` fallback
- Duration: about 2m33s
- Languages: Python 1,776; TypeScript 317; TSX 88; JavaScript 6; Ruby 1

## Fixed evaluation set

### Case 1 — Core agent class / conversation loop

Question shape: locate `AIAgent` and run-conversation surfaces.

Baseline live search:

- `search_files('class AIAgent|def run_conversation|def chat(', path=hermes-agent, file_glob='*.py')`
- Found the relevant surfaces but mixed direct and test stubs.

CodeGraph result:

- `codegraph query AIAgent --path hermes-agent --limit 8`
- Returned `class AIAgent` at `run_agent.py:326` as the top result.
- Also returned methods on `AIAgent` such as `close`, `base_url`, `clear_interrupt`, `steer`, `release_clients`, `is_interrupted`, `_thread_identity`.

Verdict: useful as a symbol locator. It reduced ambiguity for class-level entry points, but still needs `read_file` for truth-gated implementation context.

### Case 2 — Browser replay evidence / browser tool surfaces

Question shape: locate browser replay evidence and browser tool implementation surfaces.

Baseline live search:

- `search_files('replay.*evidence|browser_replay|BrowserTool|browser_tool', path=hermes-agent, file_glob='*.py')`
- Found `model_tools.py`, `cli.py`, `tui_gateway/server.py`, and browser-tool-related surfaces.

CodeGraph result:

- `codegraph query Browser --path hermes-agent --limit 12`
- `codegraph context 'browser replay evidence tool browser_tool' --path hermes-agent`
- Returned related symbols, but context quality depended heavily on query wording and did not fully replace targeted regex search.

Verdict: potentially useful for symbol graph exploration after a baseline grep has found candidate files; not strong enough as the first truth gate for cross-cutting text concepts.

### Case 3 — Taskpool pre-add triage metadata

Question shape: locate `priority_layer`, `dependency_order`, and `multimodel_triage_result` handling for taskpool pre-add triage.

Baseline live search:

- `search_files('priority_layer|dependency_order|multimodel_triage_result', path='~/.hermes/scripts/brain_task_orchestrator.py')`
- Found exact lines in `~/.hermes/scripts/brain_task_orchestrator.py`, including frontmatter ordering and row serialization.

CodeGraph result:

- `codegraph context 'taskpool priority_layer dependency_order multimodel_triage_result brain_task_orchestrator' --path hermes-agent`
- Returned mostly irrelevant repo-local symbols because `brain_task_orchestrator.py` is outside the indexed Hermes checkout.

Verdict: a project-root index is insufficient for Hermes' real control-plane topology because important runtime scripts live under `~/.hermes/scripts`, outside the repo. Any Hermes integration would need an explicit multi-root index contract or it will miss core truth surfaces.

## Stale-index / drift findings

- The generated index is a derived artifact in `.codegraph/`; this directory is not ignored in the current Hermes repo.
- The index must not be committed and must not be used as final truth after source changes.
- For Hermes, any integration must record at least: indexed roots, source snapshot/mtime/hash, backend type, index age, and fallback/degraded status.
- Query answers must remain advisory; final implementation evidence still comes from `read_file`, tests, build, or runtime checks.

## Recommendation

Do not integrate CodeGraph into Hermes mainline yet.

Recommended next shape if we continue:

1. Add `.codegraph/` to local ignore or use an external cache path before further experimentation.
2. Prototype a default-off, multi-root index covering both:
   - `/Users/charles/.hermes/hermes-agent`
   - `/Users/charles/.hermes/scripts`
3. Add an explicit staleness gate: source root + git commit / file hash + index mtime must be shown with every query result.
4. Use CodeGraph only as an advisory locator before `read_file`/`search_files`, not as a truth gate.
5. Fix native SQLite backend if performance matters; WASM fallback is acceptable for a one-off spike but not ideal for routine use.

Final decision for the current task: evidence supports closing this as an evaluation, with a negative/guarded integration conclusion: CodeGraph is promising for symbol entry-point discovery, but not ready for Hermes control-plane integration until multi-root indexing, staleness metadata, ignore/cache policy, and fallback labeling are designed.
