# Evidence Ledger and Closure

## Rule

A completion claim is valid only when it binds to evidence with sufficient maturity for the requested risk level.

## What completion is not

- The model said it is done.
- A log line contains `success`.
- A file was written but not read back.
- A task was moved without proof.
- A dashboard turned green without ledger evidence.
- A multi-model discussion approved without claim-linked evidence.

## Closure maturity levels

| Level | Meaning | Example |
|---|---|---|
| L0 | proposal/draft | model plan, design sketch |
| L1 | local artifact exists | file written, ledger appended |
| L2 | deterministic readback/test passed | test output, parsed JSON, read_file proof |
| L3 | external receipt exists | Telegram message_id, API response, delivery outbox sent |
| L4 | independent review/certification passed | multi-model or reviewer proof tied to evidence IDs |
| L5 | runtime observed stable | repeated health/readback, no duplicate/rollback alerts |

## Terminal states

Every intent must eventually become one of:

- completed
- compensated
- skipped
- expired
- rejected
- dead_letter
- manual_required
- partial with explicit next boundary

No terminal state = ghost automation.

## Ledger principles

- Logs are clues; ledgers are evidence.
- Dashboard projections are views; they cannot silently become facts.
- Evidence can expire; dynamic facts require TTL.
- Sensitive evidence stores references/hashes, not raw secrets.
- Model-generated evidence is advisory unless grounded by observed proof.
- Historical phase documents are evidence history, not the canonical current model.
