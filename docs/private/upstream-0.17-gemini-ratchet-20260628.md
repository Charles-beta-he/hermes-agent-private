# Upstream 0.17 Gemini Ratchet Upgrade SSOT

Date: 2026-06-28
Branch: ratchet/upstream-0.17-gemini-20260628-183845
Worktree: /Users/charles/.hermes/runs/hermes-upstream-0.17-gemini-ratchet
Base private HEAD: c2d82ab8a
Upstream target: upstream/main 135f23516
Backup of dirty original worktree: /Users/charles/.hermes/backups/hermes-upstream-ratchet-20260628-183834

## Objective

Fuse upstream `NousResearch/hermes-agent` 0.17+ into the private Hermes branch using a ratchet upgrade, while preserving local private AgentOS / taskpool / workflow-kernel overlays and accepting that the old `google-gemini-cli` OAuth path appears broken locally.

## Success Criteria

1. Upstream code is merged into an isolated ratchet branch/worktree, not directly into the dirty `private/main` checkout.
2. Existing private control-plane capabilities are preserved or explicitly quarantined with evidence.
3. Gemini is migrated toward upstream's current native `gemini` Google AI Studio API-key provider path rather than forcing the broken `google-gemini-cli` OAuth adapter to survive.
4. Post-update guard runs and every `FIX_FAILED`/blocker is called out.
5. Focused verification covers at least: import/compile, AgentOS status/route, Gemini provider config behavior, provider/fallback, gateway/cron/kanban smoke where possible.
6. Completion is not claimed until commands/readbacks prove it. If credentials are needed for the new Gemini API-key path, stop at a human credential boundary rather than typing secrets.

## Constraints / Red Lines

- Do not push to upstream or origin unless separately authorized.
- Do not type or request API keys into commands; only detect whether `GOOGLE_API_KEY`/`GEMINI_API_KEY` exists.
- Do not mutate the original dirty worktree except through explicit backup/read-only checks.
- Do not delete private AgentOS/taskpool/workflow-kernel files to satisfy upstream conflicts.
- Prefer upstream architecture for broad upstream refactors, then replay private overlays into the new seam.
- Treat upstream as upgrade input, not canonical remote.

## Local Brain Requirements Read

- `/Users/charles/Documents/brain/99-hermes-notes/hermes-private-evolution-manifesto.md`: upstream pull is controlled upgrade, not sync; local patches/data survive via post-update healing; verification requires tests + post-update-check + doctor; prefer Level 0/1 artifacts over vendor-core edits.
- `/Users/charles/Documents/brain/99-hermes-notes/private-branch-upgrade-strategy.md`: backup/stash before upgrade; restore private changes intentionally; verify with pre-upgrade/post-update checks; local commit is traceability and rollback.

## Gemini Evidence Before Merge

- `hermes doctor` reports Google Gemini OAuth logged in as `h1057674027@gmail.com`.
- `hermes fallback list` reports fallback `gemini-3.1-pro-preview via google-gemini-cli`.
- Direct current-path smoke failed: `hermes chat --provider google-gemini-cli -m gemini-3.1-pro-preview` timed out after retries.
- Native upstream path smoke failed because no valid API key is available: `provider gemini` returned `Gemini HTTP 400 (INVALID_ARGUMENT): API key not valid`.
- Upstream `website/docs/guides/google-gemini.md` documents Google AI Studio API-key path (`GOOGLE_API_KEY` or `GEMINI_API_KEY`) and upstream removed `agent/google_oauth.py`, `agent/google_code_assist.py`, and `agent/gemini_cloudcode_adapter.py`.

## Selected Path

Use an isolated full upstream merge worktree. Accept upstream native Gemini provider as the target direction; do not preserve broken `google-gemini-cli` unless needed as a private optional overlay after core merge passes. Stop at credential boundary for real Gemini API-key smoke.

## Verification Log

Append command outputs or summaries below as the ratchet proceeds.
