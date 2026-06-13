# Hermes Provider / Network Probe Ledger — Phase 23

Status: **PASS**
Probe count: **6**
Class counts: `{"reachable": 6}`

## Results
- **github** `reachable` status=200 host=github.com duration=1293ms
- **openai** `reachable` status=421 host=api.openai.com duration=1241ms
- **deepseek** `reachable` status=401 host=api.deepseek.com duration=296ms
- **xiaomi** `reachable` status=401 host=token-plan-cn.xiaomimimo.com duration=147ms
- **google_gemini** `reachable` status=404 host=generativelanguage.googleapis.com duration=895ms
- **telegram** `reachable` status=200 host=api.telegram.org duration=3155ms

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
