# Private Hermes Multi-Brain Architecture

Status: active
Updated: 2026-05-18

## Purpose

This private Hermes line uses a single runtime with multiple brain overlays:

- Runtime: `/Users/charles/.hermes/hermes-agent`
- Brain overlay root: `/Users/charles/.hermes/brains`
- Registry: `/Users/charles/.hermes/brains/registry.yaml`
- Sanitized backup repo: `/Users/charles/Code/personal-hermes-brain`

The goal is to keep the Hermes runtime upgradeable while assigning long-lived cognition, routing, and governance assets to explicit brain overlays.

## Brain roles

- `base`: common constitution, memory policy, evidence packet format, tool policy.
- `main`: router, dispatcher, acceptance arbiter, self-evolution steward.
- `dev`: software/project implementation and verification.
- `hermes-core`: Hermes runtime/private fork governance, overlay registry, taskpool/multimodel/conductor evolution, and sanitized personal brain backup governance.

## Routing rule

Requests involving these surfaces route to `hermes-core`:

- Hermes private repo, origin/upstream split, upstream sync, upgrade SOP.
- `post-update-check.py`, overlay registry, patch replay, private runtime self-checks.
- `personal-hermes-brain` as sanitized backup/brain overlay asset.
- taskpool event hooks, expensive multimodel routing, conductor, budget governance.
- `brainctl` and multi-brain registry commands.

General product/project code routes to `dev`; cross-brain acceptance and arbitration route to `main`.

## Control commands

```bash
python3 ~/.hermes/scripts/brainctl.py --json brains status
python3 ~/.hermes/scripts/brainctl.py --json brains validate
python3 ~/.hermes/scripts/brainctl.py --json brains route 'Hermes private repo overlay governance'
```

`post-update-check.py` includes `multi_brain_registry`, which shells through `brainctl brains validate` and verifies that Hermes private repo governance routes to `hermes-core`.

## Boundaries

Never commit runtime/sensitive state into either runtime source or sanitized brain backup repos:

- `state.db`, sqlite, wal, shm
- `.hermes/` runtime directories inside source checkouts
- raw memory, sessions, logs, caches
- auth, OAuth, tokens, env files

## Upgrade invariant

Kernel/runtime upgrades follow `docs/private/upgrade-sop.md`.
Brain overlays are upgraded only through registry-aware validation and sanitized backup commits/backup branches.

## Minimum validation

```bash
python3 -m py_compile ~/.hermes/scripts/brainctl.py ~/.hermes/scripts/post-update-check.py
python3 ~/.hermes/scripts/brainctl.py --json brains validate
python3 ~/.hermes/scripts/post-update-check.py
hermes doctor
```
