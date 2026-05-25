# Browser replay evidence

Hermes browser tools now emit a bounded replay bundle for each session under:

`get_hermes_home()/browser_replay_evidence/<session>/` (for example `~/.hermes/browser_replay_evidence/<session>/` on the default profile or `~/.hermes/profiles/<profile>/browser_replay_evidence/<session>/` in profile-scoped runs)

Files:

- `bundle.json` — latest rolled-up evidence summary for the session
- `events.jsonl` — append-only event log, one browser action per line

Schema highlights (`browser_replay_bundle_v1`):

- `session_key`
- `started_at`, `updated_at`
- `current_url`, `title`
- `latest_dom_snapshot_excerpt`
- `latest_console_errors`
- `latest_screenshot_path`
- `latest_failure_class`
- `action_sequence`
- `last_event`

Each event (`browser_replay_event_v1`) records:

- `recorded_at`
- `command`
- `action_sequence` (single bounded command+args entry)
- `success`
- `url`, `title`
- `dom_snapshot_excerpt`
- `console_errors`
- `screenshot_path`
- `failure_class`
- `error`

Boundaries and safety:

- URLs, DOM excerpts, console messages, and errors are force-redacted with Hermes secret redaction before persistence.
- DOM excerpts are capped to the first 20 non-empty lines and then truncated.
- Console evidence is capped to a small fixed number of entries.
- Action history is capped to the latest 12 steps.
- Screenshot paths are recorded only as filesystem paths; image bytes are not embedded.

Failure classification is intentionally coarse and task-facing:

- `success`
- `success_with_js_errors`
- `policy_blocked`
- `bot_detection`
- `timeout`
- `screenshot_capture_failed`
- `empty_snapshot`
- `backend_capability_gap`
- `vision_analysis_failed`
- `command_failed`

Recommended usage for browser-first reports:

1. Run the browser workflow as usual (`browser_navigate`, `browser_snapshot`, `browser_console`, `browser_vision`, interactions as needed).
2. Read `replay_bundle_path` from the latest browser tool response.
3. Cite that bundle path in the report/proof note instead of re-copying raw page output.
4. If richer audit is needed, attach or inspect the sibling `events.jsonl` log.

This keeps read-only web jobs and UI failure investigations replayable without turning Hermes into a broad recording framework or persisting unbounded page content.
