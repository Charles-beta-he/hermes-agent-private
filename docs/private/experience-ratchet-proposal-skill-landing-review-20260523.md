# Experience Ratchet proposal review — skill landing tranche

Generated: 2026-05-23T11:09:53Z
Task: `2026-05-23-experience-ratchet-proposal-review-skill-landing-tranche`
Scope: review `/Users/charles/.hermes/experience-ratchet/proposals/*` and apply only safe scoped skill updates. Public-consensus, memory/profile, and broad project-doc promotions remain proposal-only.

## Decision summary

| Proposal | Landing zone | Decision | Rationale / evidence |
|---|---:|---|---|
| `proposal-karpathy-skills-4bddfedcd6b1-20260523T082247Z` | skill | APPROVE scoped patch | Source evidence in `docs/private/andrej-karpathy-skills-gap-analysis.md` lines 43-53 and migration guidance lines 77-95 supports a thin coding guardrail, not a new standalone skill or architecture rewrite. |
| `proposal-hermes-upstream-ratchet-b1b0b8bc42f5-20260523T082247Z` | skill | APPROVE scoped patch | Existing no-agent wrapper and SOP evidence (`~/.hermes/scripts/upstream_ratchet_review_noagent.py`, `docs/private/upgrade-sop.md` lines 44-49) supports adding the no-op silence / actionable-error cron rule to the Hermes skill. |
| `proposal-rag-anything-ad6e571324cd-20260523T082247Z` | project_doc | DEFER | Not a skill landing proposal; keep as project-doc candidate. |
| `proposal-bytebot-f8e27c2932a5-20260523T082247Z` | project_doc | DEFER | Not a skill landing proposal; external-control boundary remains project architecture evidence, not skill mutation. |
| `proposal-agentmemory-82cf8de92265-20260523T082247Z` | project_doc | DEFER | Not a skill landing proposal; requires compatibility testing before enabling local memory integration. |
| `proposal-openclaw-wrong-source-ad275766a45a-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires human review and public-consensus write surface; outside task boundary. |
| `proposal-hermes-upstream-ratchet-3e3fa53b5dd1-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires human review and public-consensus write surface; outside task boundary. |
| `proposal-bytebot-0bb88eef12ce-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires human review and public-consensus write surface; outside task boundary. |
| `proposal-agentmemory-1b5ce49966a9-20260523T082247Z` | public_consensus | REJECT for this tranche | Requires human review and public-consensus write surface; outside task boundary. |

## Applied skill landing

1. `skills/software-development/writing-plans/SKILL.md`
   - Added a compact Karpathy-derived guardrail: plan thin verified slices; avoid speculative abstractions; keep broad architecture only when explicitly required by acceptance criteria.
2. `skills/autonomous-ai-agents/hermes-agent/SKILL.md`
   - Added a no-agent cron/watchdog rule: silent no-op success is acceptable only when read-back proves no actionable state; actionable states and errors must emit evidence and fail closed.

## Explicit non-actions

- No public-consensus atom was promoted.
- No memory/profile/core state was mutated.
- No third-party code was installed or executed.
- No claim was treated as completed solely because `py_compile`, generic `selfcheck`, or generic `proof-check` passed.

## Closeout evidence expectation

Close this task only if read-back confirms both skill patches and control-plane verification (`selfcheck`, `proof-check`, `next --reconcile`) remains healthy or reports only classified warnings.
