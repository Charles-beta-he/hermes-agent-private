# Private Hermes Upstream Upgrade SOP

This repo is a private overlay line for a locally customized Hermes checkout.

## Remotes

- `upstream`: official Hermes, read-only source of upstream changes.
- `origin`: private writable repo for local overlays and governance.

## Branches

- `upstream/main`: official baseline.
- `private/main`: verified private stable line.
- `integrate/YYYYMMDD-upstream-sync`: temporary upstream integration branch.
- `backup/*`: rollback points before governance or upgrades.

## Ratchet invariant

Private Hermes upgrades are ratchet-style upgrades, not automatic pulls:

1. Detect official upstream movement.
2. Produce an impact map against the private overlay registry.
3. Create/queue an integration branch or task only after safety checks.
4. Replay, retire, or redesign overlays with evidence.
5. Promote `private/main` only after verification evidence is recorded.

The watcher may fetch and record state, but it must never auto-merge, auto-rebase, push, or promote `private/main`.

## Upstream watcher

The local watcher is:

```bash
python3 ~/.hermes/scripts/upstream-ratchet-watch.py --quiet-no-change
```

It records state in `~/.hermes/state/upstream-ratchet.json` and is intentionally cron/no-agent friendly:

- silent when `upstream/main` has not changed;
- emits actionable JSON when a new upstream commit is detected;
- records merge-base, ahead/behind counts, proposed `integrate/upstream-<sha>` branch, dirty-tree blockers, and overlay impact map;
- fetches only, and never checks out, merges, rebases, pushes, or promotes.

If the watcher reports `dirty_worktree_blocks_integration_branch_creation`, finish/commit/stash the current scoped work before starting the upstream integration branch.

## Upgrade flow

1. Confirm a clean private line.
   ```bash
   git status --short --branch
   python3 ~/.hermes/scripts/upstream-ratchet-watch.py --pretty
   ```
2. Create a rollback point.
   ```bash
   git branch backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S) private/main
   git tag -a backup-pre-upstream-sync-$(date +%Y%m%d-%H%M%S) private/main -m "Pre upstream sync backup"
   ```
3. Fetch upstream.
   ```bash
   git fetch upstream --prune
   ```
4. Create an integration branch from upstream.
   ```bash
   git switch -c integrate/$(date +%Y%m%d)-upstream-sync upstream/main
   ```
5. Replay private overlay commits or use the overlay registry to re-apply only active overlays.
6. Run the post-update guard. This includes:
   - `multi_brain_registry`: validates `~/.hermes/brains/registry.yaml`, subbrain YAML, runtime/brain repo boundaries, and Hermes private-repo routing to `hermes-core`.
   - `private_repo_governance`: checks the origin/upstream split, `docs/private/` governance docs, and source-checkout runtime-state leaks.
   ```bash
   python3 ~/.hermes/scripts/post-update-check.py --fix
   ```
7. Validate the multi-brain overlay registry explicitly.
   ```bash
   python3 ~/.hermes/scripts/brainctl.py --json brains status
   python3 ~/.hermes/scripts/brainctl.py --json brains validate
   python3 ~/.hermes/scripts/brainctl.py --json brains route 'Hermes private repo overlay governance'
   ```
8. Verify with at least:
   ```bash
   git diff --check
   hermes --version
   hermes doctor
   hermes chat -q '只回复: HERMES_SMOKE_OK' --quiet --max-turns 1 --ignore-rules
   ```
8. Run targeted pytest slices for touched overlays.
9. If verification passes, advance `private/main` to the integration branch and push.
   ```bash
   git branch backup/private-main-before-promote-$(date +%Y%m%d-%H%M%S) private/main
   git branch -f private/main integrate/YYYYMMDD-upstream-sync
   git switch private/main
   git push -u origin private/main
   git push origin integrate/YYYYMMDD-upstream-sync
   ```

## Failure handling

- If cherry-pick conflicts are unclear, stop on the integration branch and inspect the overlay registry before resolving.
- If `post-update-check.py --fix` reports `FIX_FAILED`, do not promote.
- If tests fail, do not promote; either patch on the integration branch or roll back to the last `backup/*` point.
- Runtime/config corruption is recovered from Hermes home backups, not from source Git.

## Runtime-state boundary

Never commit:

- `state.db`, `*.sqlite*`, `*.wal`, `*.shm`
- `.hermes/` inside the source checkout
- logs, sessions, caches, raw memory, credentials, OAuth files, `.env`

