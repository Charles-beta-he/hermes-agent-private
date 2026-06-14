# Charles private Hermes sanitized experience pack

This pack helps an authorized private-Hermes user reproduce the *capability shape* of Charles' workstation without copying Charles' private data.

It is safe-by-default:

- templates only;
- no live `config.yaml`;
- no `.env`;
- no OAuth/session tokens;
- no memory or user profile export;
- no `state.db`, logs, checkpoints, task evidence, or local-brain vault;
- no real project profiles or client facts.

## Installed location

`install-private.sh` copies this directory to:

```text
$HERMES_HOME/templates/private-experience
```

It never overwrites existing files.

## Recommended setup order

1. Install private Hermes.
2. Run `hermes setup` for provider credentials.
3. Copy only the templates you want into live config locations.
4. Fill placeholders manually.
5. Run `hermes doctor` and the checklist in `post-install-checklist.md`.

## Experience layers

| Layer | Included here | User must provide |
|---|---|---|
| Source code | private Hermes checkout | none after GitHub authorization |
| Skills/procedures | bundled repo skills + this guide | personal overlays if desired |
| Config shape | `config.example.yaml` | real model keys, provider choices, platform tokens |
| Persona shape | `hermes-agent-persona.example.md` | personal tone/preferences |
| Project profiles | `project-profile.example.json` | project-specific facts and URLs |
| Automation | `cron.examples.md` | exact targets and delivery permissions |
| Runtime memory | not included | created naturally by the user's sessions |

## Boundary

This pack is not a backup/restore mechanism for Charles' machine. It is a reproducible, sanitized onboarding scaffold.
