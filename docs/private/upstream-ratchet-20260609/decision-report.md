# Upstream ratchet upgrade report — 2026-06-09

## Scope

- Repository: `/Users/charles/.hermes/hermes-agent`
- Base branch: `private/main`
- Upstream merged: `upstream/main` (`2f510ca8e07b570ad3fdc491400432a96494aaf3`)
- Safety backup branch: `backup/private-main-pre-20260609-upstream-ratchet`
- Safety backup tag: `backup-private-main-pre-20260609-upstream-ratchet`

## Carry / absorb / retire decisions

| Area | Decision | Evidence / rationale | Verification |
| --- | --- | --- | --- |
| Auto-relay continuation overlay | Carry + redesign into upstream `turn_finalizer` seam | Upstream extracted post-loop finalization to `agent/turn_finalizer.py`; private auto-relay helpers were replayed in `agent/conversation_loop.py` and invoked from `finalize_turn(...)`. | `pytest -q -o addopts='' tests/run_agent/test_auto_relay_continuation.py`; included in 291-test targeted suite. |
| Kanban timeout budget failure recording | Carry + adapt | Budget failure recording now runs after auto-relay gate, preserving bounded continuation before marking kanban timeout failure. | `py_compile` and post-update check; auto-relay targeted tests passed. |
| Memory provider rollback on schema failure | Carry + combine with upstream guard | `agent/memory_manager.py` preserves private rollback when `get_tool_schemas()` fails and upstream reserved-core-tool shadow prevention. | `post-update-check.py --fix`: `memory_manager_provider_rollback` OK; targeted memory tests passed. |
| Conductor runtime router helper | Carry | `_run_conductor_intent_router` restored as default-off/fail-soft importable seam after upstream refactor. | `tests/agent/test_conductor_runtime.py`: 9 passed; post-update check `conductor_runtime_productization_regression` OK. |
| CLI monolith private drift in `hermes_cli/main.py` | Absorb upstream | Upstream modularized CLI subcommands under `hermes_cli/subcommands/*`; private/main accepted upstream structure rather than keeping old monolith. | `./venv/bin/python -m hermes_cli.main version` smoke passed; `tests/hermes_cli/test_subcommands_batch.py` and `tests/hermes_cli/test_cmd_update.py` passed. |
| Home Assistant gateway path | Absorb upstream move | Upstream moved adapter from `gateway/platforms/homeassistant.py` to plugin path `plugins/platforms/homeassistant/adapter.py`; deletion/rename accepted. | Merge status clean; existing gateway/homeassistant tests included by upstream inventory, not part of final targeted slice. |
| Desktop/UI/docs upstream bulk changes | Absorb upstream | Large upstream desktop, docs, i18n, and website updates accepted with no private conflict replay unless overlay registry required it. | Merge resolved with `git diff --check` clean. |
| post-update checker drift fixes | Carry governance | `post-update-check.py --fix` run with Hermes venv Python; no fix failures. | Final post-update result: `16 OK | 0 fixed | 2 need attention`. |

## Verification evidence

Commands run successfully after conflict resolution:

```bash
./venv/bin/python -m py_compile agent/conversation_loop.py agent/turn_finalizer.py agent/memory_manager.py hermes_cli/main.py run_agent.py tools/delegate_tool.py tools/memory_tool.py tools/process_registry.py

./venv/bin/python -m pytest -q -o addopts='' \
  tests/run_agent/test_auto_relay_continuation.py \
  tests/agent/test_conductor_runtime.py \
  tests/tools/test_delegate.py::TestDelegateTask::test_child_inherits_auto_relay_settings \
  tests/tools/test_delegate.py::TestDelegateTask::test_child_inherits_string_false_auto_relay_setting \
  tests/agent/test_memory_provider.py \
  tests/tools/test_memory_tool.py \
  tests/hermes_cli/test_subcommands_batch.py \
  tests/hermes_cli/test_cmd_update.py \
  tests/tools/test_process_registry.py
# 291 passed, 1 skipped, 1 warning

./venv/bin/python ~/.hermes/scripts/post-update-check.py --fix
# 16 OK | 0 fixed | 2 need attention

./venv/bin/python -m hermes_cli.main version
# Hermes Agent v0.16.0 (2026.6.5)
```

## Remaining non-blocking attention items

- `skill_tracking`: `INSTALLED_NO_DATA_YET` — telemetry/data availability, not a merge blocker.
- `skill_quality`: `MISSING (118/241 no tests...)` — pre-existing skill coverage governance backlog, not a runtime merge blocker.

## Notes

The earlier multi-model consult job did not produce a usable final artifact within the turn budget, so this report records the evidence-grounded local certification result and the concrete verification outputs that were available during the upgrade.
