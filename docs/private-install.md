# Charles private Hermes installation

This document is the operator/user contract for the Charles-maintained private
Hermes distribution. For the broader repository/state split of Charles' own
installation, see `docs/private-self-installation-layers.md`.

## Default user flow

Charles first grants the user's GitHub account or SSH deploy key access to:

  https://github.com/Charles-beta-he/hermes-agent-private

There are three supported install paths. All avoid putting tokens in clone URLs.

### A. GitHub CLI path

Works on desktops and on headless servers that can complete GitHub device-code
login from another browser:

```bash
gh auth login
gh auth setup-git
gh api -H "Accept: application/vnd.github.raw" \
  repos/Charles-beta-he/hermes-agent-private/contents/scripts/bootstrap-private.sh?ref=private/main \
  | bash
```

The bootstrap prints the equivalent SSH path, verifies access, clones or
refreshes `~/.hermes/hermes-agent`, and delegates to `scripts/install-private.sh`.

### B. Headless SSH path, no `gh`, no browser

Use this on servers, containers, NAS boxes, CI hosts, or any machine where a
GitHub SSH key or deploy key has already been authorized for the private repo:

```bash
mkdir -p ~/.hermes
git clone --branch private/main --single-branch \
  git@github.com:Charles-beta-he/hermes-agent-private.git \
  ~/.hermes/hermes-agent
~/.hermes/hermes-agent/scripts/install-private.sh --yes
```

Optional no-interactive setup/install-only variant:

```bash
~/.hermes/hermes-agent/scripts/install-private.sh --yes --skip-setup
```

### C. Existing checkout or offline transfer

If the source checkout was copied by rsync/tar/scp from another authorized
machine, finalize the private channel locally:

```bash
cd ~/.hermes/hermes-agent
scripts/install-private.sh --yes
```

`install-private.sh` does not need `gh`; it only needs a valid checkout.

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
- copies sanitized experience templates into `$HERMES_HOME/templates/private-experience`
  unless `--skip-experience-pack` is passed;
- delegates dependency/setup work to `./setup-hermes.sh` unless `--skip-setup` is passed.

The stamped metadata makes plain `hermes update` follow `origin/private/main`
instead of falling back to the public `main` branch.

## Sanitized experience pack

The private installer now includes a safe onboarding scaffold at:

```text
templates/private-experience/
```

It is copied to:

```text
$HERMES_HOME/templates/private-experience/
```

The goal is to reproduce the capability shape of Charles' machine without
copying Charles' private memory or runtime state. The pack contains:

- `config.example.yaml` — non-secret provider/tool/dashboard/safety shape;
- `hermes-agent-persona.example.md` — communication and operating-style template;
- `user-profile.schema.json` / `user-profile.example.json` — replaceable user-profile shape, not an export;
- `project-profile.schema.json` / `project-profile.example.json` — project-profile shape with placeholder URLs;
- `ssot.template.md`, `evidence-ledger.template.json`, and `receipt.template.json` — project truth/evidence/readback scaffolds;
- `authority-matrix.template.yaml` and `side-effect-gates.template.yaml` — default-off side-effect boundaries;
- `memory-policy.md`, `evidence-first-completion.md`, and `capability-graph-primer.md` — transferable operating consensus;
- `cron.examples.md`, `gateway.example.yaml.disabled`, and `taskpool-task.template.yaml` — automation templates that remain inert until explicitly enabled;
- `post-install-checklist.md` — capability-verification checklist;
- `manifest.json` — machine-readable list of copied items, explicitly not-copied items, and activation gates.

The pack intentionally does **not** include:

- live `config.yaml` or `.env`;
- API keys, OAuth sessions, platform tokens, or browser credentials;
- personal memory, user profile, sessions, or `state.db`;
- real project profiles, client facts, local-brain vaults, notes, or task evidence;
- logs, checkpoints, runtime DBs, or cron/gateway state.

Run this repository check before releasing changes to the pack:

```bash
./venv/bin/python scripts/private_experience_selfcheck.py --write-manifest --json
```

Use `--skip-experience-pack` when a user wants only the private code channel and
no templates copied into their Hermes home.

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
