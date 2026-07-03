# Private Hermes post-install checklist

Run after the installer completes.

## Baseline

```bash
hermes --version
hermes doctor
hermes status
```

## Configure providers

```bash
hermes setup
hermes fallback list
```

Recommended shape:

- one primary model provider;
- at least one fallback provider;
- direct smoke test for any OAuth-backed provider;
- no secrets committed to git.

## Bootstrap your brain shape

1. Read `memory-policy.md` before saving durable memory.
2. Copy `user-profile.example.json` and replace every example value with your own.
3. Copy `project-profile.example.json` per project; keep project facts out of global memory.
4. Use `ssot.template.md` and `evidence-ledger.template.json` for any long-running project.
5. Keep `authority-matrix.template.yaml` as the default side-effect gate.

## Verify local capabilities

```bash
hermes tools
hermes dashboard --no-open --skip-build --port 9119
```

Open:

```text
http://127.0.0.1:9119
```

Check Control Tower / dashboard health if enabled.

## Optional capabilities

- Messaging gateway: configure one platform at a time, then smoke-test.
- Browser tools: verify browser launch and page snapshot.
- Computer-use: install/verify the platform driver only on machines where background desktop control is intended.
- Cron: start with `deliver=local` before external delivery.
- Taskpool: start with proposed/review-only tasks before enabling lifecycle mutation.

## Safety gates

Before external effects:

- exact target is known;
- payload summary/digest is known;
- rollback/escape hatch is known;
- user approved that side effect;
- receipt/readback method is known.

## Not part of install

- personal memory;
- session history;
- project private facts;
- local brain vault;
- taskpool evidence;
- API keys or OAuth tokens.
