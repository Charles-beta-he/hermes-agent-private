# Private self-installation inventory and abstraction layers

Purpose: define what a Charles private Hermes installation needs, which pieces are
repositories versus local state, and which abstraction layer each piece belongs
to. This prevents the private installer from accidentally bundling personal
memory, local-brain notes, secrets, or volatile runtime logs into the code repo.

Observed local anchors on this machine:

- Hermes source checkout: `/Users/charles/.hermes/hermes-agent`
- Hermes home / runtime root: `/Users/charles/.hermes`
- Local brain vault: `/Users/charles/Documents/brain`
- Project profiles: `/Users/charles/.hermes/project-profiles`
- Personal skills overlay: `/Users/charles/.hermes/skills`
- Local scripts: `/Users/charles/.hermes/scripts`
- Session DB: `/Users/charles/.hermes/state.db`
- Route outcomes DB: `/Users/charles/.hermes/route_outcomes.db`

Approximate local sizes at inventory time:

- `project-profiles`: 1.0 MB
- `docs`: 4.8 MB
- `scripts`: 14 MB
- `Documents/brain`: 16 MB
- `route_outcomes.db`: 38 MB
- `state.db`: 112 MB
- `skills`: 363 MB
- `hermes-agent` checkout: 1.7 GB

## Repository split

### 1. Code distribution repo — required

Repo:

- `Charles-beta-he/hermes-agent-private`

Scope:

- Hermes source code;
- private install channel scripts;
- private README / install docs;
- tests for private channel behavior;
- safe templates and governance docs.

Install role:

- This is the only required repo for another authorized user to install the
  private Hermes build.
- Users clone `private/main`, run `scripts/install-private.sh`, then update with
  `hermes update`.

Do not store here:

- `~/.hermes/config.yaml` with real credentials or endpoint secrets;
- `~/.hermes/.env`;
- `state.db`, logs, checkpoints, route outcome DBs;
- raw personal memory or local-brain vault content;
- per-user taskpool/runtime state.

### 2. Personal overlay / bootstrap repo — optional, Charles-only

Recommended future repo name:

- `Charles-beta-he/hermes-personal-overlay-private`

Scope:

- sanitized config templates;
- non-secret profile templates;
- bootstrap scripts that copy/link local overlays;
- machine-specific install notes;
- optional script manifests.

Install role:

- Not required for ordinary collaborators.
- Useful for reproducing Charles' own workstation/cloud-node setup.

Boundary:

- This repo may contain templates and manifests, not live secrets or DB dumps.
- It may reference local paths, but should not assume they exist for other users.

### 3. Local brain vault repo — optional, private knowledge corpus

Recommended future repo name:

- `Charles-beta-he/hermes-local-brain-private`

Scope:

- curated and sanitized local-brain notes;
- project Workpads that are safe to sync;
- governance/review notes intended as knowledge artifacts;
- compiled knowledge pages and indexes.

Install role:

- Not required for Hermes to run.
- Optional context corpus for Charles' own PA/local-brain experience.

Boundary:

- Treat the vault as content/data, not executable code.
- Do not make a fresh user's install depend on this repo.
- Sensitive client/project notes need project-level access controls or redaction
  before sync.

### 4. Secrets store — never a git repo

Examples:

- `~/.hermes/.env`
- API keys and provider credentials;
- messaging platform tokens;
- OAuth refresh tokens;
- private SSH keys;
- webhook secrets.

Install role:

- Installed interactively via `hermes setup`, platform login, or OS keychain / 1Password / Bitwarden / Doppler / SOPS-style secret management.

Boundary:

- Never commit secrets into any repo.
- Installation docs may name variables, but values must be entered by the owner.

### 5. Runtime state / evidence stores — backup, not install source

Examples:

- `~/.hermes/state.db`
- `~/.hermes/route_outcomes.db`
- `~/.hermes/logs/`
- `~/.hermes/checkpoints/`
- `~/.hermes/state/`
- taskpool/kanban runtime DBs and JSONL evidence;
- cron run logs.

Install role:

- Not required for another user.
- May be backed up/restored for Charles only when migrating machines.

Boundary:

- These are volatile runtime records and can contain private conversations,
  tool outputs, and evidence snippets.
- Do not place them in `hermes-agent-private`.
- Prefer explicit backup/import flows over source-control sync.

## Abstraction layers

### L0 — Source code and installer

Landing zone:

- `hermes-agent-private`

Examples:

- `scripts/bootstrap-private.sh`
- `scripts/install-private.sh`
- `hermes_cli/main.py` update-channel behavior
- installer tests

Durability:

- Versioned, reviewed, committed.

### L1 — Install channel metadata

Landing zone:

- `$HERMES_HOME/install.json`
- `$HERMES_HOME/.install_method`

Examples:

```json
{
  "channel": "charles-private",
  "origin": "https://github.com/Charles-beta-he/hermes-agent-private.git",
  "upstream": "https://github.com/NousResearch/hermes-agent.git",
  "branch": "private/main"
}
```

Durability:

- Local machine state.
- Re-created by `scripts/install-private.sh`.

### L2 — Configuration shape

Landing zone:

- config templates in optional personal overlay repo;
- live config at `$HERMES_HOME/config.yaml`.

Examples:

- provider routing shape;
- gateway enabled platforms;
- cron policy defaults;
- toolset defaults.

Durability:

- Template can be versioned.
- Live config is user/machine-specific and should not be copied blindly.

### L3 — Skill / procedure layer

Landing zone:

- repo-bundled skills when broadly safe;
- `$HERMES_HOME/skills` for personal/user overlays;
- skill references for reusable procedures.

Examples:

- Hermes update workflows;
- taskpool control-plane runbooks;
- local-brain note workflows;
- project-specific skills only when explicitly scoped.

Durability:

- Reusable procedures should become skills.
- Project facts should not be promoted into global skills.

### L4 — Project profile / local fact layer

Landing zone:

- `$HERMES_HOME/project-profiles/*.json`
- project Workpads in local brain

Examples:

- UVISA repo paths and verification conventions;
- Lark task source-of-truth notes;
- project-specific branch/base/release facts.

Durability:

- Stable enough for a project, not global.
- Should be sanitized before sharing with another user.

### L5 — Personal memory layer

Landing zone:

- Hermes memory store / injected `USER` and `MEMORY` summaries;
- `state.db` as the session/memory substrate;
- optional compact export/import, not raw repo sync.

Examples:

- stable user preferences;
- durable red lines;
- durable environment facts.

Durability:

- Personal and high-sensitivity.
- Do not include in install repo.
- Do not copy to other users.

### L6 — Local brain knowledge layer

Landing zone:

- `/Users/charles/Documents/brain`
- optional sanitized local-brain repo
- compiled indexes / dashboards

Examples:

- evidence-backed knowledge pages;
- task notes;
- Workpads;
- dashboards and review notes.

Durability:

- Charles-specific context corpus.
- Optional for self-reproduction; not required for base installation.

### L7 — Control-plane/runtime ledger layer

Landing zone:

- taskpool/kanban DBs;
- cron jobs and scheduler state;
- route outcome DB;
- logs/checkpoints/evidence stores.

Examples:

- task lifecycle evidence;
- model routing outcomes;
- cron job run state;
- gateway status.

Durability:

- Operational state, not installation source.
- Backup/restore only when intentionally migrating the same identity.

## Install bundles

### Bundle A — collaborator install

Required:

- `hermes-agent-private`
- GitHub repo access
- user-run `hermes setup`

Excluded:

- Charles memory;
- Charles local brain;
- Charles `.env` / credentials;
- taskpool runtime state.

### Bundle B — Charles new-machine install

Required:

- `hermes-agent-private`
- secret manager or manual secret restore
- optional personal overlay repo
- optional local brain vault repo or Obsidian vault sync
- explicit backup/restore of selected runtime state if desired

Excluded by default:

- stale logs;
- stale checkpoints;
- old taskpool runs unless needed for audit continuity.

### Bundle C — public/upstream install

Required:

- official NousResearch installer or public repo

Excluded:

- all private channel files and metadata.

This is intentionally a different distribution path.

## Operational rules

1. The code installer must make Hermes runnable without any personal brain or memory.
2. Personal context is additive: it can improve Charles' instance, but it must not
   be a hard dependency for other users.
3. Secrets are never copied through git. Templates may name fields; owners supply
   values locally.
4. Runtime state is backed up/restored, not committed.
5. Before promoting a local-brain/project fact into a skill, strip project names,
   paths, branch names, task IDs, and one-off artifacts unless the landing zone is
   explicitly project-scoped.
6. `hermes update` follows install metadata; ordinary users should never need to
   know upstream/main.
7. Upstream sync is a maintainer action: integrate upstream into private/main,
   verify, then publish to origin/private/main.

## Current unresolved local state

At the time this inventory was written, the source checkout still had unrelated
local changes outside this documentation task. They are intentionally not part of
private installation packaging and should be reviewed separately before any broad
push or release.
