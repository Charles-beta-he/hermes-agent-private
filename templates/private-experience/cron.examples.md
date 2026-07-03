# Cron examples

These are examples only. Do not enable external delivery until the target is exact and authorized.

## Local-only daily self-check

```bash
hermes cron create \
  --name "Daily Hermes doctor" \
  --schedule "0 9 * * *" \
  --deliver local \
  --prompt "Run hermes doctor/status checks, summarize only actionable failures, and do not send external messages."
```

## Origin-thread report

Use only when the current chat/channel should receive the result and the current
platform supports delivery:

```bash
hermes cron create \
  --name "Daily focused briefing" \
  --schedule "0 8 * * *" \
  --deliver origin \
  --prompt "Prepare a concise evidence-backed briefing from configured feeds. Cite sources and avoid unsupported claims."
```

## External delivery gate

Before any `--deliver telegram:...`, `discord:...`, or similar external target, record:

- exact target;
- expected audience;
- payload class;
- failure/rollback plan;
- opt-out/removal command;
- readback method.
