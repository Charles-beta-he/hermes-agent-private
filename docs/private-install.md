# Charles private Hermes installation

This document is the operator/user contract for the Charles-maintained private
Hermes distribution. For the broader repository/state split of Charles' own
installation, see `docs/private-self-installation-layers.md`.

## Default user flow

Charles first grants the user's GitHub account access to:

  https://github.com/Charles-beta-he/hermes-agent-private

Then the recommended flow is GitHub CLI plus the automated bootstrap:

```bash
gh auth login
gh auth setup-git
gh api -H "Accept: application/vnd.github.raw" \
  repos/Charles-beta-he/hermes-agent-private/contents/scripts/bootstrap-private.sh?ref=private/main \
  | bash
```

The bootstrap prints this recommendation, verifies access, clones or refreshes
`~/.hermes/hermes-agent`, and delegates to `scripts/install-private.sh`.

Manual SSH fallback remains available:

```bash
mkdir -p ~/.hermes
git clone --branch private/main --single-branch git@github.com:Charles-beta-he/hermes-agent-private.git ~/.hermes/hermes-agent
~/.hermes/hermes-agent/scripts/install-private.sh
```

Do not ask users to put a GitHub token in a URL. Do not use private raw
`curl | bash` as the primary path; ordinary curl does not inherit GitHub browser
login state and token-bearing commands leak easily into shell history/logs.

## What install-private.sh guarantees

The finalizer script:

- runs from an already-authorized private checkout;
- ensures the checkout is on `private/main`;
- sets `origin` to `Charles-beta-he/hermes-agent-private`;
- sets `upstream` to `NousResearch/hermes-agent` for fetch-only maintenance use;
- stamps `$HERMES_HOME/install.json` with channel metadata;
- stamps `$HERMES_HOME/.install_method` as `git`;
- delegates dependency/setup work to `./setup-hermes.sh` unless `--skip-setup` is passed.

The stamped metadata makes plain `hermes update` follow `origin/private/main`
instead of falling back to the public `main` branch.

## User data boundary

Installation and update code must not overwrite user data under `$HERMES_HOME`:

- `config.yaml`
- `.env`
- `skills/`
- `state.db` / sessions
- `logs/`
- `profiles/`

The source checkout lives at `$HERMES_HOME/hermes-agent`; installation may update
that code checkout and its venv/build outputs, but user data must remain
create-if-missing / never-overwrite.

## Long-term one-line bootstrap

The long-term UX can add a public bootstrap URL such as:

```bash
curl -fsSL https://<stable-domain>/hermes-private/install.sh | bash
```

That bootstrap must remain small and public-safe: it may check `gh`, validate
private repo access, clone `Charles-beta-he/hermes-agent-private`, and then run
`scripts/install-private.sh`. It must not contain private source code or tokens.
The GitHub private repo remains the authorization boundary.

## Maintenance model

- `origin/private/main` is the only user update channel.
- `upstream/main` is maintainer input only; ordinary users should not update from
  upstream directly.
- Maintainers absorb upstream via integration branches, validate, then push to
  `origin/private/main`.
- Private releases should use private tag names such as
  `private-vYYYY.MM.DD.N`, not official upstream `v*` tags.
