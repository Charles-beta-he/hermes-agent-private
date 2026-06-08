# Hermes session-risk-audit SSOT (2026-06-08)

Task: `2026-06-08-为-hermes-会话-cron-tool-运行记录设计确定性风险审计-gate`

## Scope

This is a low-risk, deterministic audit gate for already captured Hermes
session, cron, and tool-call run records. It does **not** mutate cron schedules,
provider routing, credentials, taskpool proof gates, or runtime prompts.

## JSON schema

Top-level classifier output:

```json
{
  "schema": "session_risk_audit_v1",
  "run_id": "string",
  "source": "session|cron|tool|unknown",
  "max_severity": "low|medium|high",
  "insufficient_evidence": true,
  "findings": [
    {
      "risk": "config_change|filesystem_write|credential_touch|external_network|mcp_or_skill_use|provider_cost_signal|insufficient_evidence",
      "severity": "medium|high",
      "evidence_bundle": ["artifact:/absolute/or/local/path", "command:<readback command>"],
      "insufficient_evidence": true,
      "reason": "matched:<deterministic tokens>"
    }
  ],
  "policy": {
    "deterministic_only": true,
    "llm_may_explain_but_not_classify": true,
    "fail_closed_on_missing_evidence": true
  }
}
```

## Deterministic classification rules

The implementation lives in `agent/session_risk_audit.py` and uses a fixed token
map:

- `config_change`: `config.yaml`, `jobs.json`, fallback provider/provider routing,
  `model-router`, or config-save signals.
- `filesystem_write`: write/edit/status-mutating operations such as `write_file`,
  `patch`, `git commit`, taskpool `transition`, or `dashboard --write`.
- `credential_touch`: credential surface names only (`api_key`, `token`, `secret`,
  `.env`, `auth.json`, `oauth`); the audit output must never include secret values.
- `external_network`: browser/web/http/curl/requests/httpx signals.
- `mcp_or_skill_use`: MCP, skill-management, tool-call, or delegated execution.
- `provider_cost_signal`: 429/rate-limit/quota/token/provider/model/cost signals.

## Fail-closed evidence behavior

A risk finding is evidence-sufficient only when at least one explicit local
pointer exists in `evidence_bundle`, `evidence_refs`, `log_path`, `artifact`, or
`path`. If a risk is detected without such a pointer, the finding remains present
but is marked `insufficient_evidence: true`. If there is neither evidence nor a
recognized signal, the whole record becomes a high-severity `insufficient_evidence`
finding rather than being classified as safe.

LLMs may later explain the deterministic output, but must not replace or downgrade
the risk labels.

## Verification boundary

Current tranche added:

- `agent/session_risk_audit.py` — deterministic pure classifier + CLI for local JSON records.
- `tests/agent/test_session_risk_audit.py` — fixture tests covering evidence-linked
  config/write, credential/provider, missing evidence, and three read-only samples.
- `docs/private/session-risk-audit-samples-20260608.json` — generated sample output
  from three synthetic read-only historical-shape records.

No cron schedule or provider routing file is part of this tranche.
