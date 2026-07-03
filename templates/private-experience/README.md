# Private Hermes sanitized experience pack

This pack gives a first-time authorized private-Hermes user a strong default
Hermes brain *capability shape* without copying the owner's lived state.

It is safe-by-default:

- templates only;
- no live `config.yaml`;
- no `.env`;
- no OAuth/session tokens;
- no memory or user profile export;
- no `state.db`, logs, checkpoints, task evidence, or local-brain vault;
- no real project profiles or client facts;
- all cron, gateway delivery, taskpool mutation, publish/deploy/push, and
  destructive actions remain disabled until explicitly configured by the new
  user.

## Installed location

`install-private.sh` copies this directory to:

```text
$HERMES_HOME/templates/private-experience
```

It never overwrites existing files.

## Recommended setup order

1. Install private Hermes.
2. Run `hermes setup` for provider credentials.
3. Read `post-install-checklist.md`.
4. Copy `user-profile.example.json` and `hermes-agent-persona.example.md` into
   your own live profile notes only after replacing the example values.
5. Copy `project-profile.example.json` for each project and fill facts manually.
6. Keep `gateway.example.yaml.disabled` and cron examples disabled until exact
   targets and delivery permissions are known.
7. Run `hermes doctor` and the pack selfcheck.

## Experience layers

| Layer | Included here | User must provide |
|---|---|---|
| Source code | private Hermes checkout | none after GitHub authorization |
| Skills/procedures | bundled repo skills + this guide | personal overlays if desired |
| Config shape | `config.example.yaml` | real model keys, provider choices, platform tokens |
| Persona shape | `hermes-agent-persona.example.md` + user profile schema | personal tone/preferences |
| Project profiles | schema + example with placeholder URLs | project-specific facts and URLs |
| Evidence | ledger and receipt templates | real test output/screenshots/readbacks |
| Automation | disabled cron/gateway examples | exact targets and delivery permissions |
| Runtime memory | not included | created naturally by the user's sessions |

## Boundary

This pack is not a backup/restore mechanism for the owner's machine. It is a
reproducible, sanitized onboarding scaffold: copy the operating consensus, not
private payloads.
