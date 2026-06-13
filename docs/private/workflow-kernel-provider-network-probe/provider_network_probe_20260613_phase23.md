# Hermes Provider / Network Probe Ledger — Phase 23

Status: **DEGRADED_PARTIAL**
Probe count: **6**
Class counts: `{"reachable": 5, "tls_error": 1}`

## Results
- **github** `reachable` status=200 host=github.com duration=1355ms
- **openai** `reachable` status=421 host=api.openai.com duration=4341ms
- **deepseek** `reachable` status=401 host=api.deepseek.com duration=137ms
- **xiaomi** `tls_error` status=- host=api.mimogpt.com duration=4550ms
- **google_gemini** `reachable` status=404 host=generativelanguage.googleapis.com duration=837ms
- **telegram** `reachable` status=200 host=api.telegram.org duration=3232ms

## Safe Boundaries
- no LLM generation call
- no credential read/write
- no cron job mutation
- no external message send
- no provider retry loop

## Next Actions
- if github is unreachable, use upstream ratchet no-fetch fallback
- if provider hosts are unreachable, keep provider_generation jobs quarantined until next probe
- if telegram is unreachable, keep failed notifications in governed ledger for manual retry
