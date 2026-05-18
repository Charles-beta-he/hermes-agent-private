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

## Upgrade flow

1. Confirm a clean private line.
   ```bash
   git status --short --branch
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
6. Run the post-update guard. This includes `private_repo_governance`, which checks the origin/upstream split, `docs/private/` governance docs, and source-checkout runtime-state leaks.
   ```bash
   python3 ~/.hermes/scripts/post-update-check.py --fix
   ```
7. Verify with at least:
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

